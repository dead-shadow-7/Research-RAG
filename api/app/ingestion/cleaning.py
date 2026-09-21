"""Text cleanup applied between parsing and chunking.

The goal is to stop boilerplate (running headers, page numbers, hyphenated line
wraps) from eating into the 512-token embedding window and from polluting the
text Claude ends up citing.
"""

from __future__ import annotations

import re
from collections import Counter

from langchain_core.documents import Document

MIN_BLOCK_CHARS = 20

# Fraction of pages a line must appear on to be treated as a running header/footer.
BOILERPLATE_RATIO = 0.6

_HYPHEN_WRAP = re.compile(r"(\w)-\n(\w)")
_MANY_NEWLINES = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")
_MANY_SPACES = re.compile(r"[ \t]{2,}")
_PAGE_NUMBER = re.compile(r"^\s*(?:page\s+)?\d+\s*(?:/\s*\d+)?\s*$", re.IGNORECASE)


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HYPHEN_WRAP.sub(r"\1\2", text)  # rejoin words split across lines
    text = _TRAILING_SPACE.sub("\n", text)
    text = _MANY_SPACES.sub(" ", text)
    text = _MANY_NEWLINES.sub("\n\n", text)
    return text.strip()


def _edge_lines(doc: Document) -> list[str]:
    """First and last non-empty lines -- where running headers/footers live."""
    lines = [ln.strip() for ln in doc.page_content.split("\n") if ln.strip()]
    if not lines:
        return []
    return [lines[0], lines[-1]] if len(lines) > 1 else [lines[0]]


def _is_structured(doc: Document) -> bool:
    """Blocks a parser built deliberately, where repetition carries meaning.

    Spreadsheet blocks repeat their sheet name and column header on purpose, so that a
    block retrieved on its own says what its columns are. Boilerplate detection sees
    exactly the signal it is built to remove -- the same line at the edge of most
    blocks -- and strips both. Measured on a two-sheet workbook it deleted the sheet
    name from every block.
    """
    return bool(doc.metadata.get("structured"))


def _find_boilerplate(docs: list[Document]) -> set[str]:
    candidates = [d for d in docs if not _is_structured(d)]
    if len(candidates) < 3:
        return set()
    counts: Counter[str] = Counter()
    for doc in candidates:
        counts.update(set(_edge_lines(doc)))
    threshold = max(2, int(len(candidates) * BOILERPLATE_RATIO))
    return {line for line, n in counts.items() if n >= threshold}


def clean_documents(docs: list[Document]) -> list[Document]:
    """Clean each block, strip repeated headers/footers, drop near-empty blocks."""
    boilerplate = _find_boilerplate(docs)

    cleaned: list[Document] = []
    for doc in docs:
        if _is_structured(doc):
            # Whitespace normalisation only. Dropping a line here means dropping a row,
            # and a page-number pattern is indistinguishable from a numeric cell.
            text = clean_text(doc.page_content)
        else:
            lines = [
                ln
                for ln in doc.page_content.split("\n")
                if ln.strip() not in boilerplate and not _PAGE_NUMBER.match(ln)
            ]
            text = clean_text("\n".join(lines))
        if len(text) < MIN_BLOCK_CHARS:
            continue
        cleaned.append(Document(page_content=text, metadata=dict(doc.metadata)))
    return cleaned
