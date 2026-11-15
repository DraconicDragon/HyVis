"""
aggregator.py — Fast per-tag maximum-score aggregation across video/animation frames.

Reduces N per-frame TagResult predictions into a single file-level TagResult by
retaining the highest confidence score observed for every tag across all frames.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np
from vibe.results import TagEntry, TagResult


class FrameMaxAggregator:
    """
    Streaming accumulator that merges multiple frame TagResults into one TagResult
    using element-wise maximum scores per tag.

    Uses a zero-allocation NumPy fast path when inputs are array-backed (the standard
    output of vibe taggers), and seamlessly falls back to dictionary merging for
    manually constructed / materialized TagResult objects.
    """

    def __init__(self) -> None:
        self._count = 0
        self._first_result: TagResult | None = None

        # Array-backed fast path state
        self._max_scores: np.ndarray | None = None
        self._tag_names: Sequence[str] | None = None
        self._category_indices: Mapping[str, Sequence[int]] | None = None
        self._extras: dict[str, Any] = {}

        # Materialized fallback state
        self._dict_acc: dict[str, dict[str, float]] | None = None

    @property
    def frame_count(self) -> int:
        """Number of frame results merged so far."""
        return self._count

    def update(self, result: TagResult) -> None:
        """Fold one frame's TagResult into the running per-tag maximum."""
        self._count += 1

        # 1. First frame: hold reference only (avoids copies entirely if N == 1)
        if self._count == 1:
            self._first_result = result
            if result.extras:
                self._extras = dict(result.extras)
            return

        # 2. Second frame: initialize active accumulator (array fast-path vs dict fallback)
        if self._count == 2:
            assert self._first_result is not None
            first = self._first_result
            if (
                first._scores is not None
                and first._tag_names is not None
                and first._category_indices is not None
                and result._scores is not None
                and result._scores.shape == first._scores.shape
                and result._tag_names is first._tag_names
            ):
                self._max_scores = np.maximum(
                    first._scores.astype(np.float32, copy=False),
                    result._scores.astype(np.float32, copy=False),
                )
                self._tag_names = first._tag_names
                self._category_indices = first._category_indices
                self._first_result = None
                return

            # Promote first frame into materialized dict fallback
            self._init_dict_from_result(first)
            self._first_result = None

        # 3. Frame 2+ in materialized dict mode
        if self._dict_acc is not None:
            self._merge_into_dict(result)
            return

        # 4. Frame 3+ in array fast path
        if (
            self._max_scores is not None
            and result._scores is not None
            and result._scores.shape == self._max_scores.shape
            and result._tag_names is self._tag_names
        ):
            np.maximum(self._max_scores, result._scores, out=self._max_scores)
            return

        # Shape or vocabulary changed mid-stream: promote current array state to dict safely
        self._promote_array_to_dict()
        self._merge_into_dict(result)

    def finalize(self) -> TagResult:
        """Return the single merged TagResult across all observed frames."""
        if self._count == 0:
            raise ValueError("Cannot finalize FrameMaxAggregator with 0 frame results.")

        if self._count == 1:
            assert self._first_result is not None
            return self._first_result

        if self._max_scores is not None and self._tag_names is not None and self._category_indices is not None:
            return TagResult.from_arrays(
                tag_names=self._tag_names,
                scores=self._max_scores,
                category_indices=self._category_indices,
                extras=dict(self._extras),
            )

        assert self._dict_acc is not None
        categories: dict[str, list[TagEntry]] = {}
        for cat_name, tag_map in self._dict_acc.items():
            entries = [TagEntry(tag=t, score=s) for t, s in tag_map.items()]
            entries.sort(key=lambda e: e.score, reverse=True)
            if entries:
                categories[cat_name] = entries

        return TagResult(categories=categories, extras=dict(self._extras))

    # region Internal Helpers

    def _init_dict_from_result(self, result: TagResult) -> None:
        self._dict_acc = {}
        self._merge_into_dict(result)

    def _promote_array_to_dict(self) -> None:
        assert self._max_scores is not None
        assert self._tag_names is not None
        assert self._category_indices is not None

        scores = self._max_scores
        names = self._tag_names
        usable = min(len(scores), len(names))
        acc: dict[str, dict[str, float]] = {}

        for cat_name, indices in self._category_indices.items():
            cat_dict: dict[str, float] = {}
            for idx in indices:
                if idx < usable:
                    cat_dict[names[idx]] = float(scores[idx])
            if cat_dict:
                acc[cat_name] = cat_dict

        self._dict_acc = acc
        self._max_scores = None
        self._tag_names = None
        self._category_indices = None

    def _merge_into_dict(self, result: TagResult) -> None:
        assert self._dict_acc is not None
        if result._categories is None and result._scores is not None and result._tag_names is not None:
            scores = result._scores
            names = result._tag_names
            usable = min(len(scores), len(names))
            for cat_name, indices in (result._category_indices or {}).items():
                cat_dict = self._dict_acc.setdefault(cat_name, {})
                for idx in indices:
                    if idx < usable:
                        tag = names[idx]
                        val = float(scores[idx])
                        prev = cat_dict.get(tag)
                        if prev is None or val > prev:
                            cat_dict[tag] = val
            return

        for cat_name, entries in result.categories.items():
            cat_dict = self._dict_acc.setdefault(cat_name, {})
            for entry in entries:
                prev = cat_dict.get(entry.tag)
                if prev is None or entry.score > prev:
                    cat_dict[entry.tag] = float(entry.score)

    # endregion


def merge_frame_results(results: Iterable[TagResult]) -> TagResult:
    """Convenience helper to merge an iterable of per-frame TagResults into one max-score TagResult."""
    agg = FrameMaxAggregator()
    for res in results:
        agg.update(res)
    return agg.finalize()
