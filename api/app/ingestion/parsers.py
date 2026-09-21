"""Source-specific parsing. Every parser returns `list[Document]` so that cleaning,
chunking and embedding stay source-agnostic and page numbers survive to the citation UI.

We call the underlying libraries directly instead of using `langchain-community`
loaders: two of the four sources (Excel, URL) need custom handling anyway, and the
community package drags in `langchain-classic`, `aiohttp` and `requests` for what
amounts to a few lines of code here.
"""

from __future__ import annotations

import codecs
import ipaddress
import socket
from pathlib import Path
from statistics import median
from urllib.parse import urlparse

import docx2txt
import httpx
import pymupdf
import trafilatura
from langchain_core.documents import Document
from openpyxl import load_workbook

from app.config import settings
from app.models import SourceType

EXTENSION_MAP = {
    ".pdf": SourceType.PDF,
    ".docx": SourceType.DOCX,
    ".doc": SourceType.DOCX,
    ".xlsx": SourceType.XLSX,
    ".xlsm": SourceType.XLSX,
    ".txt": SourceType.TXT,
    # Markdown is plain text, and the splitter already breaks on "\n## " headings,
    # so it needs no separate parser.
    ".md": SourceType.TXT,
    ".markdown": SourceType.TXT,
}

# Upper bound on rows per Excel block. The real size is computed per sheet from the
# token budget (see `_rows_per_block`), because a fixed row count is a bet on how wide
# the table is, and losing that bet is silent.
XLSX_MAX_ROWS_PER_BLOCK = 200

# Leaves room for the "Sheet: <name>" line and the splitter's own overhead, so a block
# lands inside the budget rather than exactly on it.
XLSX_BLOCK_MARGIN_TOKENS = 32

USER_AGENT = "Mozilla/5.0 (compatible; RAG-ingest/0.1)"

# The server makes this request, so the scheme and destination are a security
# boundary, not a convenience. See assert_fetchable.
ALLOWED_URL_SCHEMES = {"http", "https"}
MAX_URL_REDIRECTS = 4
MAX_URL_BYTES = 8 * 1024 * 1024

# Trafilatura returns whatever it can find, so a nav-only page yields something like
# "Home". Anything this short is not a document, and indexing it just adds noise.
MIN_URL_CONTENT_CHARS = 200


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


_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _looks_utf16(raw: bytes) -> bool:
    """BOM-less UTF-16 gives itself away by interleaved NUL bytes."""
    sample = raw[:4096]
    return bool(sample) and sample.count(0) > len(sample) // 5


def _decode_utf16(raw: bytes) -> str | None:
    """Pick the endianness that yields text rather than CJK noise.

    Both directions decode without error for ASCII-range content -- the wrong one just
    produces garbage -- so choose by how much of the result lands in ASCII.
    """
    best, best_score = None, 0.0
    for encoding in ("utf-16-le", "utf-16-be"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if not text:
            continue
        score = sum(ord(ch) < 128 for ch in text) / len(text)
        if score > best_score:
            best, best_score = text, score
    return best if best_score > 0.5 else None


def decode_text(raw: bytes) -> str:
    """Decode a plain-text file without knowing its encoding up front.

    A `.txt` can arrive as UTF-8, as UTF-16 (what Windows Notepad calls "Unicode"),
    or as cp1252 from older editors. Reading with a fixed encoding either raises or,
    worse, silently produces mojibake that then gets embedded and retrieved.
    """
    for bom, encoding in _BOMS:
        if raw.startswith(bom):
            return raw.decode(encoding)

    # Must precede the cp1252 attempt: every byte is valid cp1252, so UTF-16 would
    # "succeed" there and yield NUL-interleaved text.
    if _looks_utf16(raw) and (text := _decode_utf16(raw)) is not None:
        return text

    for encoding in ("utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue

    # Last resort: keep what is readable rather than failing the whole document.
    return raw.decode("utf-8", errors="replace")


def parse_txt(path: Path) -> list[Document]:
    text = decode_text(path.read_bytes())
    if not text.strip():
        return []
    # Plain text has no page structure; the splitter sections it.
    return [Document(page_content=text, metadata={})]


def _row_to_line(row: tuple) -> str:
    return " | ".join("" if c is None else str(c).strip() for c in row)


def _rows_per_block(header: str, rows: list[str]) -> int:
    """How many rows fit in a block without the chunker having to split it.

    The header is repeated in every block so a retrieved block says what its columns
    are. That promise only holds if the block survives chunking intact, and at a fixed
    40 rows it did not: a typical inventory sheet produced 569-token blocks against a
    380-token CHUNK_TOKENS, so the splitter halved each one and every second chunk
    arrived as bare rows with no column names.

    Sizing by tokens gives a wide table fewer rows per block and a narrow one more,
    which is what the fixed count was only ever approximating.
    """
    from app.embeddings import count_tokens

    budget = settings.chunk_tokens - count_tokens(header) - XLSX_BLOCK_MARGIN_TOKENS
    if budget <= 0:
        return 1

    # Median, not mean: one pathological row should not shrink every block.
    sample = rows[:50] or [""]
    per_row = max(1, int(median(count_tokens(r) for r in sample)))
    return max(1, min(XLSX_MAX_ROWS_PER_BLOCK, budget // per_row))


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
                        metadata={"page": sheet_index, "sheet": ws.title, "structured": True},
                    )
                )
                continue
            body_lines = [_row_to_line(r) for r in body]
            per_block = _rows_per_block(header, body_lines)
            for start in range(0, len(body_lines), per_block):
                lines = "\n".join(body_lines[start : start + per_block])
                docs.append(
                    Document(
                        page_content=f"Sheet: {ws.title}\n{header}\n{lines}",
                        # `structured` tells cleaning to leave this block alone: the
                        # repeated header and sheet line are the point, not boilerplate.
                        metadata={
                            "page": sheet_index,
                            "sheet": ws.title,
                            "structured": True,
                        },
                    )
                )
    finally:
        wb.close()
    return docs


def assert_fetchable(url: str) -> None:
    """Refuse URLs that would make this server talk to itself or its own network.

    Ingesting a URL means the *server* makes the request, so without this any signed-up
    user can aim it at addresses only the server can reach -- the cloud metadata
    endpoint on 169.254.169.254, other services in the VPC, localhost -- and read the
    response back as a document. That is server-side request forgery, and open sign-up
    makes it available to anyone.

    Every address the hostname resolves to is checked, not just the first: a name with
    one public and one private A record would otherwise pass and then connect to
    whichever the resolver handed back.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_URL_SCHEMES:
        raise UnsupportedSource(
            f"Only http and https URLs can be ingested, not '{parsed.scheme or url}'."
        )
    host = parsed.hostname
    if not host:
        raise UnsupportedSource(f"No hostname in '{url}'.")

    try:
        resolved = socket.getaddrinfo(host, parsed.port or 0, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsupportedSource(f"Could not resolve '{host}'.") from exc

    for info in resolved:
        address = ipaddress.ip_address(info[4][0])
        # An IPv4-mapped IPv6 address (::ffff:169.254.169.254) has to be unwrapped
        # before the private/loopback checks mean anything.
        mapped = getattr(address, "ipv4_mapped", None)
        if mapped is not None:
            address = mapped
        if not address.is_global or address.is_private:
            raise UnsupportedSource(
                f"'{host}' resolves to a private or reserved address. "
                "Only publicly reachable pages can be ingested."
            )


def _fetch(url: str) -> str:
    """Fetch a page, validating every hop rather than trusting the redirect chain.

    `follow_redirects=True` would hand the whole chain to httpx, and a public URL is
    free to redirect to a private one -- so the check has to run again after each hop,
    which means following them here.
    """
    with httpx.Client(follow_redirects=False, timeout=30.0) as client:
        current = url
        for _ in range(MAX_URL_REDIRECTS + 1):
            assert_fetchable(current)
            with client.stream("GET", current, headers={"User-Agent": USER_AGENT}) as resp:
                if resp.is_redirect and (location := resp.headers.get("location")):
                    current = str(httpx.URL(current).join(location))
                    continue
                resp.raise_for_status()

                # Read with a ceiling: an endpoint that streams forever would otherwise
                # fill the container's memory.
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > MAX_URL_BYTES:
                        raise UnsupportedSource(
                            f"Page at {current} exceeds "
                            f"{MAX_URL_BYTES // (1024 * 1024)} MB."
                        )
                return body.decode(resp.encoding or "utf-8", errors="replace")

    raise UnsupportedSource(f"Too many redirects starting from {url}.")


def parse_url(url: str) -> list[Document]:
    html = _fetch(url)
    text = (trafilatura.extract(html, include_comments=False, include_tables=True) or "").strip()
    if len(text) < MIN_URL_CONTENT_CHARS:
        raise UnsupportedSource(
            f"No substantial article content found at {url} "
            f"(extracted {len(text)} characters). It may be a nav page, "
            "a login wall, or rendered entirely in JavaScript."
        )
    return [Document(page_content=text, metadata={"url": url})]


def parse(source_type: SourceType, location: str | Path) -> list[Document]:
    match source_type:
        case SourceType.PDF:
            return parse_pdf(Path(location))
        case SourceType.DOCX:
            return parse_docx(Path(location))
        case SourceType.XLSX:
            return parse_xlsx(Path(location))
        case SourceType.TXT:
            return parse_txt(Path(location))
        case SourceType.URL:
            return parse_url(str(location))
    raise UnsupportedSource(f"No parser for {source_type}")
