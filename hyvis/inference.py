"""
inference.py Inference orchestration.

infer_files()             Run a model against files, store raw results in DB cache,
                          apply transforms, and enqueue tags into push_queue.
                          Does NOT require Hydrus to be reachable during inference.

FileSource abstraction
----------------------
Currently files are always local paths provided by Hydrus at startup.
The FileSource protocol exists so that a future remote implementation
(streaming file bytes from a remote Hydrus instance on demand) can be
dropped in without touching the inference loop.  See FileSource below.
"""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import vibe
from vibe.results import TagResult
from vibe.session import InferenceCancelled
from vibe_result_transforms import (
    CleanTags,
    TagLevelThresholds,
    TransformPipeline,
)

from hyvis.logging_utils import BOLD, MAGENTA, _c

if TYPE_CHECKING:
    from hyvis.config import AppConfig, ModelConfig, OutputFilterConfig
    from hyvis.db import Database
    from hyvis.hydrus import FileInfo, HydrusClient
    from hyvis.progress import Progress

logger = logging.getLogger(__name__)

#: Stop the run after this many consecutive errors (something is clearly wrong).
MAX_CONSECUTIVE_ERRORS = 10


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


def extract_tags(
    result_input: TagResult | dict[str, Any],
    *,
    output_filter: OutputFilterConfig,
) -> list[TagRecord]:
    """
    Convert a TagResult or dictionary representation into TagRecord objects.

    Applies inclusions, exclusions, category limits, namespace prefixes,
    and joint subset limits. Normalizes tags to support both underscores and spaces.
    """
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

    records: list[TagRecord] = []

    categories = output_filter.output_categories
    # sets for fast lookups (normalized)
    include_set = {_norm(t) for t in output_filter.include_tags}
    exclude_set = {_norm(t) for t in output_filter.exclude_tags}

    # set of all tags governed by custom subset limits (normalized)
    subset_managed_tags = {_norm(t) for group in output_filter.max_tags_per_subset for t in group.tags}
    prefix_overrides = {_norm(t): pfx for t, pfx in output_filter.tag_prefix_overrides.items()}

    standard_records_by_category: dict[str, list[TagRecord]] = {}
    subset_records: list[TagRecord] = []

    for category, tag_scores in tags_by_category.items():
        category_prefix = output_filter.category_tag_prefix_mapping.get(category) or ""
        standard_records_by_category[category] = []

        for raw_tag, score in tag_scores.items():
            norm_tag = _norm(raw_tag)

            # 1. Check Exclusions: Drop if explicitly excluded
            if norm_tag in exclude_set:
                continue

            # 2. Check Overrides: Always keep if explicitly included
            is_allowed = norm_tag in include_set

            # 3. Check Categories: If not explicitly included, fallback to standard category checks
            if not is_allowed and (not categories or category not in categories):
                continue

            # Determine effective prefix (individual override takes precedence)
            prefix = prefix_overrides.get(norm_tag, category_prefix)
            record = TagRecord(
                category=category,
                raw_tag=raw_tag,
                prefixed_tag=f"{prefix}{raw_tag}",
                score=float(score),
            )

            # Isolate subset-managed tags from standard category limits
            if norm_tag in subset_managed_tags:
                subset_records.append(record)
            else:
                standard_records_by_category[category].append(record)

    # apply category limits
    for category, cat_records in standard_records_by_category.items():
        limit = output_filter.max_tags_per_category.get(category)
        if limit is not None:
            # sort descending by score, keep top-N
            cat_records.sort(key=lambda r: r.score, reverse=True)
            cat_records = cat_records[:limit]
        records.extend(cat_records)

    # apply subset limits to isolated tags
    for group in output_filter.max_tags_per_subset:
        group_tags_set = {_norm(t) for t in group.tags}
        matching_subset_records = [r for r in subset_records if _norm(r.raw_tag) in group_tags_set]

        if len(matching_subset_records) > group.limit:
            matching_subset_records.sort(key=lambda r: r.score, reverse=True)
            matching_subset_records = matching_subset_records[: group.limit]

        records.extend(matching_subset_records)

        # Remove processed records to avoid double-evaluation
        subset_records = [r for r in subset_records if r not in matching_subset_records]

    # add any fallback subset records that didn't match a defined subset group
    records.extend(subset_records)

    return records


# endregion


# region Processor chain


def resolve_effective_thresholds(
    session: vibe.ModelSession,
    output_filter: OutputFilterConfig,
) -> tuple[dict[str, float], float]:
    """
    Resolve exact, unambiguous per-tag thresholds honoring precedence,
    relative offsets, override_tlt, and category/tag-level fallbacks.
    """
    use_tlt = output_filter.prefer_tag_level_thresholds and TagLevelThresholds.is_supported(session)
    base_calibrated = dict(session.tagger.thresholds.values) if (use_tlt and session.tagger.thresholds) else {}

    rel_scale = 1.0 + output_filter.tag_level_threshold_relative_offset

    # Normalized user configuration lookups
    tag_overrides = {_norm(k): v for k, v in output_filter.tag_thresholds.items()}
    cat_overrides = output_filter.category_thresholds

    final_map: dict[str, float] = {}

    if session.tagger.catalog:
        for info in session.tagger.catalog.labels:
            raw_name = info.name
            norm_name = _norm(raw_name)
            cat_name = info.category

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


# endregion


# region Run statistics


@dataclass
class PhaseStats:
    """Counters for one phase (infer or push) of one model."""

    model_id: str
    ok: int = 0
    errors: int = 0
    skipped: int = 0
    aborted_early: bool = False
    abort_reason: str = ""

    # inference-specific
    total_tags_cached: int = 0
    total_tags_enqueued: int = 0

    # push-specific (preserved for API compatibility with CLI summary)
    push_ok: int = 0
    total_tags_pushed: int = 0
    push_errors: int = 0
    hydrus_suspended: bool = False


# endregion


# region P1: Inference


async def infer_files(
    model_cfg: ModelConfig,
    file_infos: list[FileInfo],
    *,
    config: AppConfig,
    db: Database,
    progress: Progress,
    force: bool,
    run_id: str | None = None,
    hydrus: HydrusClient | None = None,
) -> PhaseStats:
    """
    Run inference for one model against all eligible files.

    Saves raw predictions to inference_cache in the database and enqueues
    the resulting tags into push_queue for asynchronous or trailing pushing.
    """
    del run_id, hydrus  # Retained in signature for compatibility; pushing is handled via push_queue
    stats = PhaseStats(model_id=model_cfg.model_id)

    # Build file source from the pre-resolved local paths.
    source = LocalFileSource(file_infos)

    # Determine which files to process.
    all_hashes = [fi.file_hash for fi in file_infos]
    file_info_map: dict[str, FileInfo] = {fi.file_hash: fi for fi in file_infos}

    if force:
        to_process = list(file_infos)
        logger.info("--force: re-inferring all %d files for %s", len(to_process), model_cfg.model_id)
    else:
        already_inferred = db.bulk_already_inferred(all_hashes, model_cfg.model_id)
        to_process = [fi for fi in file_infos if fi.file_hash not in already_inferred]
        stats.skipped = len(already_inferred)
        if already_inferred:
            progress.tick(skipped=len(already_inferred))
        logger.info(
            "Model %s: %d to infer, %d skipped (already cached)",
            model_cfg.model_id,
            len(to_process),
            stats.skipped,
        )

    # Drop files with no local path or missing files
    valid_process: list[FileInfo] = []
    for fi in to_process:
        if not source.has_path(fi.file_hash):
            logger.warning("Model %s: %s has no local path, marking missing", model_cfg.model_id, fi.file_hash[:8])
            db.upsert_file(fi.file_hash, mime=fi.mime, file_path=None, status="missing_on_disk")
            progress.tick(errors=1)
            stats.errors += 1
        else:
            valid_process.append(fi)

    if not valid_process:
        return stats

    print()
    print(_c("  Press 'q' to cancel current model", MAGENTA, BOLD))
    print()
    if stats.skipped != 0:
        print(f"  {stats.skipped} items already cached")
        print()

    eff_filter = config.resolved_output_filter(model_cfg)
    output_services = config.resolved_output_tag_services(model_cfg)
    configured_model_ids = [m.model_id for m in config.inference.models]

    # Build inputs as (path, file_hash) tuples for inference backend.
    inputs = [(source.get_input(fi.file_hash), fi.file_hash) for fi in valid_process]

    load_kwargs: dict[str, Any] = {
        "source": model_cfg.source,
        "device": model_cfg.device,
        "precision": model_cfg.precision,
    }
    if model_cfg.backend is not None:
        load_kwargs["backend"] = model_cfg.backend

    consecutive_errors = 0
    listener = None
    stop_cancel = threading.Event()

    try:
        with vibe.load(model_cfg.model_id, **load_kwargs) as session:
            progress.reset_start_time()
            pipeline = build_transform_pipeline(session, eff_filter)

            listener, stop_cancel = _start_cancel_listener(session, model_cfg.model_id)

            async for chunk in session.infer_async(inputs, batch_size=model_cfg.batch_size):
                batch_processed = 0
                batch_errors = 0
                last_file_hash = None
                last_tag_count = 0

                for item in chunk:
                    file_hash: str = str(item.input_ref)
                    fi = file_info_map[file_hash]

                    try:
                        raw_result = item.result
                        if not isinstance(raw_result, TagResult):
                            raise TypeError(f"Expected TagResult from tagger model, got {type(raw_result).__name__}")

                        # 1. Upsert known_file FIRST to satisfy foreign key constraint!
                        db.upsert_file(file_hash, file_path=fi.local_path, mime=fi.mime, status="active")

                        # 2. Save un-culled raw predictions to SQLite cache (if enabled)
                        db.save_raw_cache(
                            file_hash,
                            model_cfg.model_id,
                            raw_result.to_dict(),
                            enabled=config.database.cache_raw_predictions,
                        )

                        # 3. Apply VRT transform pipeline (thresholds + clean tags)
                        filtered_result = pipeline(raw_result)

                        # 4. Extract final tags (prefixes, subset limits, and inclusions/exclusions)
                        tag_records = extract_tags(filtered_result, output_filter=eff_filter)
                        prefixed_tags = [tr.prefixed_tag for tr in tag_records]

                        # 5. Enqueue tags into push_queue for each configured service (merges tags automatically)
                        for svc_key in output_services:
                            db.enqueue_push(file_hash, svc_key, prefixed_tags, action="add_tags")

                        # 6. Check if all models configured for this file are complete using has_raw_cache
                        all_models_cached = True
                        for m_id in configured_model_ids:
                            if m_id == model_cfg.model_id:
                                continue
                            if not db.has_raw_cache(file_hash, m_id):
                                all_models_cached = False
                                break

                        if all_models_cached:
                            if config.hydrus.add_tags:
                                a_cfg = config.hydrus.add_tags
                                for svc_key in a_cfg.tag_service_keys:
                                    db.enqueue_push(file_hash, svc_key, a_cfg.tags, action="add_tags")
                            if config.hydrus.remove_tags:
                                r_cfg = config.hydrus.remove_tags
                                for svc_key in r_cfg.tag_service_keys:
                                    db.enqueue_push(file_hash, svc_key, r_cfg.tags, action="delete_tags")

                        # Periodic commit to prevent disk sync thrashing
                        db.batch_tick(batch_size=50)

                        consecutive_errors = 0
                        stats.ok += 1
                        stats.total_tags_cached += len(raw_result.tags)
                        stats.total_tags_enqueued += len(prefixed_tags)
                        batch_processed += 1
                        last_file_hash = file_hash
                        last_tag_count = len(prefixed_tags)

                    except Exception as exc:
                        _handle_infer_error(
                            exc=exc,
                            file_hash=file_hash,
                            fi=fi,
                            db=db,
                            stats=stats,
                        )
                        consecutive_errors += 1
                        batch_errors += 1
                        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            stats.aborted_early = True
                            stats.abort_reason = f"Aborted: {consecutive_errors} consecutive errors (last: {exc})"
                            return stats
                        continue

                if last_file_hash is not None:
                    progress.set_last_file_info(last_file_hash, model_cfg.model_id, last_tag_count)
                if batch_processed or batch_errors:
                    progress.tick(processed=batch_processed, errors=batch_errors)

            # Flush any uncommitted transactions from this run
            db.commit()

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
    logger.error("Inference error for %s: %s", file_hash[:8], msg)
    try:
        db.upsert_file(file_hash, mime=fi.mime, file_path=fi.local_path, status="error")
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
