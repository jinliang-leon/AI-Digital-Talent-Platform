"""Extract inert, locatable text from supported resume formats."""
from dataclasses import dataclass
from io import BytesIO
from math import isfinite, sqrt
from pathlib import PurePath
import re
import subprocess
from threading import Lock
from typing import Callable
from zipfile import BadZipFile, ZipFile

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 30
MAX_PIXELS = 12_000_000
MAX_TEXT = 100_000
MAX_PARTS = 5_000
PDF_LOCK = Lock()
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocumentError(ValueError):
    """A stable diagnostic safe to include in task status."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class TextBlock:
    id: str
    text: str
    location: str
    method: str


OCR = Callable[[bytes], str]


def clean(text: str) -> str:
    # Offsets in evidence refer to this canonical string, never the encoded file.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")).strip()


def tesseract(
    png: bytes, *, executable: str = "tesseract", languages: str = "chi_sim+eng", timeout: float = 25
) -> str:
    try:
        process = subprocess.run(
            [executable, "stdin", "stdout", "-l", languages],
            input=png, capture_output=True, timeout=timeout, check=False,
        )
    except FileNotFoundError as exc:
        raise DocumentError("OCR_UNAVAILABLE") from exc
    except subprocess.TimeoutExpired as exc:
        raise DocumentError("OCR_TIMEOUT") from exc
    except OSError as exc:
        raise DocumentError("OCR_UNAVAILABLE") from exc
    if process.returncode:
        raise DocumentError("OCR_FAILED")
    try:
        return process.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentError("OCR_OUTPUT_INVALID") from exc


def _image_png(raw: bytes, expected: str) -> bytes:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(BytesIO(raw), formats=[expected]) as image:
            if image.width * image.height > MAX_PIXELS or getattr(image, "n_frames", 1) != 1:
                raise DocumentError("IMAGE_LIMIT")
            output = BytesIO()
            image.convert("RGB").save(output, format="PNG")
            return output.getvalue()
    except Image.DecompressionBombError as exc:
        raise DocumentError("IMAGE_LIMIT") from exc
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise DocumentError("INVALID_IMAGE") from exc


def _docx(raw: bytes) -> list[TextBlock]:
    from defusedxml import ElementTree
    from defusedxml.common import DefusedXmlException
    from xml.etree.ElementTree import ParseError

    try:
        with ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > 5000 or sum(member.file_size for member in entries) > 80 * 1024 * 1024:
                raise DocumentError("DOCX_LIMIT")
            if any(member.flag_bits & 1 for member in entries):
                raise DocumentError("INVALID_DOCX")
            names = archive.namelist()
            if names.count("[Content_Types].xml") != 1 or names.count("word/document.xml") != 1:
                raise DocumentError("INVALID_DOCX")
            document = archive.getinfo("word/document.xml")
            if document.file_size > 4 * 1024 * 1024:
                raise DocumentError("DOCX_LIMIT")
            xml = archive.read(document)
        root = ElementTree.fromstring(xml)
    except (BadZipFile, DefusedXmlException, ParseError, RuntimeError, NotImplementedError) as exc:
        raise DocumentError("INVALID_DOCX") from exc
    if root.tag != WORD + "document":
        raise DocumentError("INVALID_DOCX")
    body = root.find(WORD + "body")
    if body is None:
        raise DocumentError("INVALID_DOCX")
    result = []
    for number, paragraph in enumerate(body.iter(WORD + "p"), 1):
        if number > MAX_PARTS:
            raise DocumentError("DOCX_LIMIT")
        text = clean("".join(
            node.text or "" if node.tag == WORD + "t" else "\t" if node.tag == WORD + "tab" else "\n"
            for node in paragraph.iter()
            if node.tag in {WORD + "t", WORD + "tab", WORD + "br", WORD + "cr"}
        ))
        if text:
            result.append(TextBlock(f"docx-{number}", text, f"paragraph:{number}", "docx"))
    return result


def _pdf(raw: bytes, ocr: OCR) -> list[TextBlock]:
    import pypdfium2 as pdfium

    if not raw.startswith(b"%PDF-"):
        raise DocumentError("INVALID_PDF")
    result = []
    # PDFium calls must be serialized within the process.
    with PDF_LOCK:
        try:
            document = pdfium.PdfDocument(raw)
            try:
                if len(document) > MAX_PAGES:
                    raise DocumentError("PDF_PAGE_LIMIT")
                length = 0
                for index in range(len(document)):
                    page = document[index]
                    try:
                        text_page = page.get_textpage()
                        try:
                            text = clean(text_page.get_text_bounded())
                        finally:
                            text_page.close()
                        method = "pdf"
                        if not text:
                            width, height = page.get_size()
                            if not all(isfinite(n) and n > 0 for n in (width, height)):
                                raise DocumentError("INVALID_PDF")
                            scale = min(2.5, 6000 / max(width, height), sqrt(0.99 * MAX_PIXELS / (width * height)))
                            bitmap = page.render(scale=scale, may_draw_forms=False)
                            try:
                                buffer = BytesIO()
                                bitmap.to_pil().save(buffer, format="PNG")
                                ocr_text = clean(ocr(buffer.getvalue()))
                                if ocr_text:
                                    text = ocr_text
                                    method = "ocr"
                            finally:
                                bitmap.close()
                        length += len(text)
                        if length > MAX_TEXT:
                            raise DocumentError("DOCUMENT_TEXT_LIMIT")
                    finally:
                        page.close()
                    if text:
                        number = index + 1
                        result.append(TextBlock(f"pdf-{number}", text, f"page:{number}", method))
            finally:
                document.close()
        except pdfium.PdfiumError as exc:
            raise DocumentError("INVALID_PDF") from exc
    return result


def extract_document(filename: str, raw: bytes, ocr: OCR = tesseract) -> tuple[TextBlock, ...]:
    if not raw:
        raise DocumentError("EMPTY_DOCUMENT")
    if len(raw) > MAX_BYTES:
        raise DocumentError("DOCUMENT_SIZE_LIMIT")
    suffix = PurePath(filename).suffix.lower()
    try:
        if suffix == ".txt":
            try:
                lines = raw.decode("utf-8-sig").splitlines()
            except UnicodeDecodeError as exc:
                raise DocumentError("INVALID_TEXT_ENCODING") from exc
            if len(lines) > MAX_PARTS:
                raise DocumentError("DOCUMENT_TEXT_LIMIT")
            blocks = [
                TextBlock(f"txt-{number}", text, f"line:{number}", "text")
                for number, line in enumerate(lines, 1) if (text := clean(line))
            ]
        elif suffix == ".docx":
            blocks = _docx(raw)
        elif suffix == ".pdf":
            blocks = _pdf(raw, ocr)
        elif suffix in {".png", ".jpg", ".jpeg"}:
            png = _image_png(raw, "PNG" if suffix == ".png" else "JPEG")
            blocks = [TextBlock("image-1", clean(ocr(png)), "page:1", "ocr")]
        else:
            raise DocumentError("UNSUPPORTED_DOCUMENT")
    except ImportError as exc:
        raise DocumentError("PARSER_DEPENDENCY_MISSING") from exc
    blocks = [block for block in blocks if block.text]
    if not blocks:
        raise DocumentError("NO_TEXT_FOUND")
    if sum(len(block.text) for block in blocks) > MAX_TEXT:
        raise DocumentError("DOCUMENT_TEXT_LIMIT")
    return tuple(blocks)
