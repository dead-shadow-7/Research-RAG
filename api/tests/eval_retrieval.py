"""Measure retrieval, per document type, against a corpus the eval owns.

    PYTHONPATH=. .venv/Scripts/python tests/eval_retrieval.py
    PYTHONPATH=. .venv/Scripts/python tests/eval_retrieval.py --no-floor
    PYTHONPATH=. .venv/Scripts/python tests/eval_retrieval.py --keep

Not a pytest test: it costs an embedding round trip for the whole corpus (~200 chunks,
roughly 25k tokens) and it is something you read rather than something that gates a
commit. It exits non-zero on a failing case so it *can* gate one later.

The corpus is built, embedded into a reserved Pinecone namespace, measured, and deleted.
It deliberately does not touch Postgres or your own documents: an eval whose result moves
when you upload a file is measuring the corpus, not the code. That is what the previous
version of this script did, which is why its precision figure read 100% one week and 56%
the next.

What the columns mean:

* **recall@k** -- did the chunk holding the answer come back at all, before filtering.
* **recall (floored)** -- did it survive `apply_floor`. A gap between these two is a
  mis-calibrated threshold, not a retrieval failure, and the fix is a different one.
* **precision** -- of the chunks handed to the model, how many carried the answer.
* **best score** -- the top chunk's similarity, answerable cases only. Compare this
  against the negatives' range: if one document type's answerable scores overlap another
  type's unanswerable scores, no single CONTEXT_MIN_SCORE can serve both.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from langchain_core.documents import Document as LCDocument

from app.config import settings
from app.ingestion.chunking import chunk_documents
from app.ingestion.cleaning import clean_documents
from app.retrieval import retrieve
from app.vectorstore import get_index, get_vector_store

sys.path.insert(0, str(Path(__file__).parent))
from eval_cases import CASES, Case, groups  # noqa: E402
from eval_corpus import build  # noqa: E402

# A literal, so it can never collide with a tenant namespace -- those are UUID strings.
EVAL_NAMESPACE = "__eval__"


@dataclass
class Result:
    case: Case
    retrieved: list[LCDocument]  # after the floor (or raw top-k with --no-floor)
    found_unfiltered: bool  # was the answer anywhere in the raw candidates
    best_score: float | None

    @property
    def hit(self) -> bool:
        if self.case.expect is None:
            return len(self.retrieved) == 0
        return any(self.case.expect in d.page_content for d in self.retrieved)

    @property
    def relevant(self) -> int:
        if self.case.expect is None:
            return 0
        return sum(self.case.expect in d.page_content for d in self.retrieved)


# --- corpus -----------------------------------------------------------------------


def index_corpus(dest: Path) -> int:
    """Parse, clean, chunk and embed every fixture into the eval namespace.

    Runs the real pipeline functions rather than a copy, so what is measured is what
    ingestion actually produces -- including the cleaning and chunking steps, which is
    where the spreadsheet path loses its headers.
    """
    store = get_vector_store()
    total = 0

    for entry in build(dest):
        chunks = chunk_documents(clean_documents(entry.docs))
        if not chunks:
            raise RuntimeError(f"{entry.title} produced no chunks")

        payload, ids = [], []
        for chunk in chunks:
            vector_id = f"{entry.doc_id}#{chunk.metadata['ordinal']}"
            ids.append(vector_id)
            payload.append(
                LCDocument(
                    page_content=chunk.page_content,
                    metadata={
                        "document_id": entry.doc_id,
                        "title": entry.title,
                        "source_type": entry.source_type.value,
                        "ordinal": chunk.metadata["ordinal"],
                        "page_from": chunk.metadata.get("page_from"),
                        "page_to": chunk.metadata.get("page_to"),
                    },
                )
            )

        size = settings.ingest_batch_size
        for start in range(0, len(payload), size):
            store.add_documents(
                payload[start : start + size],
                ids=ids[start : start + size],
                namespace=EVAL_NAMESPACE,
            )
        print(f"  {entry.source_type.value:<5} {entry.title:<40} {len(chunks):>3} chunks")
        total += len(chunks)

    return total


def purge() -> None:
    """Empty the eval namespace. A namespace that does not exist is already empty."""
    from pinecone.errors import NotFoundError

    try:
        get_index().delete(delete_all=True, namespace=EVAL_NAMESPACE)
    except NotFoundError:
        pass


# --- measurement ------------------------------------------------------------------


async def run_case(case: Case, use_floor: bool) -> Result:
    # Always fetch the unfiltered candidates too, so a miss can be attributed to the
    # floor rather than to retrieval.
    raw = await get_vector_store().asimilarity_search_with_score(
        case.question, k=settings.retrieve_k, namespace=EVAL_NAMESPACE
    )
    best = max((s for _, s in raw), default=None)
    found_unfiltered = (
        case.expect is not None and any(case.expect in d.page_content for d, _ in raw)
    )

    if use_floor:
        retrieved = await retrieve(case.question, namespace=EVAL_NAMESPACE)
    else:
        retrieved = [d for d, _ in raw[: settings.context_k]]

    return Result(case, retrieved, found_unfiltered, best)


def report(results: list[Result], use_floor: bool) -> int:
    mode = "floor" if use_floor else f"raw top-{settings.context_k}"
    print(
        f"\nmode: {mode}   retrieve_k={settings.retrieve_k} context_k={settings.context_k} "
        f"ratio={settings.context_min_ratio} min={settings.context_min_score}\n"
    )

    header = f"{'type':<9} {'cases':>5} {'recall@k':>9} {'floored':>8} {'precision':>10}  {'best score':>16}"
    print(header)
    print("-" * len(header))

    failures = 0
    for group in groups():
        rows = [r for r in results if r.case.group == group]
        if not rows:
            continue
        scores = sorted(r.best_score for r in rows if r.best_score is not None)
        span = f"{scores[0]:.3f} - {scores[-1]:.3f}" if scores else "-"

        if group == "negative":
            passed = sum(r.hit for r in rows)
            leaked = sum(len(r.retrieved) for r in rows)
            print(f"{group:<9} {len(rows):>5} {'-':>9} {f'{passed}/{len(rows)}':>8} "
                  f"{f'{leaked} leaked':>10}  {span:>16}")
        else:
            unfiltered = sum(r.found_unfiltered for r in rows)
            floored = sum(r.hit for r in rows)
            sent = sum(len(r.retrieved) for r in rows)
            relevant = sum(r.relevant for r in rows)
            precision = f"{relevant / sent * 100:.0f}%" if sent else "-"
            print(f"{group:<9} {len(rows):>5} {f'{unfiltered}/{len(rows)}':>9} "
                  f"{f'{floored}/{len(rows)}':>8} {precision:>10}  {span:>16}")
        failures += sum(not r.hit for r in rows)

    # The comparison that decides whether one global threshold can work.
    print("\nbest-chunk score by group (answerable vs unanswerable):")
    for group in groups():
        scores = [r.best_score for r in results if r.case.group == group and r.best_score]
        if scores:
            label = "unanswerable" if group == "negative" else "answerable"
            print(f"  {group:<9} {label:<13} n={len(scores):<3} "
                  f"min={min(scores):.3f}  median={statistics.median(scores):.3f}  "
                  f"max={max(scores):.3f}")

    print("\nfailing cases:")
    for r in results:
        if r.hit:
            continue
        if r.case.expect is None:
            why = f"returned {len(r.retrieved)} chunk(s) for an unanswerable question"
        elif r.found_unfiltered:
            why = f"answer was in the candidates but the floor dropped it (best={r.best_score:.3f})"
        else:
            why = f"answer never retrieved (best={r.best_score:.3f})"
        print(f"  [{r.case.group}] {r.case.question}\n      {why}")
        if r.case.note:
            print(f"      note: {r.case.note}")
    if not failures:
        print("  none")

    print(f"\n{len(results) - failures}/{len(results)} cases passed")
    return failures


async def main(use_floor: bool, keep: bool) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        print(f"building corpus into namespace {EVAL_NAMESPACE!r}")
        purge()  # a previous run that crashed before teardown would skew everything
        total = index_corpus(Path(tmp))
        print(f"  {total} chunks indexed")

    try:
        results = [await run_case(c, use_floor) for c in CASES]
        failures = report(results, use_floor)
    finally:
        if keep:
            print(f"\n--keep: namespace {EVAL_NAMESPACE!r} left in place")
        else:
            purge()
            print(f"\nnamespace {EVAL_NAMESPACE!r} purged")

    return failures


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-floor", action="store_true",
                    help="show unfiltered top-k for comparison")
    ap.add_argument("--keep", action="store_true",
                    help="leave the eval namespace in place for inspection")
    args = ap.parse_args()
    sys.exit(1 if asyncio.run(main(not args.no_floor, args.keep)) else 0)
