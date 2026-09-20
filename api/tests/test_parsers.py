"""Parser tests -- each source must yield Documents with usable page metadata."""

from pathlib import Path

import pytest

from app.ingestion.parsers import UnsupportedSource, detect_source_type, parse_pdf, parse_xlsx
from app.models import SourceType

FIXTURE = Path(__file__).parent / "fixtures" / "handbook.pdf"


def test_detect_source_type():
    assert detect_source_type("report.PDF") is SourceType.PDF
    assert detect_source_type("notes.docx") is SourceType.DOCX
    assert detect_source_type("data.xlsx") is SourceType.XLSX
    with pytest.raises(UnsupportedSource):
        detect_source_type("archive.zip")


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
