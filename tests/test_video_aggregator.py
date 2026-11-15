"""
test_video_aggregator.py — Tests for per-tag max-score aggregation across frames.
"""

from __future__ import annotations

import numpy as np
import pytest
from vibe.results import TagEntry, TagResult

from hyvis.video.aggregator import FrameMaxAggregator, merge_frame_results


def test_single_frame_passthrough() -> None:
    single = TagResult.from_arrays(
        tag_names=("1girl", "flower"),
        scores=np.array([0.85, 0.12], dtype=np.float32),
        category_indices={"general": (0, 1)},
    )
    merged = merge_frame_results([single])
    assert merged is single


def test_array_fast_path_takes_elementwise_max() -> None:
    vocab = ("general_rating", "1girl", "flower", "sword")
    cat_idx = {"rating": (0,), "general": (1, 2, 3)}

    f1 = TagResult.from_arrays(
        tag_names=vocab,
        scores=np.array([0.90, 0.80, 0.01, 0.15], dtype=np.float32),
        category_indices=cat_idx,
    )
    f2 = TagResult.from_arrays(
        tag_names=vocab,
        scores=np.array([0.40, 0.20, 0.72, 0.05], dtype=np.float32),
        category_indices=cat_idx,
    )
    f3 = TagResult.from_arrays(
        tag_names=vocab,
        scores=np.array([0.50, 0.88, 0.10, 0.30], dtype=np.float32),
        category_indices=cat_idx,
    )

    merged = merge_frame_results([f1, f2, f3])
    # Stays unmaterialized/array-backed until filtered
    assert merged._scores is not None
    scores = merged.as_score_dict()

    assert scores["general_rating"] == pytest.approx(0.90, abs=1e-5)
    assert scores["1girl"] == pytest.approx(0.88, abs=1e-5)
    assert scores["flower"] == pytest.approx(0.72, abs=1e-5)  # 1-frame cameo preserved!
    assert scores["sword"] == pytest.approx(0.30, abs=1e-5)


def test_materialized_dict_fallback() -> None:
    f1 = TagResult(
        categories={
            "general": [TagEntry("1girl", 0.70), TagEntry("cat_ears", 0.20)],
            "character": [TagEntry("hakurei_reimu", 0.91)],
        }
    )
    f2 = TagResult(
        categories={
            "general": [TagEntry("1girl", 0.65), TagEntry("flower", 0.62)],
        }
    )

    merged = merge_frame_results([f1, f2])
    scores = merged.as_score_dict()

    assert scores["1girl"] == pytest.approx(0.70)
    assert scores["cat_ears"] == pytest.approx(0.20)
    assert scores["flower"] == pytest.approx(0.62)
    assert scores["hakurei_reimu"] == pytest.approx(0.91)


def test_mixed_array_and_materialized_promotes_cleanly() -> None:
    vocab = ("1girl", "blue_sky")
    f1 = TagResult.from_arrays(
        tag_names=vocab,
        scores=np.array([0.50, 0.40], dtype=np.float32),
        category_indices={"general": (0, 1)},
    )
    f2 = TagResult.from_arrays(
        tag_names=vocab,
        scores=np.array([0.80, 0.10], dtype=np.float32),
        category_indices={"general": (0, 1)},
    )
    f3 = TagResult(categories={"general": [TagEntry("1girl", 0.30), TagEntry("sunset", 0.75)]})

    agg = FrameMaxAggregator()
    agg.update(f1)
    agg.update(f2)
    agg.update(f3)
    assert agg.frame_count == 3

    merged = agg.finalize()
    scores = merged.as_score_dict()
    assert scores["1girl"] == pytest.approx(0.80)
    assert scores["blue_sky"] == pytest.approx(0.40)
    assert scores["sunset"] == pytest.approx(0.75)


def test_empty_aggregator_raises() -> None:
    agg = FrameMaxAggregator()
    with pytest.raises(ValueError, match="0 frame results"):
        agg.finalize()
