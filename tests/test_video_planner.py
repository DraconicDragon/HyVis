"""
test_video_planner.py — Deterministic tests for video/animation frame sampling math.
"""

from __future__ import annotations

import pytest

from hyvis.video.planner import FramePlanConfig, plan_frames


def test_static_single_frame() -> None:
    plan = plan_frames(duration_s=0.1, total_frames=1)
    assert plan.mode == "static"
    assert plan.target_count == 1
    assert plan.indices == (0,)
    assert plan.times_s == (0.0,)


def test_fps_independence_on_rich_videos() -> None:
    """A 60s video at 24fps and 60fps must yield the exact same frame count and timestamps."""
    plan_24 = plan_frames(duration_s=60.0, total_frames=60 * 24)
    plan_60 = plan_frames(duration_s=60.0, total_frames=60 * 60)

    assert plan_24.mode == "rich"
    assert plan_60.mode == "rich"
    assert plan_24.target_count == plan_60.target_count == 15
    assert plan_24.times_s == plan_60.times_s


def test_sparse_animations_and_tiny_gifs() -> None:
    # 2-frame gif -> captures both frames
    plan_2 = plan_frames(duration_s=0.5, total_frames=2)
    assert plan_2.mode == "sparse"
    assert plan_2.target_count == 2
    assert plan_2.indices == (0, 1)

    # 16-frame gif -> sparse_k=1.5 * 4 = 6 frames, strictly increasing and unique
    plan_16 = plan_frames(duration_s=1.6, total_frames=16)
    assert plan_16.mode == "sparse"
    assert plan_16.target_count == 6
    assert len(set(plan_16.indices)) == 6
    assert list(plan_16.indices) == sorted(plan_16.indices)
    assert plan_16.indices[0] >= 0 and plan_16.indices[-1] < 16


def test_slow_sparse_slideshow_respects_duration() -> None:
    """A 10-frame slideshow lasting 60 seconds should still get time-based coverage (10 frames)."""
    plan = plan_frames(duration_s=60.0, total_frames=10)
    assert plan.mode == "sparse"
    assert plan.target_count == 10
    assert plan.indices == tuple(range(10))


def test_max_frames_hard_ceiling() -> None:
    cfg = FramePlanConfig(density_k=2.0, min_frames=4, max_frames=64)
    # 2h video (7200s) -> sqrt(7200)*2 ≈ 170, capped at 64
    plan = plan_frames(duration_s=7200.0, total_frames=7200 * 24, config=cfg)
    assert plan.mode == "rich"
    assert plan.target_count == 64
    assert len(plan.times_s) == 64
    assert 0.0 < plan.times_s[0] < plan.times_s[-1] < 7200.0


def test_max_frames_beats_min_frames_when_smaller() -> None:
    cfg = FramePlanConfig(min_frames=8, max_frames=2)
    plan = plan_frames(duration_s=10.0, total_frames=300, config=cfg)
    assert plan.target_count == 2


def test_missing_metadata_fallbacks() -> None:
    # Missing total_frames (common in MKV/WebM headers)
    plan_no_count = plan_frames(duration_s=30.0, total_frames=None)
    assert plan_no_count.mode == "rich"
    assert plan_no_count.target_count == 11
    assert len(plan_no_count.times_s) == 11
    assert plan_no_count.indices == ()

    # Missing duration (broken GIF delays)
    plan_no_dur = plan_frames(duration_s=0.0, total_frames=25)
    assert plan_no_dur.mode == "sparse"
    assert plan_no_dur.target_count == 8
    assert plan_no_dur.times_s == ()
    assert len(plan_no_dur.indices) == 8

    # Both missing/corrupt -> safe 1-frame fallback
    plan_empty = plan_frames(duration_s=None, total_frames=None)
    assert plan_empty.mode == "fallback"
    assert plan_empty.target_count == 1
    assert plan_empty.times_s == (0.0,)
    assert plan_empty.indices == (0,)


def test_invalid_config_raises() -> None:
    with pytest.raises(ValueError):
        FramePlanConfig(density_k=0.0)
    with pytest.raises(ValueError):
        FramePlanConfig(min_frames=0)
