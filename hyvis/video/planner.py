"""
planner.py — Pure math and planning logic for sampling frames from videos and animated images.

Balances coverage across short/sparse animations and long high-FPS videos by:
  - Sampling rich/long media by time via square-root scaling: N = k * sqrt(duration_s)
  - Sampling sparse media (below `sparse_below_frames`) via frame-count scaling
  - Selecting equal-segment midpoints `(i + 0.5) * span / N` to avoid fade-in/out edge frames
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

PlanningMode = Literal["static", "sparse", "rich", "fallback"]


@dataclass(frozen=True, slots=True)
class FramePlanConfig:
    """Tuning parameters for frame sampling density and boundaries."""

    density_k: float = 2.0
    min_frames: int = 4
    max_frames: int = 128
    sparse_below_frames: int = 32
    sparse_k: float = 1.5

    def __post_init__(self) -> None:
        if self.density_k <= 0:
            raise ValueError(f"density_k must be > 0, got {self.density_k}")
        if self.sparse_k <= 0:
            raise ValueError(f"sparse_k must be > 0, got {self.sparse_k}")
        if self.min_frames < 1:
            raise ValueError(f"min_frames must be >= 1, got {self.min_frames}")
        if self.max_frames < 1:
            raise ValueError(f"max_frames must be >= 1, got {self.max_frames}")
        if self.sparse_below_frames < 1:
            raise ValueError(f"sparse_below_frames must be >= 1, got {self.sparse_below_frames}")


@dataclass(frozen=True, slots=True)
class FramePlan:
    """Resolved sampling plan for one video or animated image."""

    mode: PlanningMode
    target_count: int
    duration_s: float | None
    total_frames: int | None
    times_s: tuple[float, ...] = ()
    indices: tuple[int, ...] = ()


# region Planning Math


def _compute_midpoint_times(duration_s: float, count: int) -> tuple[float, ...]:
    """Return `count` evenly spaced segment midpoints across `[0, duration_s]`."""
    return tuple((i + 0.5) * duration_s / count for i in range(count))


def _compute_midpoint_indices(total_frames: int, count: int) -> tuple[int, ...]:
    """Return `count` strictly increasing frame indices across `0..total_frames - 1`."""
    return tuple(min(total_frames - 1, int((i + 0.5) * total_frames / count)) for i in range(count))


def plan_frames(
    duration_s: float | None = None,
    total_frames: int | None = None,
    config: FramePlanConfig | None = None,
) -> FramePlan:
    """
    Compute the frame sampling plan for a video or animated image.

    Handles missing/corrupted container headers gracefully:
      - `total_frames == 1`: treated as static (`target_count=1`).
      - `1 < total_frames < sparse_below_frames`: sparse mode (frame-count sqrt scaling,
        bounded below by time-based count if duration is known).
      - `duration_s > 0`: rich mode (time-based sqrt scaling, independent of FPS).
      - `duration_s` missing with `total_frames >= sparse_below_frames`: falls back to frame-count scaling.
      - Both missing/invalid: minimal 1-frame fallback at `0.0s` / index `0`.
    """
    cfg = config or FramePlanConfig()

    valid_dur = duration_s if (duration_s is not None and math.isfinite(duration_s) and duration_s > 0.0) else None
    valid_frames = total_frames if (total_frames is not None and total_frames > 0) else None

    # 1. Static single-frame content
    if valid_frames == 1:
        return FramePlan(
            mode="static",
            target_count=1,
            duration_s=valid_dur,
            total_frames=1,
            times_s=(0.0,) if valid_dur is not None else (),
            indices=(0,),
        )

    # 2. Missing both duration and frame count -> safe 1-frame fallback
    if valid_dur is None and valid_frames is None:
        return FramePlan(
            mode="fallback",
            target_count=1,
            duration_s=None,
            total_frames=None,
            times_s=(0.0,),
            indices=(0,),
        )

    # Ensure max_frames acts as an absolute ceiling even if set below min_frames
    eff_min = min(cfg.min_frames, cfg.max_frames)
    time_n = round(cfg.density_k * math.sqrt(valid_dur)) if valid_dur is not None else 0

    # 3. Sparse content (few total frames) OR missing duration fallback
    if valid_frames is not None and (valid_frames < cfg.sparse_below_frames or valid_dur is None):
        mode: PlanningMode = "sparse" if valid_frames < cfg.sparse_below_frames else "fallback"
        sparse_n = round(cfg.sparse_k * math.sqrt(valid_frames))
        raw_n = max(eff_min, time_n, sparse_n)
        target_n = max(1, min(raw_n, cfg.max_frames, valid_frames))
    else:
        # 4. Rich/time-based content (independent of FPS)
        mode = "rich"
        raw_n = max(eff_min, time_n)
        upper_bound = min(cfg.max_frames, valid_frames) if valid_frames is not None else cfg.max_frames
        target_n = max(1, min(raw_n, upper_bound))

    times = _compute_midpoint_times(valid_dur, target_n) if valid_dur is not None else ()
    indices = _compute_midpoint_indices(valid_frames, target_n) if valid_frames is not None else ()

    return FramePlan(
        mode=mode,
        target_count=target_n,
        duration_s=valid_dur,
        total_frames=valid_frames,
        times_s=times,
        indices=indices,
    )


# endregion
