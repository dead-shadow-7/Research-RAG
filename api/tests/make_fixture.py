"""Generate a small multi-page PDF fixture with facts on known pages.

Used by the retrieval sanity test: we ask a question whose answer lives on a
specific page and assert that page comes back.
"""

from pathlib import Path

import pymupdf

FIXTURE = Path(__file__).parent / "fixtures" / "handbook.pdf"

PAGES = [
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


def build() -> Path:
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for heading, body in PAGES:
        page = doc.new_page()
        page.insert_text((72, 90), heading, fontsize=16)
        # Wrap the body so it renders as real paragraph text.
        page.insert_textbox(pymupdf.Rect(72, 120, 520, 400), body, fontsize=11)
    doc.save(FIXTURE)
    doc.close()
    return FIXTURE


if __name__ == "__main__":
    print(build())
