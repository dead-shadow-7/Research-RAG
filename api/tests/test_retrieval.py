"""Context-floor tests.

Hermetic: these exercise the filtering decision with synthetic scores, so they run
without Pinecone and without depending on whichever documents happen to be indexed.
The live measurement lives in `tests/eval_retrieval.py`.
"""

from langchain_core.documents import Document

from app.retrieval import apply_floor

RATIO = 0.75
MIN = 0.35


def scored(*values):
    return [(Document(page_content=f"chunk {i}", metadata={}), v) for i, v in enumerate(values)]


def test_noise_below_the_ratio_is_dropped():
    """The real case: one strong match, then a cliff into unrelated documents."""
    kept = apply_floor(scored(0.72, 0.48, 0.47, 0.47, 0.46, 0.45), 6, min_ratio=RATIO, min_score=MIN)
    assert len(kept) == 1
    assert kept[0].page_content == 'chunk 0'


def test_several_strong_matches_are_all_kept():
    kept = apply_floor(scored(0.80, 0.74, 0.62), 6, min_ratio=RATIO, min_score=MIN)
    assert len(kept) == 3


def test_nothing_relevant_returns_nothing():
    """Better to say the corpus lacks the answer than to answer from the best of a bad set."""
    assert apply_floor(scored(0.31, 0.29, 0.20), 6, min_ratio=RATIO, min_score=MIN) == []


def test_top_n_still_caps_the_result():
    kept = apply_floor(scored(0.9, 0.89, 0.88, 0.87, 0.86), 2, min_ratio=RATIO, min_score=MIN)
    assert len(kept) == 2


def test_results_are_ordered_best_first_and_carry_their_score():
    kept = apply_floor(scored(0.62, 0.91, 0.75), 6, min_ratio=RATIO, min_score=MIN)
    assert [d.metadata['score'] for d in kept] == [0.91, 0.75]


def test_empty_input():
    assert apply_floor([], 6, min_ratio=RATIO, min_score=MIN) == []


def test_absolute_threshold_alone_works():
    """With the ratio disabled the floor is purely absolute -- the shape a calibrated
    scorer wants, and what a reranker would need if one is ever added back."""
    kept = apply_floor(scored(0.95, 0.61, 0.49, 0.02), 6, min_ratio=0.0, min_score=0.5)
    assert [round(d.metadata['score'], 2) for d in kept] == [0.95, 0.61]
