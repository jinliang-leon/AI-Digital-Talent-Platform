from io import BytesIO
import zipfile

import pytest
from docx import Document
from reportlab.pdfgen import canvas

from app.errors import AppError
from app.services.documents import extract_document, split_chunks, validate_upload


def docx_bytes(text):
    doc = Document()
    doc.add_paragraph(text)
    stream = BytesIO()
    doc.save(stream)
    return stream.getvalue()


def pdf_bytes():
    stream = BytesIO()
    pdf = canvas.Canvas(stream)
    pdf.drawString(60, 700, "Python developer experience")
    pdf.showPage()
    pdf.drawString(60, 700, "FastAPI project evidence")
    pdf.save()
    return stream.getvalue()


def test_pdf_pages_and_docx_paragraphs_keep_source_positions():
    pages = extract_document(pdf_bytes(), "resume.pdf")
    assert [p.page_no for p in pages] == [1, 2]
    assert "Python" in pages[0].text
    paragraphs = extract_document(docx_bytes("技能：Python 和 FastAPI"), "简历.docx")
    assert paragraphs[0].paragraph_no == 1 and paragraphs[0].page_no is None
    assert "FastAPI" in paragraphs[0].text
    chunks = split_chunks(pages, max_chars=15, overlap=3)
    assert all(c.char_end > c.char_start for c in chunks)
    assert {c.page_no for c in chunks} == {1, 2}


@pytest.mark.parametrize(
    "data,name", [(b"fake PDF", "a.pdf"), (b"%PDF-1.4\nfake", "a.docx"), (b"hello", "a.exe")]
)
def test_mismatched_or_unsupported_file_is_rejected(data, name):
    with pytest.raises(AppError):
        validate_upload(data, name, 10 * 1024 * 1024)


def test_large_upload_rejected_before_parsing():
    with pytest.raises(AppError) as exc:
        validate_upload(b"%PDF-" + b"x" * 100, "a.pdf", 50)
    assert exc.value.status == 413


def test_blank_pdf_is_explicitly_not_supported_for_ocr():
    stream = BytesIO()
    pdf = canvas.Canvas(stream)
    pdf.showPage()
    pdf.save()
    with pytest.raises(AppError) as exc:
        extract_document(stream.getvalue(), "扫描件.pdf")
    assert exc.value.code == "OCR_NOT_SUPPORTED"


def test_docx_expansion_limit_prevents_zip_bomb():
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "x")
        z.writestr("word/document.xml", "a" * (2 * 1024 * 1024))
    with pytest.raises(AppError) as exc:
        extract_document(stream.getvalue(), "压缩炸弹.docx")
    assert exc.value.code == "UNSAFE_ARCHIVE"
