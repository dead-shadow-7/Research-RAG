"""The synthetic corpus the retrieval eval measures against.

One document per supported source type, each holding facts at known locations, so a case
can assert "this question should retrieve the chunk containing that string". Synthetic on
purpose: the ground truth is exact, the files are reproducible byte-for-byte, and nothing
here depends on documents you happen to have uploaded.

The documents deliberately overlap. The spreadsheet has a `warranty_months` column so
that warranty questions -- whose real answer is prose in the PDF -- have 150 tabular rows
competing for them. A corpus where every document is about something different would
flatter retrieval and measure nothing.

The URL document is extracted from a committed HTML file rather than fetched, so this
covers trafilatura's extraction and everything downstream of it. The HTTP layer itself
(redirects, user agent, the short-content guard) stays covered by `parse_url`.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

import pymupdf
import trafilatura
from langchain_core.documents import Document
from openpyxl import Workbook

from app.ingestion.parsers import parse
from app.models import SourceType

FIXTURES = Path(__file__).parent / "fixtures"
ARTICLE_HTML = FIXTURES / "article.html"

WORDML = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


@dataclass(frozen=True)
class CorpusDoc:
    doc_id: str
    title: str
    source_type: SourceType
    docs: list[Document]


# --- PDF: multi-page prose with a running header on every page ------------------------

PDF_PAGES = [
    (
        "Chapter 1: Company Overview",
        "Northwind Components was founded in 1998 in Rotterdam. "
        "The company manufactures precision bearings for wind turbines. "
        "It employs roughly 400 people across three facilities.",
    ),
    (
        "Chapter 2: Expense Policy",
        "Employees may claim up to 85 euros per day for meals while travelling. "
        "Receipts are required for any single expense above 25 euros. "
        "Expense reports must be submitted within 30 days of the trip ending.",
    ),
    (
        "Chapter 3: Equipment Warranty",
        "Standard bearings carry a warranty of 36 months from the date of dispatch. "
        "The warranty is void if the bearing is operated above 2400 rpm. "
        "Warranty claims are processed by the Rotterdam service desk.",
    ),
    (
        "Chapter 4: Support Hours",
        "Technical support operates Monday to Friday, 07:00 to 19:00 CET. "
        "Emergency out-of-hours support is available to customers on the Platinum plan. "
        "The support desk aims to respond to critical incidents within two hours.",
    ),
]

# Repeated on every page so cleaning has a genuine running header to strip.
PDF_RUNNING_HEADER = "Northwind Components — Internal Handbook"


def build_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    for heading, body in PDF_PAGES:
        page = doc.new_page()
        page.insert_text((72, 50), PDF_RUNNING_HEADER, fontsize=8)
        page.insert_text((72, 90), heading, fontsize=16)
        page.insert_textbox(pymupdf.Rect(72, 120, 520, 400), body, fontsize=11)
    doc.save(path)
    doc.close()
    return path


# --- DOCX: prose with no page concept -------------------------------------------------

DOCX_PARAGRAPHS = [
    "Information Security Policy",
    "Access badges must be returned to reception within 5 working days of departure. "
    "A badge that is not returned is deactivated and reported as lost.",
    "Remote work is capped at 3 days per week for staff in engineering roles. "
    "Managers may approve a temporary exception for up to one month.",
    "Suspected security incidents must be reported to the security desk within 24 hours "
    "of discovery. Reports are acknowledged the same working day.",
    "Company laptops are encrypted at rest and are re-imaged every 24 months.",
]


def build_docx(path: Path) -> Path:
    """Write a minimal .docx directly.

    A .docx is a zip of XML and `docx2txt` reads only `word/document.xml`, so three parts
    are enough. This avoids adding `python-docx` as a dependency purely to produce a test
    fixture, and keeps the fixture reproducible rather than a committed binary.
    """
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{escape(p)}</w:t></w:r></w:p>'
        for p in DOCX_PARAGRAPHS
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{WORDML}"><w:body>{body}</w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return path


# --- Markdown: exercises the splitter's "\n## " separator -----------------------------

MARKDOWN = """# Operations Runbook

## Restarting the ingest worker

The worker holds no state between jobs, so it is safe to restart at any time.
Run `systemctl restart rag-worker` on the API host. Documents that were mid-flight
are marked failed on the next start and must be uploaded again.

## Rotating credentials

Provider credentials rotate every 90 days. Update the value in the secrets store first,
then restart the API and the worker together so the two never disagree about which key
is current.

## Backup retention

Database snapshots are retained for 35 days. Vector data is not snapshotted because it is
rebuildable from the chunk table; the re-index script is the recovery path.

## On-call escalation

Page the secondary on-call if an incident is not acknowledged within 15 minutes.
"""


def build_markdown(path: Path) -> Path:
    path.write_text(MARKDOWN, encoding="utf-8")
    return path


# --- XLSX: tabular rows, several blocks, two sheets ------------------------------------

WAREHOUSES = ["Rotterdam", "Gdansk", "Bilbao"]
INVENTORY_ROWS = 150


def build_xlsx(path: Path) -> Path:
    wb = Workbook()

    inventory = wb.active
    inventory.title = "Inventory"
    # `warranty_months` is deliberate: it makes every row a plausible-looking match for
    # warranty questions whose real answer is prose in the PDF.
    inventory.append(["sku", "description", "warehouse", "warranty_months", "price_eur"])
    for i in range(INVENTORY_ROWS):
        inventory.append(
            [
                f"SKU-{i:04d}",
                f"precision bearing type {i}, sealed",
                WAREHOUSES[i % len(WAREHOUSES)],
                12 + (i % 4) * 6,
                100 + i * 3,
            ]
        )

    suppliers = wb.create_sheet("Suppliers")
    suppliers.append(["supplier", "country", "lead_time_days", "contract_ends"])
    for name, country, lead, ends in [
        ("Kestrel Metalworks", "Portugal", 45, "2027-03-31"),
        ("Halden Precision", "Norway", 21, "2026-12-31"),
        ("Verro Componenti", "Italy", 30, "2028-06-30"),
        ("Aldergate Alloys", "United Kingdom", 60, "2027-09-30"),
    ]:
        suppliers.append([name, country, lead, ends])

    wb.save(path)
    wb.close()
    return path


# --- URL: trafilatura extraction from a committed page --------------------------------

ARTICLE = """<!doctype html>
<html><head><title>Meridian rollout reaches twelve sites</title></head>
<body>
<nav><a href="/">Home</a> <a href="/news">News</a> <a href="/about">About</a></nav>
<header><h1>Meridian rollout reaches twelve sites</h1></header>
<article>
<p>The Meridian condition-monitoring programme reached 12 sites in March, two ahead of
the schedule agreed at the start of the year. Each site runs a gateway that samples
vibration data once per second and forwards hourly summaries.</p>
<p>Commissioning takes about four weeks per site, of which the largest part is cabling.
The programme team expects the remaining eight sites to be live before the end of the
financial year.</p>
<p>Unplanned downtime across the commissioned sites fell by 18 percent compared with the
same period last year. The team attributes most of that to earlier detection of bearing
wear rather than to any change in maintenance scheduling.</p>
</article>
<footer><p>Copyright 2026 Northwind Components. All rights reserved.</p>
<p>Subscribe to our newsletter for weekly updates.</p></footer>
</body></html>
"""


def build_article_html(path: Path) -> Path:
    path.write_text(ARTICLE, encoding="utf-8")
    return path


def extract_url_document(html_path: Path, url: str) -> list[Document]:
    text = (
        trafilatura.extract(
            html_path.read_text(encoding="utf-8"), include_comments=False, include_tables=True
        )
        or ""
    ).strip()
    if not text:
        raise RuntimeError("trafilatura extracted nothing from the article fixture")
    return [Document(page_content=text, metadata={"url": url})]


# --- assembly -------------------------------------------------------------------------


def build(dest: Path) -> list[CorpusDoc]:
    """Write every fixture into `dest` and parse it. Returns one entry per document."""
    dest.mkdir(parents=True, exist_ok=True)

    pdf = build_pdf(dest / "handbook.pdf")
    docx = build_docx(dest / "policy.docx")
    md = build_markdown(dest / "runbook.md")
    xlsx = build_xlsx(dest / "parts.xlsx")
    html = build_article_html(dest / "article.html")

    return [
        CorpusDoc("handbook", "handbook.pdf", SourceType.PDF, parse(SourceType.PDF, pdf)),
        CorpusDoc("policy", "policy.docx", SourceType.DOCX, parse(SourceType.DOCX, docx)),
        CorpusDoc("runbook", "runbook.md", SourceType.TXT, parse(SourceType.TXT, md)),
        CorpusDoc("parts", "parts.xlsx", SourceType.XLSX, parse(SourceType.XLSX, xlsx)),
        CorpusDoc(
            "article",
            "Meridian rollout reaches twelve sites",
            SourceType.URL,
            extract_url_document(html, "https://example.invalid/meridian-rollout"),
        ),
    ]


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        for doc in build(Path(tmp)):
            chars = sum(len(d.page_content) for d in doc.docs)
            print(f"{doc.source_type.value:<5} {doc.title:<40} {len(doc.docs):>3} blocks  {chars:>6} chars")
