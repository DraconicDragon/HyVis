"""
inference.py Inference orchestration.

infer_files()             Run a model against files (via cache re-filtering and/or model inference),
                          save raw results in disposable cache.db, stage multi-model outputs in hyvis.db,
                          and atomically enqueue tags into push_queue.
                          Does NOT require Hydrus to be reachable during inference.

FileSource abstraction
----------------------
Currently files are always local paths provided by Hydrus at startup.
The FileSource protocol exists so that a future remote implementation
(streaming file bytes from a remote Hydrus instance on demand) can be
dropped in without touching the inference loop.  See FileSource below.
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import sys
import threading
import time
from collections import deque
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import vibe
from vibe import InferenceCancelled, TagResult
from vibe_result_transforms import (
    CleanTags,
    TagLevelThresholds,
    TransformPipeline,
)

from hyvis.db import CacheDatabase, CachedVocab, FileStatus, quantize_floor
from hyvis.logging_utils import BOLD, GREEN, MAGENTA, _c

if TYPE_CHECKING:
    from hyvis.config import AppConfig, ModelConfig, OutputFilterConfig
    from hyvis.db import Database
    from hyvis.hydrus import FileInfo
    from hyvis.progress import Progress


logger = logging.getLogger(__name__)

#: Stop the run after this many consecutive errors (something is clearly wrong).
MAX_CONSECUTIVE_ERRORS = 10

_CLEANER = CleanTags()


# region FileSource protocol


class FileSource(Protocol):
    """
    Provides input data for the inference engine.
    """

    def get_input(self, file_hash: str) -> str | bytes:
        """Return a local path (str) or raw bytes for the given hash."""
        ...


@dataclass
class LocalFileSource:
    """
    Resolves inputs from a pre-built {hash → local_path} map.

    Built once at startup from the FileInfo list returned by Hydrus.
    Hydrus does not need to be running while this source is in use.
    """

    _path_map: dict[str, str]

    def __init__(self, file_infos: list[FileInfo]) -> None:
        self._path_map = {fi.file_hash: fi.local_path for fi in file_infos if fi.local_path}

    def get_input(self, file_hash: str) -> str:
        path = self._path_map.get(file_hash)
        if not path:
            raise KeyError(f"No local path for {file_hash}")
        return path

    def has_path(self, file_hash: str) -> bool:
        return file_hash in self._path_map


# @dataclass
# class RemoteFileSource:
#     """Placeholder"""

# endregion


# region Tag extraction


@dataclass(slots=True)
class TagRecord:
    """One tag destined for Hydrus, with its provenance."""

    category: str
    raw_tag: str
    prefixed_tag: str
    score: float


def _norm(tag: str) -> str:
    """Normalize tag for flexible space/underscore matching."""
    return tag.strip().replace("_", " ")


@dataclass(slots=True, frozen=True)
class CompiledOutputFilter:
    """Pre-compiled normalized lookup sets for fast tag extraction."""

    filter_cfg: OutputFilterConfig
    include_set: set[str]
    exclude_set: set[str]
    subset_managed_tags: set[str]
    prefix_overrides: dict[str, str]
    category_prefixes: dict[str, str]
    tag_replacements: dict[str, str]
    compiled_subsets: list[tuple[set[str], int]]


def compile_output_filter(output_filter: OutputFilterConfig) -> CompiledOutputFilter:
    """Compile an OutputFilterConfig once so sets/dicts aren't rebuilt on every image."""
    return CompiledOutputFilter(
        filter_cfg=output_filter,
        include_set={_norm(t) for t in output_filter.include_tags},
        exclude_set={_norm(t) for t in output_filter.exclude_tags},
        subset_managed_tags={_norm(t) for group in output_filter.max_tags_per_subset for t in group.tags},
        prefix_overrides={_norm(t): pfx for t, pfx in output_filter.tag_prefix_overrides.items()},
        category_prefixes=dict(output_filter.category_tag_prefix_mapping),
        tag_replacements={_norm(k): v for k, v in output_filter.tag_replacements.items()},
        compiled_subsets=[({_norm(t) for t in g.tags}, g.limit) for g in output_filter.max_tags_per_subset],
    )


def extract_tags(
    result_input: TagResult | dict[str, Any],
    *,
    output_filter: OutputFilterConfig | CompiledOutputFilter,
) -> list[TagRecord]:
    """
    Convert a TagResult or dictionary representation into TagRecord objects.

    Follows a strict Two-Stage architecture:
      Stage 1 (Decision Engine): Evaluates exclusions, inclusions, categories,
              subsets, and category limits using the model's native vocabulary.
      Stage 2 (Presentation Layer): Cleans underscores to spaces, applies tag
              replacements, deduplicates by max score, and applies namespace prefixes.
    """
    # Accept either raw config or pre-compiled filter
    if isinstance(output_filter, CompiledOutputFilter):
        compiled = output_filter
        filter_cfg = output_filter.filter_cfg
    else:
        compiled = compile_output_filter(output_filter)
        filter_cfg = output_filter

    # Normalize input into {category: {tag: score}} mapping
    tags_by_category: dict[str, dict[str, float]] = {}
    if isinstance(result_input, TagResult):
        for cat_name, entries in result_input.categories.items():
            tags_by_category[cat_name] = {e.tag: float(e.score) for e in entries}
    elif isinstance(result_input, dict):
        raw_cats = result_input.get("categories") or result_input.get("tags") or {}
        if isinstance(raw_cats, dict):
            for cat_name, entries in raw_cats.items():
                if isinstance(entries, list):
                    tags_by_category[cat_name] = {
                        e["tag"]: float(e.get("score", 0.0)) for e in entries if isinstance(e, dict) and "tag" in e
                    }
                elif isinstance(entries, dict):
                    tags_by_category[cat_name] = {t: float(s) for t, s in entries.items()}

    categories = filter_cfg.allowed_categories
    include_set = compiled.include_set
    exclude_set = compiled.exclude_set
    subset_managed_tags = compiled.subset_managed_tags

    prefix_overrides = compiled.prefix_overrides
    category_prefixes = compiled.category_prefixes
    tag_replacements = compiled.tag_replacements

    # region Stage 1
    # Decision engine

    standard_records_by_category: dict[str, list[TagRecord]] = {}
    subset_records: list[TagRecord] = []

    for category, tag_scores in tags_by_category.items():
        standard_records_by_category[category] = []

        for raw_tag, score in tag_scores.items():
            norm_tag = _norm(raw_tag)

            # 1. Exclusions: Drop if explicitly excluded
            if norm_tag in exclude_set:
                continue

            # 2. Inclusions: Always keep if explicitly included
            is_allowed = norm_tag in include_set

            # 3. Category Whitelist: If not explicitly included, check category allowance
            if not is_allowed and (categories is not None and category not in categories):
                continue

            record = TagRecord(
                category=category,
                raw_tag=raw_tag,
                prefixed_tag=raw_tag,  # Formatted in Stage 2
                score=float(score),
            )

            # Isolate subset-managed tags from standard category limits
            if norm_tag in subset_managed_tags:
                subset_records.append(record)
            else:
                standard_records_by_category[category].append(record)

    surviving_records: list[TagRecord] = []

    # apply category limits
    for category, cat_records in standard_records_by_category.items():
        limit = filter_cfg.max_tags_per_category.get(category)
        if limit is not None:
            # sort descending by score, keep top-N
            cat_records.sort(key=lambda r: r.score, reverse=True)
            cat_records = cat_records[:limit]
        surviving_records.extend(cat_records)

    # apply subset limits to isolated tags using pre-compiled tag sets
    for group_tags_set, limit in compiled.compiled_subsets:
        matching_subset_records = [r for r in subset_records if _norm(r.raw_tag) in group_tags_set]

        if len(matching_subset_records) > limit:
            matching_subset_records.sort(key=lambda r: r.score, reverse=True)
            matching_subset_records = matching_subset_records[:limit]

        surviving_records.extend(matching_subset_records)

        # Purge ALL records belonging to this subset group so dropped ones cannot leak into fallback
        subset_records = [r for r in subset_records if _norm(r.raw_tag) not in group_tags_set]

    # Add any remaining subset records that were not covered by a defined group
    surviving_records.extend(subset_records)

    # region Stage 2
    # Presentation layer

    formatted_map: dict[str, TagRecord] = {}

    for r in surviving_records:
        norm_raw = _norm(r.raw_tag)

        # 1. Clean underscores to spaces while preserving kaomojis
        cleaned_tag = _CLEANER.clean_text(r.raw_tag)
        norm_cleaned = _norm(cleaned_tag)

        # 2. Tag replacement (checks both normalized raw and cleaned versions)
        replaced_tag = tag_replacements.get(norm_cleaned, tag_replacements.get(norm_raw, cleaned_tag))

        # 3. Namespace prefixing (individual overrides take precedence over category prefixes)
        prefix = prefix_overrides.get(
            norm_raw, prefix_overrides.get(norm_cleaned, category_prefixes.get(r.category, ""))
        )
        prefixed_tag = f"{prefix}{replaced_tag}"

        # 4. Merge duplicates by taking the maximum confidence score
        if prefixed_tag not in formatted_map or r.score > formatted_map[prefixed_tag].score:
            formatted_map[prefixed_tag] = TagRecord(
                category=r.category,
                raw_tag=replaced_tag,
                prefixed_tag=prefixed_tag,
                score=r.score,
            )

    return list(formatted_map.values())


# endregion


# region Processor chain


def _resolve_effective_thresholds_from_items(
    label_items: Iterable[tuple[str, str]],
    calibrated_values: Mapping[str, float] | None,
    output_filter: OutputFilterConfig,
) -> tuple[dict[str, float], float]:
    """
    Resolve exact, unambiguous per-tag thresholds honoring precedence,
    relative offsets, override_tlt, and category/tag-level fallbacks.
    """
    use_tlt = output_filter.prefer_tag_level_thresholds and bool(calibrated_values)
    base_calibrated = dict(calibrated_values) if (use_tlt and calibrated_values) else {}

    rel_scale = 1.0 + output_filter.tag_level_threshold_relative_offset

    # Normalized user configuration lookups
    tag_overrides = {_norm(k): v for k, v in output_filter.tag_thresholds.items()}
    cat_overrides = output_filter.category_thresholds

    final_map: dict[str, float] = {}

    for raw_name, cat_name in label_items:
        norm_name = _norm(raw_name)
        tag_cfg = tag_overrides.get(norm_name)
        cat_cfg = cat_overrides.get(cat_name)

        # Case A: Tag has a model-calibrated threshold
        if raw_name in base_calibrated:
            if tag_cfg and tag_cfg.override_tlt:
                thresh = tag_cfg.threshold  # User tag override (unscaled)
            elif cat_cfg and cat_cfg.override_tlt:
                thresh = cat_cfg.threshold  # User category override (unscaled)
            else:
                thresh = base_calibrated[raw_name] * rel_scale  # Calibrated model value (scaled)

        # Case B: Tag has NO calibrated threshold (fallback path)
        else:
            if tag_cfg:
                thresh = tag_cfg.threshold  # Tag-specific fallback
            elif cat_cfg:
                thresh = cat_cfg.threshold  # Category-specific fallback
            else:
                thresh = output_filter.default_threshold  # Global fallback

        final_map[raw_name] = thresh
        final_map[norm_name] = thresh

    return final_map, output_filter.default_threshold


def resolve_effective_thresholds(
    session: vibe.ModelSession,
    output_filter: OutputFilterConfig,
) -> tuple[dict[str, float], float]:
    """Resolve effective per-tag thresholds from an active vibe ModelSession."""
    label_items = (
        (
            info.name,
            info.category.value if hasattr(info.category, "value") else str(info.category),
        )
        for info in (session.tagger.catalog.labels if session.tagger.catalog else ())
    )
    calibrated = (
        session.tagger.thresholds.values
        if (TagLevelThresholds.is_supported(session) and session.tagger.thresholds)
        else None
    )
    return _resolve_effective_thresholds_from_items(label_items, calibrated, output_filter)


def build_transform_pipeline(
    session: vibe.ModelSession,
    output_filter: OutputFilterConfig,
) -> TransformPipeline:
    """
    Build the TransformPipeline for one model session using vibe-result-transforms.
    """
    threshold_map, global_fallback = resolve_effective_thresholds(session, output_filter)

    transforms = [
        # Relative offsets were already applied during resolution to calibrated tags only.
        # Passing relative_offset=0.0 prevents double-scaling or scaling explicit user values.
        TagLevelThresholds(
            threshold_map=threshold_map,
            fallback=global_fallback,
        ),
        CleanTags(),
    ]
    return TransformPipeline(transforms)


def build_transform_pipeline_from_vocab(
    vocab: CachedVocab,
    output_filter: OutputFilterConfig,
) -> TransformPipeline:
    """
    Build the TransformPipeline directly from cache.db CachedVocab without loading model weights.
    """
    label_items = ((vocab.idx_to_tag[idx], vocab.idx_to_category[idx]) for idx in vocab.idx_to_tag)
    calibrated = vocab.calibrated_thresholds or None
    threshold_map, global_fallback = _resolve_effective_thresholds_from_items(
        label_items,
        calibrated,
        output_filter,
    )
    return TransformPipeline(
        [
            TagLevelThresholds(
                threshold_map=threshold_map,
                fallback=global_fallback,
            ),
            CleanTags(),
        ]
    )


# endregion


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


# region Run statistics


@dataclass
class PhaseStats:
    """Counters for one model pass."""

    model_id: str
    ok: int = 0  # Newly inferred by model
    refiltered: int = 0  # Re-filtered on CPU from cache.db
    zero_tags: int = 0  # Number of files where inference/refilter succeeded, but 0 tags passed filter
    errors: int = 0
    skipped: int = 0
    aborted_early: bool = False
    abort_reason: str = ""
    total_tags_enqueued: int = 0


# endregion


# region Inference & Re-Filtering


def _finalize_file(
    *,
    file_hash: str,
    model_cfg: ModelConfig,
    variant_id: str,
    vocab_hash: str,
    filter_hash: str,
    prefixed_tags: list[str],
    output_services: list[str],
    config: AppConfig,
    db: Database,
    last_active_model_for_file: Mapping[str, str] | None,
    model_meta_by_id: Mapping[str, tuple[str, str, str]],
) -> bool:
    """
    Stage intermediate tags in run_progress, or commit atomically if this is the final active model.
    Returns True if this call performed the final atomic commit for `file_hash`.
    """
    my_service_tags: dict[str, list[str]] = (
        {svc_key: list(prefixed_tags) for svc_key in output_services} if prefixed_tags else {}
    )

    is_final = last_active_model_for_file is None or last_active_model_for_file.get(file_hash) == model_cfg.model_id

    if not is_final:
        db.stage_model_progress(file_hash, model_cfg.model_id, my_service_tags)
        return False

    # Final active model for this file: merge any staged payloads + current tags
    staged = db.get_staged_progress(file_hash)
    add_by_service: dict[str, list[str]] = {}
    delete_by_service: dict[str, list[str]] = {}

    for staged_svc_map in staged.values():
        for svc_key, tags in staged_svc_map.items():
            if tags:
                add_by_service.setdefault(svc_key, []).extend(tags)

    for svc_key, tags in my_service_tags.items():
        if tags:
            add_by_service.setdefault(svc_key, []).extend(tags)

    any_tags_emitted = any(bool(tags) for tags in add_by_service.values())

    for a_cfg in config.hydrus.add_tags:
        if a_cfg.only_if_tags_emitted and not any_tags_emitted:
            continue
        for svc_key in a_cfg.tag_service_keys:
            add_by_service.setdefault(svc_key, []).extend(a_cfg.tags)

    for r_cfg in config.hydrus.remove_tags:
        if r_cfg.only_if_tags_emitted and not any_tags_emitted:
            continue
        for svc_key in r_cfg.tag_service_keys:
            delete_by_service.setdefault(svc_key, []).extend(r_cfg.tags)

    completed_models: list[tuple[str, str, str, str]] = []
    for staged_m_id in staged:
        if staged_m_id == model_cfg.model_id:
            continue
        v_id, vh, fh = model_meta_by_id.get(staged_m_id, ("", "", ""))
        completed_models.append((staged_m_id, v_id, vh, fh))
    completed_models.append((model_cfg.model_id, variant_id, vocab_hash, filter_hash))

    db.commit_file_completion(
        file_hash,
        completed_models=completed_models,
        add_by_service=add_by_service,
        delete_by_service=delete_by_service,
    )
    return True


async def infer_files(
    model_cfg: ModelConfig,
    file_infos: list[FileInfo],
    *,
    config: AppConfig,
    db: Database,
    cache_db: CacheDatabase,
    progress: Progress,
    force: bool,
    plan: ModelPassPlan | None = None,
    last_active_model_for_file: Mapping[str, str] | None = None,
    model_meta_by_id: dict[str, tuple[str, str, str]] | None = None,
) -> PhaseStats:
    """
    Execute one model pass:
      1. Skip files already completed under identical filter_hash.
      2. Re-filter files present in disposable cache.db on CPU (0 GPU load).
      3. Run model inference on remaining files, storing packed binary scores in cache.db.
      4. Stage or atomically commit completed files into push_queue + inference_log.
    """
    stats = PhaseStats(model_id=model_cfg.model_id)

    if model_meta_by_id is None:
        model_meta_by_id = {}

    if plan is None:
        plan = plan_model_pass(
            model_cfg,
            file_infos,
            config=config,
            db=db,
            cache_db=cache_db,
            force=force,
            is_resume=False,
        )

    stats.skipped = len(plan.skip_hashes)
    if stats.skipped:
        progress.tick(skipped=stats.skipped)

    logger.info(
        "Model %s plan: %d infer, %d re-filter (cpu), %d skipped",
        model_cfg.model_id,
        len(plan.files_to_infer),
        len(plan.refilter_files),
        stats.skipped,
    )

    if not plan.refilter_files and not plan.files_to_infer:
        return stats

    eff_filter = config.resolved_output_filter(model_cfg)
    output_services = config.resolved_output_tag_services(model_cfg)
    compiled_filter = compile_output_filter(eff_filter)

    from hyvis.video.frame_cache import FrameDiskCache, resolve_frame_cache_dir

    is_dry = db.read_only

    # region Pass A: CPU Re-filter from cache.db (zero model weight loading)
    if plan.refilter_files and plan.cache_model_row is not None:
        vocab = cache_db.get_vocab(plan.cache_model_row)
        if vocab is None:
            # Defensive fallback: move to GPU pass if vocab row disappeared unexpectedly
            plan.files_to_infer.extend(plan.refilter_files)
            plan.refilter_files.clear()
        else:
            model_meta_by_id[model_cfg.model_id] = (vocab.variant_id, vocab.vocab_hash, plan.filter_hash)
            cpu_pipeline = build_transform_pipeline_from_vocab(vocab, eff_filter)

            fc_dir = resolve_frame_cache_dir(config, db_path=getattr(db, "_path", None))
            pass_a_frame_cache = FrameDiskCache(fc_dir) if (not is_dry and fc_dir.is_dir()) else None

            print(_c(f"  Re-filtering {len(plan.refilter_files)} cached item(s) on CPU (cache.db)...", GREEN))
            print()
            progress.reset_start_time()

            for idx, fi in enumerate(plan.refilter_files):
                try:
                    cached_pred = cache_db.get_prediction(plan.cache_model_row, fi.file_hash)
                    if cached_pred is None:
                        plan.files_to_infer.append(fi)
                        continue

                    indices, scores, _ = cached_pred
                    raw_result = CacheDatabase.reconstruct_tag_result(vocab, indices, scores)
                    filtered_result = cpu_pipeline(raw_result)
                    tag_records = extract_tags(filtered_result, output_filter=compiled_filter)
                    prefixed_tags = [tr.prefixed_tag for tr in tag_records]

                    # Ensure known_files row exists to satisfy inference_log FK
                    db.upsert_file(
                        fi.file_hash,
                        file_path=fi.local_path,
                        mime=fi.mime,
                        num_frames=fi.num_frames,
                        status=FileStatus.ACTIVE,
                    )

                    if not prefixed_tags:
                        stats.zero_tags += 1

                    is_final = _finalize_file(
                        file_hash=fi.file_hash,
                        model_cfg=model_cfg,
                        variant_id=vocab.variant_id,
                        vocab_hash=vocab.vocab_hash,
                        filter_hash=plan.effective_filter_hash(fi),
                        prefixed_tags=prefixed_tags,
                        output_services=output_services,
                        config=config,
                        db=db,
                        last_active_model_for_file=last_active_model_for_file,
                        model_meta_by_id=model_meta_by_id,
                    )
                    if is_final and pass_a_frame_cache is not None:
                        pass_a_frame_cache.cleanup_file(fi.file_hash)

                    stats.refiltered += 1
                    stats.total_tags_enqueued += len(prefixed_tags)

                    if idx % 25 == 0 or idx == len(plan.refilter_files) - 1:
                        progress.set_last_file_info(fi.file_hash, model_cfg.model_id, len(prefixed_tags), redraw=False)
                    progress.tick(processed=1)

                except Exception as exc:
                    _handle_infer_error(exc=exc, file_hash=fi.file_hash, fi=fi, db=db, stats=stats)
                    progress.tick(errors=1)

            db.commit()

    # region Pass B: Model Inference (only if files still need model inference)
    if not plan.files_to_infer:
        return stats

    from hyvis.config import ANIMATABLE_IMAGE_MIMES
    from hyvis.video.aggregator import FrameMaxAggregator
    from hyvis.video.extractor import is_animated_image, iter_extract_frames, probe_media
    from hyvis.video.planner import plan_frames

    source = LocalFileSource(plan.files_to_infer)
    file_info_map: dict[str, FileInfo] = {fi.file_hash: fi for fi in plan.files_to_infer}

    static_files: list[FileInfo] = []
    multiframe_files: list[FileInfo] = []
    skipped_unprobed_anim = 0

    for fi in plan.files_to_infer:
        if not source.has_path(fi.file_hash):
            logger.warning("Model %s: %s has no local path, marking missing", model_cfg.model_id, fi.file_hash[:8])
            db.upsert_file(
                fi.file_hash,
                mime=fi.mime,
                num_frames=fi.num_frames,
                file_path=None,
                status=FileStatus.MISSING_ON_DISK,
            )
            progress.tick(errors=1)
            stats.errors += 1
            continue

        if is_known_multiframe(fi):
            if config.video.enabled:
                multiframe_files.append(fi)
            else:
                logger.warning(
                    "Skipping multi-frame file %s (%s) because video.enabled=False.",
                    fi.file_hash[:8],
                    fi.mime,
                )
                skipped_unprobed_anim += 1
            continue

        # Header-only animation guard when Hydrus metadata omitted num_frames on PNG/WebP/AVIF/JXL.
        if fi.num_frames is None and fi.mime.strip().lower() in ANIMATABLE_IMAGE_MIMES:
            assert fi.local_path is not None
            try:
                if is_animated_image(fi.local_path):
                    if config.video.enabled:
                        fi.num_frames = 2  # Refined by probe_media() during extraction.
                        multiframe_files.append(fi)
                    else:
                        logger.warning(
                            "Skipping animated %s (%s) because video.enabled=False.",
                            fi.file_hash[:8],
                            fi.mime,
                        )
                        skipped_unprobed_anim += 1
                    continue
                fi.num_frames = 1
            except Exception as exc:
                logger.debug("Header animation check failed for %s: %s", fi.file_hash[:8], exc)

        static_files.append(fi)

    if skipped_unprobed_anim:
        stats.skipped += skipped_unprobed_anim
        progress.tick(skipped=skipped_unprobed_anim)

    if not static_files and not multiframe_files:
        return stats

    print()
    print(_c("  Press 'q' to cancel current model", MAGENTA, BOLD))
    print()
    if stats.skipped != 0:
        print(f"  {stats.skipped} items skipped (up-to-date)")
        print()

    load_kwargs: dict[str, Any] = {
        "source": model_cfg.source,
        "device": model_cfg.device,
        "precision": model_cfg.precision,
    }
    if model_cfg.backend is not None:
        load_kwargs["backend"] = model_cfg.backend
    if model_cfg.variant_id is not None:
        load_kwargs["variant"] = model_cfg.variant_id

    consecutive_errors = 0
    listener = None
    stop_cancel = threading.Event()

    try:
        with vibe.load(model_cfg.model_id, **load_kwargs) as session:
            exec_info = session.execution_info()
            runtime = exec_info.get("runtime", {})

            if session.backend == vibe.Backend.PYTORCH:
                prec = runtime.get("precision", {})
                weight_dt = prec.get("weight_dtype", "unknown")
                compute_dt = prec.get("compute_dtype", "unknown")
                autocast = " (autocast)" if prec.get("autocast_enabled") else ""

                device_str = str(runtime.get("device", "unknown"))
                try:
                    import torch

                    if device_str.startswith("cuda") and torch.cuda.is_available():
                        idx = int(device_str.split(":")[1]) if ":" in device_str else torch.cuda.current_device()
                        device_str = f"cuda:{idx} ({torch.cuda.get_device_name(idx)})"
                except Exception:
                    pass

                runtime_str = f"PyTorch on {device_str} | Weights: {weight_dt} | Compute: {compute_dt}{autocast}"

            elif session.backend == vibe.Backend.ONNX:
                providers = runtime.get("providers", [])
                active_ep = providers[0] if providers else "unknown"
                runtime_str = f"ONNX on {active_ep}"
            else:
                runtime_str = f"{session.backend}"

            print(f"    Runtime: {_c(runtime_str, GREEN)}")
            print()
            logger.info("Model '%s' initialized | %s", model_cfg.model_id, runtime_str)

            progress.reset_start_time()
            pipeline = build_transform_pipeline(session, eff_filter)

            catalog = session.tagger.catalog
            resolved_variant = session.variant or ""
            vocab_hash = catalog.vocab_hash
            model_meta_by_id[model_cfg.model_id] = (resolved_variant, vocab_hash, plan.filter_hash)

            cache_raw_enabled = config.database.cache_raw_predictions
            min_cache_score = config.database.min_cache_score

            cache_model_row = -1
            if cache_raw_enabled:
                calibrated = (
                    session.tagger.thresholds.values
                    if (TagLevelThresholds.is_supported(session) and session.tagger.thresholds)
                    else None
                )
                cache_model_row = cache_db.register_model(
                    model_cfg.model_id,
                    resolved_variant,
                    catalog,
                    calibrated_thresholds=calibrated,
                )

            listener, stop_cancel = _start_cancel_listener(session, model_cfg.model_id)

            def _commit_inferred_result(fi: FileInfo, raw_result: TagResult) -> int:
                """Persist raw scores, filter tags, and stage/commit file completion."""
                file_hash = fi.file_hash
                db.upsert_file(
                    file_hash,
                    file_path=fi.local_path,
                    mime=fi.mime,
                    num_frames=fi.num_frames,
                    status=FileStatus.ACTIVE,
                )

                if cache_raw_enabled and cache_model_row >= 0:
                    indices, scores = raw_result.to_indexed_scores(catalog, min_score=min_cache_score)
                    cache_db.save_prediction(
                        cache_model_row,
                        file_hash,
                        indices,
                        scores,
                        min_cache_score=min_cache_score,
                        prep_hash=plan.video_prep_hash if is_known_multiframe(fi) else "",
                    )

                filtered_result = pipeline(raw_result)
                tag_records = extract_tags(filtered_result, output_filter=compiled_filter)
                prefixed_tags = [tr.prefixed_tag for tr in tag_records]
                if not prefixed_tags:
                    stats.zero_tags += 1

                is_final = _finalize_file(
                    file_hash=file_hash,
                    model_cfg=model_cfg,
                    variant_id=resolved_variant,
                    vocab_hash=vocab_hash,
                    filter_hash=plan.effective_filter_hash(fi),
                    prefixed_tags=prefixed_tags,
                    output_services=output_services,
                    config=config,
                    db=db,
                    last_active_model_for_file=last_active_model_for_file,
                    model_meta_by_id=model_meta_by_id,
                )
                return len(prefixed_tags) if is_final or prefixed_tags else 0

            # Sub-pass B1: static images use the ordinary Vibe file loader.
            if static_files:
                static_inputs = {fi.file_hash: source.get_input(fi.file_hash) for fi in static_files}
                async for chunk in session.infer_async(static_inputs, batch_size=model_cfg.batch_size):
                    batch_processed = 0
                    batch_errors = 0
                    last_file_hash = None
                    last_tag_count = 0

                    for item in chunk:
                        file_hash = str(item.input_ref)
                        fi = file_info_map[file_hash]
                        try:
                            raw_result = item.result
                            if not isinstance(raw_result, TagResult):
                                raise TypeError(
                                    f"Expected TagResult from tagger model, got {type(raw_result).__name__}"
                                )
                            tag_count = _commit_inferred_result(fi, raw_result)
                            consecutive_errors = 0
                            stats.ok += 1
                            stats.total_tags_enqueued += tag_count
                            batch_processed += 1
                            last_file_hash = file_hash
                            last_tag_count = tag_count
                        except Exception as exc:
                            _handle_infer_error(exc=exc, file_hash=file_hash, fi=fi, db=db, stats=stats)
                            consecutive_errors += 1
                            batch_errors += 1
                            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                                stats.aborted_early = True
                                stats.abort_reason = f"Aborted: {consecutive_errors} consecutive errors (last: {exc})"
                                return stats

                    if last_file_hash is not None:
                        progress.set_last_file_info(last_file_hash, model_cfg.model_id, last_tag_count, redraw=False)
                    if batch_processed or batch_errors:
                        progress.tick(processed=batch_processed, errors=batch_errors)

            # Sub-Pass B2: Videos & Animated Images (lazy frame streaming + max-score merge)
            if multiframe_files:
                fc_mode = config.video.frame_cache_mode
                # NOTE: In "auto" mode, len(models) > 1 enables disk caching even on a --resume run
                # where only the final model remains (writes cached frames and deletes them immediately
                # after commit). Harmless and safe; keeping the check simple avoids guessing remaining passes.
                use_disk_cache = (not is_dry) and (
                    fc_mode == "disk" or (fc_mode == "auto" and len(config.inference.models) > 1)
                )
                frame_cache = (
                    FrameDiskCache(resolve_frame_cache_dir(config, db_path=getattr(db, "_path", None)))
                    if use_disk_cache
                    else None
                )
                plan_cfg = config.video.to_plan_config()
                prep_hash = plan.video_prep_hash

                pending_refs: deque[tuple[str, int, int]] = deque()
                decode_errors: queue.SimpleQueue[tuple[FileInfo, Exception]] = queue.SimpleQueue()
                failed_decode_hashes: set[str] = set()

                def _multiframe_image_stream() -> Iterator[Any]:
                    for fi in multiframe_files:
                        if session.is_cancellation_requested():
                            break
                        assert fi.local_path is not None
                        try:
                            probe = probe_media(fi.local_path)
                            if probe.total_frames is not None:
                                fi.num_frames = probe.total_frames
                            f_plan = plan_frames(probe.duration_s, probe.total_frames, config=plan_cfg)
                            expected_n = f_plan.target_count

                            if frame_cache is not None and frame_cache.validate_frames(fi.file_hash, prep_hash):
                                frame_iter = frame_cache.load_frames(fi.file_hash, prep_hash)
                            else:
                                raw_iter = iter_extract_frames(
                                    fi.local_path,
                                    config=plan_cfg,
                                    probe=probe,
                                    plan=f_plan,
                                )
                                if frame_cache is not None:
                                    frame_iter = frame_cache.save_and_yield_frames(fi.file_hash, prep_hash, raw_iter)
                                else:
                                    frame_iter = raw_iter

                            for frame in frame_iter:
                                pending_refs.append((fi.file_hash, frame.index, expected_n))
                                yield frame.image

                        except ImportError as exc:
                            # Environment/dependency failure: stop immediately instead of blaming files
                            decode_errors.put((fi, exc))
                            return
                        except Exception as exc:
                            failed_decode_hashes.add(fi.file_hash)
                            decode_errors.put((fi, exc))

                def _multiframe_ref_stream() -> Iterator[tuple[str, int, int]]:
                    while pending_refs:
                        yield pending_refs.popleft()

                active_hash: str | None = None
                active_agg: FrameMaxAggregator | None = None
                active_expected = 0
                total_frames_inferred = 0
                multiframe_t0 = time.monotonic()

                def _flush_active_video() -> None:
                    nonlocal active_hash, active_agg, active_expected, consecutive_errors
                    if active_hash is None or active_agg is None:
                        return
                    fh = active_hash
                    agg = active_agg
                    expected = active_expected
                    active_hash = None
                    active_agg = None
                    active_expected = 0
                    fi = file_info_map[fh]

                    if fh in failed_decode_hashes:
                        progress.set_sub_status("", redraw=False)
                        return

                    try:
                        if expected >= 4 and agg.frame_count < expected / 2:
                            logger.warning(
                                "Video %s yielded significantly fewer frames than planned (%d/%d).",
                                fh[:8],
                                agg.frame_count,
                                expected,
                            )

                        merged = agg.finalize()
                        tag_count = _commit_inferred_result(fi, merged)
                        if frame_cache is not None and (
                            last_active_model_for_file is None
                            or last_active_model_for_file.get(fh) == model_cfg.model_id
                        ):
                            frame_cache.cleanup_file(fh)

                        consecutive_errors = 0
                        stats.ok += 1
                        stats.total_tags_enqueued += tag_count
                        progress.set_sub_status("", redraw=False)
                        progress.set_last_file_info(fh, model_cfg.model_id, tag_count, redraw=False)
                        progress.tick(processed=1)
                    except Exception as exc:
                        _handle_infer_error(exc=exc, file_hash=fh, fi=fi, db=db, stats=stats)
                        consecutive_errors += 1
                        progress.set_sub_status("", redraw=False)
                        progress.tick(errors=1)
                        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            raise RuntimeError(
                                f"Aborted: {consecutive_errors} consecutive errors (last: {exc})"
                            ) from exc

                def _drain_decode_errors() -> None:
                    while not decode_errors.empty():
                        bad_fi, exc = decode_errors.get_nowait()
                        if isinstance(exc, ImportError):
                            raise exc

                        # Per-file media/decode errors mark the file as ERROR without counting
                        # toward MAX_CONSECUTIVE_ERRORS (reserved for inference/runtime failures).
                        _handle_infer_error(
                            exc=exc,
                            file_hash=bad_fi.file_hash,
                            fi=bad_fi,
                            db=db,
                            stats=stats,
                        )
                        if frame_cache is not None:
                            frame_cache.cleanup_file(bad_fi.file_hash)
                        progress.tick(errors=1)

                try:
                    async for chunk in session.infer_async(
                        _multiframe_image_stream(),
                        refs=_multiframe_ref_stream(),
                        batch_size=model_cfg.batch_size,
                    ):
                        _drain_decode_errors()

                        for item in chunk:
                            file_hash, _frame_idx, expected_n = item.input_ref
                            if active_hash is not None and file_hash != active_hash:
                                _flush_active_video()

                            if file_hash in failed_decode_hashes:
                                continue

                            if active_hash is None:
                                active_hash = file_hash
                                active_agg = FrameMaxAggregator()
                                active_expected = expected_n

                            raw_result = item.result
                            if not isinstance(raw_result, TagResult):
                                failed_decode_hashes.add(file_hash)
                                decode_errors.put(
                                    (
                                        file_info_map[file_hash],
                                        TypeError(f"Expected TagResult, got {type(raw_result).__name__}"),
                                    )
                                )
                                continue

                            assert active_agg is not None
                            active_agg.update(raw_result)
                            total_frames_inferred += 1

                        if active_hash is not None and active_agg is not None:
                            fr_elapsed = max(time.monotonic() - multiframe_t0, 0.1)
                            fr_rate = total_frames_inferred / fr_elapsed
                            progress.set_sub_status(
                                f"Video {active_hash[:8]} ({active_agg.frame_count}/{active_expected}) "
                                f"│ Frames: {total_frames_inferred} ({fr_rate:.1f} fr/s)"
                            )

                    _flush_active_video()
                    _drain_decode_errors()
                except ImportError as imp_exc:
                    stats.aborted_early = True
                    stats.abort_reason = f"Aborted: {imp_exc}"
                    return stats
                except RuntimeError as abort_exc:
                    if str(abort_exc).startswith("Aborted:"):
                        stats.aborted_early = True
                        stats.abort_reason = str(abort_exc)
                        return stats
                    raise
                finally:
                    progress.set_sub_status("")

            db.commit()
            cache_db.commit()

    except InferenceCancelled:
        stats.aborted_early = True
        stats.abort_reason = "Inference cancelled by user (press 'q')"
        logger.info("Inference cancelled for model %s", model_cfg.model_id)

    except KeyboardInterrupt:
        stats.aborted_early = True
        stats.abort_reason = "Cancelled by user"
        logger.info("Keyboard interrupt for model %s", model_cfg.model_id)

    except Exception as exc:
        stats.aborted_early = True
        stats.abort_reason = f"Unexpected error loading/running model: {exc}"
        logger.exception("Fatal error running model %s", model_cfg.model_id)

    finally:
        stop_cancel.set()
        if listener is not None:
            listener.join(timeout=1.0)
        db.commit()
        cache_db.commit()

    logger.info(
        "Model '%s' pass finished: inferred=%d, refiltered=%d, skipped=%d | zero_tags=%d, enqueued=%d tags | errors=%d",
        model_cfg.model_id,
        stats.ok,
        stats.refiltered,
        stats.skipped,
        stats.zero_tags,
        stats.total_tags_enqueued,
        stats.errors,
    )
    return stats


# endregion


# region Helpers


def _handle_infer_error(
    *,
    exc: Exception,
    file_hash: str,
    fi: FileInfo,
    db: Database,
    stats: PhaseStats,
) -> None:
    msg = str(exc)
    # Clear active progress line before printing error so it doesn't overwrite
    sys.stdout.write("\r" + " " * 80 + "\r")
    sys.stdout.flush()
    logger.error("Inference error for %s: %s", file_hash[:8], msg)
    try:
        db.upsert_file(
            file_hash,
            mime=fi.mime,
            num_frames=fi.num_frames,
            file_path=fi.local_path,
            status=FileStatus.ERROR,
        )
        db.batch_tick(batch_size=10)
    except Exception as db_exc:
        logger.error("Failed to update status for %s in DB: %s", file_hash[:8], db_exc)
    stats.errors += 1


def _start_cancel_listener(
    session: Any,
    model_id: str,
) -> tuple[threading.Thread | None, threading.Event]:
    stop_event = threading.Event()

    if not sys.stdin.isatty():
        return None, stop_event

    # Use platform-appropriate method
    if sys.platform == "win32":
        return _start_cancel_listener_windows(session, model_id, stop_event)
    else:
        return _start_cancel_listener_unix(session, model_id, stop_event)


def _start_cancel_listener_unix(
    session: Any,
    model_id: str,
    stop_event: threading.Event,
) -> tuple[threading.Thread | None, threading.Event]:
    import termios
    import tty

    def _listen() -> None:
        fd = sys.stdin.fileno()
        try:
            old_attrs = termios.tcgetattr(fd)
        except Exception:
            return

        try:
            tty.setcbreak(fd)
            while not stop_event.is_set():
                char = sys.stdin.read(1)
                if stop_event.is_set():
                    break
                if char in ("q", "Q"):
                    logger.warning("Cancel requested for model %s ('q' key)", model_id)
                    session.cancel_current_inference()
                    break
        except Exception:
            logger.debug("Cancel listener error for model %s", model_id, exc_info=True)
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
            except Exception:
                pass

    thread = threading.Thread(target=_listen, name="cancel-key-listener", daemon=True)
    thread.start()
    return thread, stop_event


def _start_cancel_listener_windows(
    session: Any,
    model_id: str,
    stop_event: threading.Event,
) -> tuple[threading.Thread | None, threading.Event]:
    import msvcrt

    def _listen() -> None:
        try:
            while not stop_event.is_set():
                if msvcrt.kbhit():  # ty:ignore[unresolved-attribute]
                    char = msvcrt.getch().decode("utf-8", errors="ignore")  # ty:ignore[unresolved-attribute]
                    if char in ("q", "Q"):
                        logger.warning("Cancel requested for model %s ('q' key)", model_id)
                        session.cancel_current_inference()
                        break
                # Small sleep to avoid busy-waiting
                stop_event.wait(0.1)
        except Exception:
            logger.debug("Cancel listener error for model %s", model_id, exc_info=True)

    thread = threading.Thread(target=_listen, name="cancel-key-listener", daemon=True)
    thread.start()
    return thread, stop_event


# endregion
