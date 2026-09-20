"""Source-specific parsing. Every parser returns `list[Document]` so that cleaning,
chunking and embedding stay source-agnostic and page numbers survive to the citation UI.

We call the underlying libraries directly instead of using `langchain-community`
loaders: two of the four sources (Excel, URL) need custom handling anyway, and the
community package drags in `langchain-classic`, `aiohttp` and `requests` for what
amounts to a few lines of code here.
"""

from __future__ import annotations

from pathlib import Path

import docx2txt
import httpx
import pymupdf
import trafilatura
from langchain_core.documents import Document
from openpyxl import load_workbook

from app.models import SourceType

EXTENSION_MAP = {
    ".pdf": SourceType.PDF,
    ".docx": SourceType.DOCX,
    ".doc": SourceType.DOCX,
    ".xlsx": SourceType.XLSX,
    ".xlsm": SourceType.XLSX,
}

# Rows per Excel chunk. The header row is repeated in each so a chunk is
# self-describing once it is retrieved out of context.
XLSX_ROWS_PER_BLOCK = 40

USER_AGENT = "Mozilla/5.0 (compatible; RAG-ingest/0.1)"


class UnsupportedSource(Exception):
    pass


def detect_source_type(filename: str) -> SourceType:
    suffix = Path(filename).suffix.lower()
    if suffix not in EXTENSION_MAP:
        raise UnsupportedSource(
            f"Unsupported file type '{suffix or filename}'. "
            f"Supported: {', '.join(sorted(EXTENSION_MAP))}"
        )
    return EXTENSION_MAP[suffix]


def parse_pdf(path: Path) -> list[Document]:
    docs: list[Document] = []
    with pymupdf.open(path) as pdf:
        for i, page in enumerate(pdf, start=1):
            text = page.get_text("text")
            if text.strip():
                docs.append(Document(page_content=text, metadata={"page": i}))
    return docs


def parse_docx(path: Path) -> list[Document]:
    text = docx2txt.process(str(path)) or ""
    if not text.strip():
        return []
    # .docx has no page concept until rendered; the splitter will section it.
    return [Document(page_content=text, metadata={})]


def _row_to_line(row: tuple) -> str:
    return " | ".join("" if c is None else str(c).strip() for c in row)


def parse_xlsx(path: Path) -> list[Document]:
    docs: list[Document] = []
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet_index, ws in enumerate(wb.worksheets, start=1):
            rows = [r for r in ws.iter_rows(values_only=True) if any(c is not None for c in r)]
            if not rows:
                continue
            header = _row_to_line(rows[0])
            body = rows[1:]
            if not body:
                docs.append(
                    Document(
                        page_content=f"Sheet: {ws.title}\n{header}",
                        metadata={"page": sheet_index, "sheet": ws.title},
                    )
                )
                continue
            for start in range(0, len(body), XLSX_ROWS_PER_BLOCK):
                block = body[start : start + XLSX_ROWS_PER_BLOCK]
                lines = "\n".join(_row_to_line(r) for r in block)
                docs.append(
                    Document(
                        page_content=f"Sheet: {ws.title}\n{header}\n{lines}",
                        metadata={"page": sheet_index, "sheet": ws.title},
                    )
                )
    finally:
        wb.close()
    return docs


def parse_url(url: str) -> list[Document]:
    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        resp = client.get(url, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        html = resp.text

    text = trafilatura.extract(html, include_comments=False, include_tables=True) or ""
    if not text.strip():
        raise UnsupportedSource(f"No extractable main content found at {url}")
    return [Document(page_content=text, metadata={"url": url})]


def parse(source_type: SourceType, location: str | Path) -> list[Document]:
    match source_type:
        case SourceType.PDF:
            return parse_pdf(Path(location))
        case SourceType.DOCX:
            return parse_docx(Path(location))
        case SourceType.XLSX:
            return parse_xlsx(Path(location))
        case SourceType.URL:
            return parse_url(str(location))
    raise UnsupportedSource(f"No parser for {source_type}")
