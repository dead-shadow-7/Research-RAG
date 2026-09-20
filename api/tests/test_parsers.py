"""Parser tests -- each source must yield Documents with usable page metadata."""

from pathlib import Path

import pytest

from app.ingestion.parsers import (
    UnsupportedSource,
    decode_text,
    detect_source_type,
    parse_pdf,
    parse_txt,
    parse_xlsx,
)
from app.models import SourceType

FIXTURE = Path(__file__).parent / "fixtures" / "handbook.pdf"


def test_detect_source_type():
    assert detect_source_type("report.PDF") is SourceType.PDF
    assert detect_source_type("notes.docx") is SourceType.DOCX
    assert detect_source_type("data.xlsx") is SourceType.XLSX
    assert detect_source_type("notes.txt") is SourceType.TXT
    assert detect_source_type("README.md") is SourceType.TXT
    with pytest.raises(UnsupportedSource):
        detect_source_type("archive.zip")


TEXT = "Standard bearings carry a warranty of 36 months from the date of dispatch."


@pytest.mark.parametrize(
    "encoding",
    ["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1252"],
)
def test_text_decodes_whatever_the_editor_produced(tmp_path, encoding):
    """Notepad writes UTF-16, older editors write cp1252.

    A fixed encoding would either raise or silently yield mojibake, and mojibake gets
    embedded and retrieved like anything else.
    """
    path = tmp_path / "notes.txt"
    path.write_bytes(TEXT.encode(encoding))

    docs = parse_txt(path)
    assert len(docs) == 1
    assert docs[0].page_content.strip() == TEXT


def test_non_ascii_survives_round_trip(tmp_path):
    body = "Café — 85 € per day. Température: 20°C. 北風"
    path = tmp_path / "notes.txt"
    path.write_text(body, encoding="utf-8")
    assert parse_txt(path)[0].page_content.strip() == body


def test_undecodable_bytes_degrade_rather_than_fail(tmp_path):
    """One bad byte should not cost the whole document."""
    path = tmp_path / "notes.txt"
    path.write_bytes(b"valid text \xff\xfe\x00 more valid text")
    assert "valid text" in parse_txt(path)[0].page_content


def test_empty_text_file_yields_nothing(tmp_path):
    """The pipeline turns this into a clear 'no extractable text' failure."""
    path = tmp_path / "empty.txt"
    path.write_text("   \n\n  ", encoding="utf-8")
    assert parse_txt(path) == []


def test_markdown_is_read_as_text(tmp_path):
    path = tmp_path / "guide.md"
    path.write_text("## Warranty\n\nBearings are covered for 36 months.", encoding="utf-8")
    assert "36 months" in parse_txt(path)[0].page_content


def test_decode_text_handles_bare_utf8():
    assert decode_text(TEXT.encode("utf-8")) == TEXT


@pytest.mark.skipif(not FIXTURE.exists(), reason="run tests/make_fixture.py first")
def test_pdf_pages_are_one_indexed_and_carry_text():
    docs = parse_pdf(FIXTURE)
    assert len(docs) == 4
    assert [d.metadata["page"] for d in docs] == [1, 2, 3, 4]
    assert "warranty of 36 months" in docs[2].page_content


def test_xlsx_repeats_the_header_in_every_block(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Parts"
    ws.append(["sku", "description", "price"])
    for i in range(100):
        ws.append([f"SKU-{i}", f"bearing type {i}", i * 10])
    path = tmp_path / "parts.xlsx"
    wb.save(path)

    docs = parse_xlsx(path)
    assert len(docs) > 1, "100 rows should split across multiple blocks"
    # Without the repeated header a retrieved block is unreadable out of context.
    assert all("sku | description | price" in d.page_content for d in docs)
    assert all(d.metadata["sheet"] == "Parts" for d in docs)
