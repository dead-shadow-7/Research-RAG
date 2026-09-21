"""Questions the eval asks of the synthetic corpus.

Each answerable case names a substring that must appear in a retrieved chunk. The
substring is the *evidence*, not the answer text -- retrieval either put the passage in
front of the model or it did not, and that is all this measures.

Cases are grouped by the document type holding the answer, because the interesting
question is whether one score floor serves prose and tables equally. Several are chosen
to be hard on purpose:

* warranty questions have 150 spreadsheet rows with a `warranty_months` column competing
  against the one prose paragraph that actually answers them
* "Rotterdam" appears as a founding city, as a service desk, and as a warehouse in a
  third of the inventory rows
* the negatives include two that look exactly like answerable questions for this corpus
  (parental leave, a supplier in Japan). A floor tuned only against absurd negatives
  would pass those and still admit noise on realistic ones.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    group: str  # document type holding the answer, or "negative"
    question: str
    expect: str | None  # substring of the chunk that answers it; None = unanswerable
    note: str = ""


CASES: list[Case] = [
    # --- PDF: multi-page prose ---------------------------------------------------
    Case("pdf", "How long is the warranty on standard bearings?", "36 months",
         "competes with 150 rows carrying a warranty_months column"),
    Case("pdf", "What voids the bearing warranty?", "2400 rpm"),
    Case("pdf", "What is the daily meal allowance when travelling?", "85 euros"),
    Case("pdf", "When is technical support available?", "07:00 to 19:00"),
    Case("pdf", "In which year and city was the company founded?", "founded in 1998",
         "'Rotterdam' alone also appears in the service desk line and 50 inventory rows"),

    # --- DOCX: prose with no page structure --------------------------------------
    Case("docx", "How long do I have to return my access badge after leaving?",
         "5 working days"),
    Case("docx", "How many days a week can engineers work remotely?", "3 days per week"),
    Case("docx", "How quickly must a suspected security incident be reported?",
         "24 hours"),

    # --- Markdown / plain text: heading-structured ------------------------------
    Case("txt", "How do I restart the ingest worker?", "systemctl restart rag-worker"),
    Case("txt", "How often do provider credentials rotate?", "90 days"),
    Case("txt", "How long are database snapshots retained?", "35 days"),

    # --- XLSX: tabular rows ------------------------------------------------------
    Case("xlsx", "What is the price of SKU-0022?", "SKU-0022",
         "the answer is one row among forty in a 569-token block"),
    Case("xlsx", "Which warehouse holds SKU-0100?", "SKU-0100"),
    Case("xlsx", "What is the lead time for Kestrel Metalworks?", "Kestrel Metalworks"),
    Case("xlsx", "Which supplier has a contract ending in 2028?", "Verro Componenti"),

    # --- URL: extracted article --------------------------------------------------
    Case("url", "How many sites did the Meridian rollout reach in March?", "12 sites"),
    Case("url", "By how much did unplanned downtime fall?", "18 percent"),
    Case("url", "How long does commissioning take per site?", "four weeks"),

    # --- Negatives: returning nothing is the pass --------------------------------
    Case("negative", "What is the airspeed velocity of an unladen swallow?", None),
    Case("negative", "How do I replace the timing belt on a diesel engine?", None),
    Case("negative", "What is the company's parental leave entitlement?", None,
         "plausible HR question, deliberately absent from the policy document"),
    Case("negative", "Which supplier is based in Japan?", None,
         "structurally identical to an answerable spreadsheet lookup"),
]


def groups() -> list[str]:
    """Document-type groups in a stable reporting order, negatives last."""
    seen = [c.group for c in CASES]
    ordered = sorted({g for g in seen if g != "negative"}, key=seen.index)
    return ordered + (["negative"] if "negative" in seen else [])
