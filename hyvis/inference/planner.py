from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import vibe

from hyvis.db import quantize_floor

if TYPE_CHECKING:
    from hyvis.config import AppConfig, ModelConfig
    from hyvis.db import CacheDatabase, Database
    from hyvis.hydrus import FileInfo


# region Planning & Filter Fingerprinting


def compute_filter_hash(
    config: AppConfig,
    model_cfg: ModelConfig,
    *,
    video_prep_hash: str = "",
) -> str:
    """
    Compute a deterministic 16-char SHA-256 fingerprint of all settings that affect
    tag output or destination routing for `model_cfg`.

    When `video_prep_hash` is non-empty (videos / animated images), sampling parameters
    are folded in so changing video density invalidates multi-frame files without
    touching static image completion records.
    """
    payload: dict[str, Any] = {
        "output_filter": config.resolved_output_filter(model_cfg).model_dump(mode="json"),
        "output_tag_services": config.resolved_output_tag_services(model_cfg),
        "add_tags": [a.model_dump(mode="json") for a in config.hydrus.add_tags],
        "remove_tags": [r.model_dump(mode="json") for r in config.hydrus.remove_tags],
    }
    if video_prep_hash:
        payload["video_prep_hash"] = video_prep_hash
        if config.video.threshold_adjust_value != 0.0:
            payload["video_threshold_adjust"] = {
                "mode": config.video.threshold_adjust_mode,
                "value": float(config.video.threshold_adjust_value),
            }

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def is_known_multiframe(fi: FileInfo) -> bool:
    """Return True if Hydrus metadata already identifies `fi` as a video or multi-frame animation."""
    from hyvis.config import is_video_mime

    if is_video_mime(fi.mime):
        return True
    return fi.num_frames is not None and fi.num_frames > 1


def resolve_model_variant_id(model_cfg: ModelConfig) -> str:
    """Dry-run variant resolver returning the exact variant_id vibe will select without loading weights."""
    try:
        from vibe.registry import model_registry

        model_registry.ensure_discovered()
        plugin_cls = model_registry.get(model_cfg.model_id)
        resolved = vibe.resolve_variant(
            plugin_cls,
            backend=model_cfg.backend,
            variant=model_cfg.variant_id,
            device=model_cfg.device,
        )
        return resolved.variant_id or ""
    except Exception:
        return model_cfg.variant_id or ""


@dataclass
class ModelPassPlan:
    """Pre-computed routing buckets for one model pass."""

    model_cfg: ModelConfig
    filter_hash: str
    video_filter_hash: str
    video_prep_hash: str
    resolved_variant_id: str
    cache_model_row: int | None
    skip_hashes: set[str]
    refilter_files: list[FileInfo]
    files_to_infer: list[FileInfo]

    def effective_filter_hash(self, fi: FileInfo) -> str:
        """Return the filter_hash matching `fi` (static vs. multi-frame)."""
        return self.video_filter_hash if is_known_multiframe(fi) else self.filter_hash


def plan_model_pass(
    model_cfg: ModelConfig,
    file_infos: list[FileInfo],
    *,
    config: AppConfig,
    db: Database,
    cache_db: CacheDatabase,
    force: bool,
    is_resume: bool = False,
) -> ModelPassPlan:
    """
    Classify all target files into:
      - skip_hashes: Already completed with identical filter_hash (or staged on resume).
      - refilter_files: Needs filtering, raw scores available in cache.db at deep enough floor_q (CPU).
      - files_to_infer: Needs model inference (not in cache.db, too shallow floor_q, or --force).
    """
    filter_hash = compute_filter_hash(config, model_cfg)
    video_prep_hash = config.video.prep_hash()
    video_filter_hash = compute_filter_hash(config, model_cfg, video_prep_hash=video_prep_hash)
    resolved_variant = resolve_model_variant_id(model_cfg)

    staged_hashes = db.get_staged_hashes_for_model(model_cfg.model_id) if is_resume else set()
    logged = db.get_inference_filter_hashes([fi.file_hash for fi in file_infos], model_cfg.model_id)

    skip_hashes: set[str] = set()
    need_work: list[FileInfo] = []

    for fi in file_infos:
        expected_fh = video_filter_hash if is_known_multiframe(fi) else filter_hash
        if fi.file_hash in staged_hashes or (not force and logged.get(fi.file_hash) == expected_fh):
            skip_hashes.add(fi.file_hash)
        else:
            need_work.append(fi)

    if force or not need_work:
        return ModelPassPlan(
            model_cfg=model_cfg,
            filter_hash=filter_hash,
            video_filter_hash=video_filter_hash,
            video_prep_hash=video_prep_hash,
            resolved_variant_id=resolved_variant,
            cache_model_row=None,
            skip_hashes=skip_hashes,
            refilter_files=[],
            files_to_infer=need_work,
        )

    # Check cache.db for CPU re-filter candidates
    candidate_rows = cache_db.find_model_rows(model_cfg.model_id, resolved_variant)
    if not candidate_rows:
        return ModelPassPlan(
            model_cfg=model_cfg,
            filter_hash=filter_hash,
            video_filter_hash=video_filter_hash,
            video_prep_hash=video_prep_hash,
            resolved_variant_id=resolved_variant,
            cache_model_row=None,
            skip_hashes=skip_hashes,
            refilter_files=[],
            files_to_infer=need_work,
        )

    target_model_row = candidate_rows[0]
    max_floor_q = quantize_floor(config.database.min_cache_score)
    prep_hashes_by_file = {fi.file_hash: (video_prep_hash if is_known_multiframe(fi) else "") for fi in need_work}
    cached_ok = cache_db.filter_cached_hashes(
        target_model_row,
        [fi.file_hash for fi in need_work],
        max_floor_q=max_floor_q,
        prep_hashes_by_file=prep_hashes_by_file,
    )

    refilter_files = [fi for fi in need_work if fi.file_hash in cached_ok]
    files_to_infer = [fi for fi in need_work if fi.file_hash not in cached_ok]

    return ModelPassPlan(
        model_cfg=model_cfg,
        filter_hash=filter_hash,
        video_filter_hash=video_filter_hash,
        video_prep_hash=video_prep_hash,
        resolved_variant_id=resolved_variant,
        cache_model_row=target_model_row if refilter_files else None,
        skip_hashes=skip_hashes,
        refilter_files=refilter_files,
        files_to_infer=files_to_infer,
    )


# endregion
