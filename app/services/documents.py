"""有界文件解析与原文定位，不执行简历中的脚本、宏或指令。"""

from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
from zipfile import ZipFile, BadZipFile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from app.errors import AppError


@dataclass
class TextPage:
    text: str
    page_no: int | None = None
    paragraph_no: int | None = None


@dataclass
class Chunk(TextPage):
    chunk_no: int = 0
    char_start: int = 0
    char_end: int = 0


def validate_upload(data: bytes, filename: str, max_bytes: int) -> str:
    if len(data) > max_bytes:
        raise AppError("FILE_TOO_LARGE", "文件超过10 MiB限制", 413)
    if not data:
        raise AppError("EMPTY_FILE", "文件内容为空")
    suffix = PurePath(filename.replace("\\", "/")).suffix.lower()
    if suffix == ".pdf" and data.startswith(b"%PDF-"):
        return "application/pdf"
    if suffix == ".docx" and data.startswith(b"PK\x03\x04"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    raise AppError("UNSUPPORTED_FILE", "仅支持文件签名正确的文本 PDF 和 DOCX")


def _check_archive(data):
    try:
        with ZipFile(BytesIO(data)) as z:
            infos = z.infolist()
            if len(infos) > 2000 or sum(i.file_size for i in infos) > 30 * 1024 * 1024:
                raise AppError("UNSAFE_ARCHIVE", "DOCX 解压后内容过大")
            for item in infos:
                if item.file_size > 1024 * 1024 and item.file_size / max(item.compress_size, 1) > 200:
                    raise AppError("UNSAFE_ARCHIVE", "DOCX 压缩比例异常")
                if ".." in PurePath(item.filename).parts or item.filename.startswith(("/", "\\")):
                    raise AppError("UNSAFE_ARCHIVE", "DOCX 包含非法路径")
            for name in ("[Content_Types].xml", "word/document.xml"):
                body = z.read(name)
                if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
                    raise AppError("UNSAFE_ARCHIVE", "DOCX 包含不支持的 XML 声明")
    except (BadZipFile, KeyError) as exc:
        raise AppError("INVALID_DOCUMENT", "DOCX 文件已损坏") from exc


def _paragraphs(container):
    seen = set()
    for item in container.iter_inner_content():
        if isinstance(item, Paragraph):
            yield item.text
        elif isinstance(item, Table):
            for row in item.rows:
                for cell in row.cells:
                    if cell._tc not in seen:
                        seen.add(cell._tc)
                        yield from _paragraphs(cell)


def extract_document(data: bytes, filename: str) -> list[TextPage]:
    mime = validate_upload(data, filename, 10 * 1024 * 1024)
    try:
        if mime == "application/pdf":
            reader = PdfReader(BytesIO(data), strict=False)
            if reader.is_encrypted:
                raise AppError("ENCRYPTED_PDF", "请上传未加密的 PDF")
            if len(reader.pages) > 100:
                raise AppError("TOO_MANY_PAGES", "简历不能超过100页")
            pages = [
                TextPage(text=(page.extract_text() or "").replace("\x00", ""), page_no=n + 1)
                for n, page in enumerate(reader.pages)
            ]
        else:
            _check_archive(data)
            doc = Document(BytesIO(data))
            texts = list(_paragraphs(doc))
            if len(texts) > 10000:
                raise AppError("DOCUMENT_TOO_LARGE", "简历段落过多")
            pages = [TextPage(text=t, paragraph_no=n + 1) for n, t in enumerate(texts) if t.strip()]
        if not any(p.text.strip() for p in pages):
            raise AppError("OCR_NOT_SUPPORTED", "未提取到文字，首版不支持扫描件 OCR")
        if sum(len(p.text) for p in pages) > 300_000:
            raise AppError("DOCUMENT_TOO_LARGE", "简历文字内容过多")
        return pages
    except AppError:
        raise
    except Exception as exc:
        raise AppError("INVALID_DOCUMENT", "文件解析失败，请检查是否损坏") from exc


def split_chunks(pages: list[TextPage], max_chars: int = 1000, overlap: int = 120) -> list[Chunk]:
    if not 0 <= overlap < max_chars:
        raise ValueError("overlap must be smaller than max_chars")
    result = []
    base = 0
    for page in pages:
        start = 0
        while start < len(page.text):
            end = min(start + max_chars, len(page.text))
            text = page.text[start:end]
            if text.strip():
                result.append(
                    Chunk(
                        text=text,
                        page_no=page.page_no,
                        paragraph_no=page.paragraph_no,
                        chunk_no=len(result),
                        char_start=base + start,
                        char_end=base + end,
                    )
                )
            if end == len(page.text):
                break
            start = end - overlap
        base += len(page.text) + 1
    return result
