"""Measure retrieval against the live index.

Not a pytest test: results depend on which documents are currently indexed, so this is
a script you run and read. It reports precision (how much of what reaches the model is
actually relevant) and whether the expected source was found at all.

    .venv/Scripts/python tests/eval_retrieval.py --user <supabase-user-id>
    .venv/Scripts/python tests/eval_retrieval.py --user <id> --no-floor   # raw top-k

`--user` is whose library to measure: vectors live in a per-user Pinecone namespace, so
an evaluation has to say whose corpus it is evaluating. The id is the one in
`documents.owner_id` for the documents you indexed.

Add cases as you add documents. A case with `expect=None` asserts the opposite and
equally important behaviour: questions the corpus cannot answer should return nothing,
rather than six unrelated chunks.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass

from app.auth import tenant_namespace
from app.config import settings
from app.retrieval import retrieve
from app.vectorstore import get_vector_store


@dataclass
class Case:
    question: str
    expect: str | None  # substring of the expected chunk text, or None for "no answer"


CASES = [
    Case("What voids the bearing warranty?", "2400 rpm"),
    Case("How long is the warranty on standard bearings?", "36 months"),
    Case("What is the daily meal allowance when travelling?", "85 euros"),
    Case("When is technical support available?", "07:00 to 19:00"),
    Case("Where was the company founded?", "Rotterdam"),
    # Negatives: nothing in the corpus answers these.
    Case("What is the airspeed velocity of an unladen swallow?", None),
    Case("How do I replace the timing belt on a diesel engine?", None),
]


async def raw_topk(question: str, namespace: str):
    hits = await get_vector_store().asimilarity_search_with_score(
        question, k=settings.context_k, namespace=namespace
    )
    for doc, score in hits:
        doc.metadata["score"] = score
    return [doc for doc, _ in hits]


async def main(use_floor: bool, namespace: str) -> int:
    mode = "floor" if use_floor else "raw top-k"
    print(f"mode: {mode}   context_k={settings.context_k} "
          f"ratio={settings.context_min_ratio} min={settings.context_min_score}\n")

    total_chunks = relevant_chunks = 0
    failures = 0

    for case in CASES:
        docs = await (
            retrieve(case.question, namespace=namespace)
            if use_floor
            else raw_topk(case.question, namespace)
        )
        hit = any(case.expect in d.page_content for d in docs) if case.expect else False

        if case.expect is None:
            ok = len(docs) == 0
            verdict = "PASS" if ok else f"FAIL  returned {len(docs)} chunks for an unanswerable question"
            # Every chunk returned here is by definition noise.
            total_chunks += len(docs)
        else:
            ok = hit
            verdict = "PASS" if ok else "FAIL  expected source not retrieved"
            for d in docs:
                total_chunks += 1
                if case.expect in d.page_content:
                    relevant_chunks += 1

        failures += 0 if ok else 1
        print(f"  [{verdict.split()[0]}] {case.question}")
        print(f"         chunks={len(docs):<2} {verdict[6:]}".rstrip())

    precision = (relevant_chunks / total_chunks * 100) if total_chunks else 0.0
    print(f"\n  chunks sent to the model : {total_chunks}")
    print(f"  of those, on-target      : {relevant_chunks}  ({precision:.0f}% precision)")
    print(f"  failing cases            : {failures}/{len(CASES)}")
    return failures


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True, help="owner id whose library to evaluate")
    ap.add_argument("--no-floor", action="store_true", help="show unfiltered top-k for comparison")
    args = ap.parse_args()
    namespace = tenant_namespace(uuid.UUID(args.user))
    sys.exit(1 if asyncio.run(main(not args.no_floor, namespace)) else 0)
