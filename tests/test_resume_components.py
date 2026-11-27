import json
from io import BytesIO
import shutil
import subprocess
from secrets import token_urlsafe
from zipfile import ZipFile

import httpx
import pytest

from backend.app.ai_resume.documents import DocumentError, TextBlock, extract_document
from backend.app.ai_resume.structured import ResumeExtraction, extract_professional_facts, verify_evidence
from backend.app.core.ai_client import ModelError, OpenAICompatible


def test_txt_preserves_line_locations():
    blocks = extract_document("candidate.txt", "\ufeff亚马逊运营\r\n\r\n  Listing\t优化 ".encode())
    assert [(block.id, block.location, block.text) for block in blocks] == [
        ("txt-1", "line:1", "亚马逊运营"),
        ("txt-3", "line:3", "Listing 优化"),
    ]


@pytest.mark.parametrize(("name", "data", "error"), [
    ("resume.txt", b"", "EMPTY_DOCUMENT"),
    ("resume.txt", b"\xff", "INVALID_TEXT_ENCODING"),
    ("resume.txt", b" \n ", "NO_TEXT_FOUND"),
    ("resume.html", b"<html>", "UNSUPPORTED_DOCUMENT"),
])
def test_errors_are_stable(name, data, error):
    with pytest.raises(DocumentError, match=f"^{error}$"):
        extract_document(name, data)


def test_docx_extracts_table_and_keeps_paragraph_anchors():
    pytest.importorskip("defusedxml")
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
           <w:body><w:p><w:r><w:t>亚马逊</w:t></w:r><w:r><w:t>运营</w:t></w:r></w:p>
           <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Listing</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
           </w:body></w:document>""".encode())
    blocks = extract_document("candidate.docx", buffer.getvalue())
    assert [(block.location, block.text) for block in blocks] == [
        ("paragraph:1", "亚马逊运营"), ("paragraph:2", "Listing"),
    ]


def test_image_ocr_contract_uses_normalized_image():
    image_module = pytest.importorskip("PIL.Image")
    image = BytesIO()
    image_module.new("RGB", (48, 48), color="white").save(image, format="PNG")

    def fake_ocr(normalized):
        assert normalized.startswith(b"\x89PNG\r\n\x1a\n")
        return " Amazon US  "

    assert extract_document("scan.png", image.getvalue(), fake_ocr) == (
        TextBlock("image-1", "Amazon US", "page:1", "ocr"),
    )
    with pytest.raises(DocumentError, match="INVALID_IMAGE"):
        extract_document("scan.jpg", image.getvalue(), fake_ocr)


def _one_page_pdf(page_stream):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(page_stream), page_stream),
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, content in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(b"%d 0 obj\n%s\nendobj\n" % (index, content))
    xref = len(data)
    data.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        data.extend(b"%010d 00000 n \n" % offset)
    data.extend(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref)
    return bytes(data)


def test_pdf_prefers_text_layer():
    pytest.importorskip("pypdfium2")
    data = _one_page_pdf(b"BT /F1 12 Tf 20 100 Td (Listing) Tj ET")

    def unexpected_ocr(_image):
        pytest.fail("OCR should not run for a usable text layer")

    assert extract_document("candidate.pdf", data, unexpected_ocr) == (
        TextBlock("pdf-1", "Listing", "page:1", "pdf"),
    )


def test_blank_pdf_routes_to_ocr_contract():
    pytest.importorskip("pypdfium2")
    pytest.importorskip("PIL")

    def fake_ocr(image):
        assert image.startswith(b"\x89PNG\r\n\x1a\n")
        return "FBA"

    assert extract_document("scan.pdf", _one_page_pdf(b""), fake_ocr) == (
        TextBlock("pdf-1", "FBA", "page:1", "ocr"),
    )


@pytest.mark.parametrize(("suffix", "image_format"), [("png", "PNG"), ("jpg", "JPEG"), ("pdf", "PDF")])
def test_real_ocr_on_synthetic_scan(suffix, image_format):
    executable = shutil.which("tesseract")
    if not executable:
        pytest.skip("Tesseract executable is unavailable")
    languages = subprocess.run([executable, "--list-langs"], capture_output=True, text=True, timeout=8, check=True)
    if not {"chi_sim", "eng"}.issubset(languages.stdout.splitlines()):
        pytest.skip("The default OCR language files are unavailable")
    if image_format == "PDF":
        pytest.importorskip("pypdfium2")
    image_module = pytest.importorskip("PIL.Image")
    draw_module = pytest.importorskip("PIL.ImageDraw")
    font_module = pytest.importorskip("PIL.ImageFont")
    image = image_module.new("RGB", (780, 210), "white")
    draw_module.Draw(image).text((70, 75), "Amazon Listing", fill="black", font=font_module.load_default(size=52))
    buffer = BytesIO()
    image.save(buffer, image_format)
    blocks = extract_document("synthetic." + suffix, buffer.getvalue())
    assert blocks[0].method == "ocr" and "amazon listing" in blocks[0].text.casefold()


@pytest.mark.parametrize("items", [
    [{"section": "skill", "block_id": "missing", "quote": "Listing"}],
    [{"section": "skill", "block_id": "txt-1", "quote": "Listing expert"}],
    [{"section": "skill", "block_id": "txt-1", "quote": "Listing"}],
    [{"section": "age", "block_id": "txt-1", "quote": "Listing"}],
])
def test_invented_ambiguous_or_out_of_scope_evidence_rejected(items):
    source = (TextBlock("txt-1", "Listing / Listing", "line:1", "text"),)
    with pytest.raises(DocumentError):
        verify_evidence({"items": items}, source)


def test_structured_pipeline_via_mock_http_transport():
    source = extract_document("sample.txt", (
        "邮箱：synthetic@example.invalid\n"
        "工作经历：负责 Amazon US Listing 优化。\n"
        "年龄：25"
    ).encode())
    quote = "Amazon US Listing 优化"

    def handler(request):
        body = json.loads(request.content)
        data = body["messages"][1]["content"]
        assert "synthetic@example.invalid" not in data and "年龄" not in data
        assert body["response_format"]["json_schema"]["strict"] is True
        result = {"items": [{"section": "skill", "block_id": "txt-2", "quote": quote}]}
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
            "usage": {"prompt_tokens": 18, "completion_tokens": 6},
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = OpenAICompatible(client, "https://test.example.invalid/v1", token_urlsafe(24), "test-model")
        with pytest.raises(DocumentError, match="MODEL_DATA_AUTHORIZATION_REQUIRED"):
            extract_professional_facts(source, model)
        output = extract_professional_facts(source, model, external_processing_authorized=True)
    assert output.usage.prompt_tokens == 18
    item, = output.evidence
    assert item.source == "line:2" and source[1].text[item.start:item.end] == quote


def test_model_malformed_output_has_safe_diagnostic():
    def handler(_request):
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": "not json"}}],
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = OpenAICompatible(client, "https://test.example.invalid/v1", token_urlsafe(24), "test-model")
        with pytest.raises(ModelError, match="MODEL_OUTPUT_INVALID"):
            model.structured([], ResumeExtraction)
