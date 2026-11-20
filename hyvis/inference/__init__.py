"""
inference — Model pass planning, execution orchestration, and cancellation handling.
"""

from __future__ import annotations

from hyvis.inference.planner import (
    ModelPassPlan,
    compute_filter_hash,
    is_known_multiframe,
    plan_model_pass,
    resolve_model_variant_id,
)
from hyvis.inference.runner import (
    MAX_CONSECUTIVE_ERRORS,
    FileSource,
    LocalFileSource,
    PhaseStats,
    infer_files,
)

__all__ = [
    "MAX_CONSECUTIVE_ERRORS",
    "FileSource",
    "LocalFileSource",
    "ModelPassPlan",
    "PhaseStats",
    "compute_filter_hash",
    "infer_files",
    "is_known_multiframe",
    "plan_model_pass",
    "resolve_model_variant_id",
]
