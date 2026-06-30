"""
inference.py Inference orchestration.

infer_files()             Run a model against files, store results in DB.
                          Does NOT require Hydrus to be reachable.
                          If Hydrus is reachable, pushes results (interleaved) and performs tag cleanups.

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
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

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

# region Tag extraction


@dataclass
class TagRecord:
    """One tag destined for Hydrus, with its provenance."""

    category: str
    raw_tag: str
    prefixed_tag: str
    score: float


def extract_tags(
    result_dict: dict[str, Any],
    *,
    output_filter: OutputFilterConfig,
) -> list[TagRecord]:
    """
    Convert a TagResult.to_dict() payload into TagRecord objects.

    result_dict["tags"] has the form:
        { category: { tag_name: score, ... }, ... }
    """
    tags_by_category: dict[str, dict[str, float]] = result_dict.get("tags", {})
    records: list[TagRecord] = []

    categories = output_filter.output_categories
    # sets for fast lookups
    include_set = set(output_filter.include_tags)
    exclude_set = set(output_filter.exclude_tags)

    # set of all tags governed by custom subset limits
    subset_managed_tags = set()
    for group in output_filter.max_tags_per_subset:
        subset_managed_tags.update(group.tags)

    standard_records_by_category: dict[str, list[TagRecord]] = {}
    subset_records: list[TagRecord] = []

    for category, tag_scores in tags_by_category.items():
        category_prefix = output_filter.category_tag_prefix_mapping.get(category) or ""
        standard_records_by_category[category] = []

        for raw_tag, score in tag_scores.items():
            # 1. Check Exclusions: Drop if explicitly excluded
            if raw_tag in exclude_set:
                continue

            # 2. Check Overrides: Always keep if explicitly included
            is_allowed = raw_tag in include_set

            # 3. Check Categories: If not explicitly included, fallback to standard category checks
            if not is_allowed:
                if not categories or category not in categories:
                    continue

            # Determine effective prefix (individual override takes precedence)
            prefix = output_filter.tag_prefix_overrides.get(raw_tag, category_prefix)
            record = TagRecord(
                category=category,
                raw_tag=raw_tag,
                prefixed_tag=f"{prefix}{raw_tag}",
                score=float(score),
            )

            # Isolate subset-managed tags from standard category limits
            if raw_tag in subset_managed_tags:
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
        group_tags_set = set(group.tags)
        matching_subset_records = [r for r in subset_records if r.raw_tag in group_tags_set]

        if len(matching_subset_records) > group.limit:
            matching_subset_records.sort(key=lambda r: r.score, reverse=True)
            matching_subset_records = matching_subset_records[: group.limit]

        records.extend(matching_subset_records)

        # Remove processed records to avoid double-evaluation
        subset_records = [r for r in subset_records if r not in matching_subset_records]

    # add any fallback subset records that didn't match a defined subset group
    records.extend(subset_records)

    return records


# region Processor chain


def build_result_processors(
    model_id: str,
    *,
    prefer_tag_level_thresholds: bool,
    tlt_relative_offset: float,
    default_threshold: float,
    category_thresholds: dict[str, float],
    output_filter: OutputFilterConfig | None = None,
) -> list[Any]:
    """
    Build the result processor list for one model session.
    """
    import vibe
    from vibe.result_processors import CleanTags, ScoreThresholds, TagLevelThresholds

    processors: list[Any] = []

    use_tlt = False
    if prefer_tag_level_thresholds:
        try:
            model_info = vibe.describe(model_id)
            if TagLevelThresholds.__name__ in model_info.supported_processors:
                use_tlt = True
            else:
                logger.info(
                    "Model %s does not support TagLevelThresholds; falling back to ScoreThresholds",
                    model_id,
                )
        except Exception as exc:
            logger.warning(
                "Could not determine processor support for %s (%s); falling back to ScoreThresholds",
                model_id,
                exc,
            )

    if use_tlt:
        tlt_kwargs: dict[str, Any] = {"threshold_relative_offset": tlt_relative_offset}
        if output_filter is not None:
            cat_overrides = {
                cat: cfg.threshold for cat, cfg in output_filter.category_thresholds.items() if cfg.override_tlt
            }
            tag_overrides = {
                tag: cfg.threshold for tag, cfg in output_filter.tag_thresholds.items() if cfg.override_tlt
            }

            try:
                import inspect

                sig = inspect.signature(TagLevelThresholds.__init__)
                supported_params = list(sig.parameters.keys())
            except Exception:
                supported_params = []

            # Find matching parameters
            cat_param_name = next((p for p in supported_params if "category" in p), None)
            tag_param_name = next((p for p in supported_params if "tag" in p), None)

            if cat_overrides:
                if cat_param_name:
                    tlt_kwargs[cat_param_name] = cat_overrides
                else:
                    logger.warning(
                        "Model %s: TagLevelThresholds does not support category overrides; override_tlt has no effect",
                        model_id,
                    )

            if tag_overrides:
                if tag_param_name:
                    tlt_kwargs[tag_param_name] = tag_overrides
                else:
                    logger.warning(
                        "Model %s: TagLevelThresholds does not support tag overrides; override_tlt has no effect",
                        model_id,
                    )

        processors.append(TagLevelThresholds(**tlt_kwargs))
        logger.info("Model %s: using TagLevelThresholds(relative_offset=%.2f)", model_id, tlt_relative_offset)
    else:
        kwargs: dict[str, Any] = {"threshold": default_threshold}
        if category_thresholds:
            kwargs["category_thresholds"] = category_thresholds
        processors.append(ScoreThresholds(**kwargs))
        logger.info(
            "Model %s: using ScoreThresholds(threshold=%.2f, category_overrides=%s)",
            model_id,
            default_threshold,
            category_thresholds or "{}",
        )

    processors.append(CleanTags())
    return processors


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

    # push-specific (interleaved)
    push_ok: int = 0
    total_tags_pushed: int = 0
    push_errors: int = 0
    hydrus_suspended: bool = False  # True if push was cut short mid-inference


# region P1: Inference


async def infer_files(
    model_cfg: ModelConfig,
    file_infos: list[FileInfo],
    *,
    config: AppConfig,
    db: Database,
    progress: Progress,
    run_id: str,
    force: bool,
    hydrus: "HydrusClient | None" = None,
) -> PhaseStats:
    """
    Run inference for one model against all eligible files.

    Saves results to inference_cache and file_model_results (infer_success).

    If `hydrus` is provided, tags are pushed to Hydrus immediately after each
    file is inferred (interleaved) using the shared push_service.

    If `hydrus` is None (--infer-only), no push is attempted at all.
    """
    import vibe
    from vibe.session import InferenceCancelled

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

    # Drop files with no local path.
    no_path = [fi for fi in to_process if not source.has_path(fi.file_hash)]
    if no_path:
        logger.warning("Model %s: %d files have no local path, skipping", model_cfg.model_id, len(no_path))
        for fi in no_path:
            db.upsert_known_file(fi.file_hash, mime=fi.mime, file_path=None)
            db.record_infer_result(
                run_id=run_id,
                file_hash=fi.file_hash,
                model_id=model_cfg.model_id,
                success=False,
                duration_ms=0,
                error_message="No local file path available",
            )
            db.commit()
            progress.tick(errors=1)
            stats.errors += 1
    to_process = [fi for fi in to_process if source.has_path(fi.file_hash)]

    if not to_process:
        return stats

    print()
    print(_c("  Press 'q' to cancel current model", MAGENTA, BOLD))
    print()
    if stats.skipped != 0:
        print(f"  {stats.skipped} items already cached")
        print()

    eff = config.resolved_output_filter(model_cfg)
    processors = build_result_processors(
        model_cfg.model_id,
        prefer_tag_level_thresholds=eff.prefer_tag_level_thresholds,
        tlt_relative_offset=eff.tag_level_threshold_relative_offset,
        default_threshold=eff.default_threshold,
        category_thresholds={cat: cfg.threshold for cat, cfg in eff.category_thresholds.items()},
        output_filter=eff,
    )

    # Build inputs as (path_or_bytes, file_hash) tuples for inference backend.
    inputs = [(source.get_input(fi.file_hash), fi.file_hash) for fi in to_process]

    load_kwargs: dict[str, Any] = {
        "source": model_cfg.source,
        "device": model_cfg.device,
        "precision": model_cfg.precision,
    }
    if model_cfg.backend is not None:
        load_kwargs["backend"] = model_cfg.backend

    consecutive_errors = 0
    # Tracks whether Hydrus is still reachable. Flipped to False on first push
    # error; stays False for the remainder of inference to avoid spam.
    hydrus_reachable = hydrus is not None

    listener = None
    stop_cancel = threading.Event()

    try:
        with vibe.load(model_cfg.model_id, **load_kwargs) as session:
            progress.reset_start_time()

            listener, stop_cancel = _start_cancel_listener(session, model_cfg.model_id)
            async for chunk in session.infer_async(
                inputs,
                batch_size=model_cfg.batch_size,
                result_processors=processors,
            ):
                batch_processed = 0
                batch_errors = 0
                last_file_hash = None
                last_tag_count = 0

                for item in chunk:
                    file_hash: str = item.input_ref
                    fi = file_info_map[file_hash]
                    t_start = time.monotonic()

                    try:
                        result_dict: dict[str, Any] = item.result.to_dict()
                    except Exception as exc:
                        _handle_infer_error(
                            exc=exc,
                            file_hash=file_hash,
                            model_id=model_cfg.model_id,
                            run_id=run_id,
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

                    duration_ms = int((time.monotonic() - t_start) * 1000)

                    # Count tags that will be available for pushing.
                    tag_records = extract_tags(result_dict, output_filter=eff)

                    try:
                        db.upsert_known_file(file_hash, mime=fi.mime, file_path=fi.local_path)
                        db.save_inference_cache(file_hash, model_cfg.model_id, run_id, result_dict)
                        db.record_infer_result(
                            run_id=run_id,
                            file_hash=file_hash,
                            model_id=model_cfg.model_id,
                            success=True,
                            duration_ms=duration_ms,
                        )
                        db.commit()
                    except Exception as exc:
                        logger.error("DB write failed for %s: %s", file_hash[:8], exc)

                    # region Interleaved push
                    if hydrus_reachable:
                        assert hydrus is not None
                        prefixed_tags = [tr.prefixed_tag for tr in tag_records]

                        # Delegate pushing entirely to push_service module
                        from hyvis.push_service import push_and_cleanup_file

                        success = push_and_cleanup_file(
                            db=db,
                            hydrus=hydrus,
                            config=config,
                            model_cfg=model_cfg,
                            file_hash=file_hash,
                            prefixed_tags=prefixed_tags,
                        )
                        if success:
                            stats.push_ok += 1
                            stats.total_tags_pushed += len(prefixed_tags)
                        else:
                            hydrus_reachable = False
                            stats.push_errors += 1

                    consecutive_errors = 0
                    stats.ok += 1
                    stats.total_tags_cached += len(tag_records)

                    batch_processed += 1
                    last_file_hash = file_hash
                    last_tag_count = len(tag_records)

                if last_file_hash is not None:
                    progress.set_last_file_info(last_file_hash, model_cfg.model_id, last_tag_count)
                if batch_processed or batch_errors:
                    progress.tick(processed=batch_processed, errors=batch_errors)

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

    # Record whether Hydrus became unreachable mid-run so the caller can
    # decide to do a backlog push pass.
    if hydrus is not None and not hydrus_reachable:
        stats.hydrus_suspended = True

    return stats


# region Helpers


def _handle_infer_error(
    *,
    exc: Exception,
    file_hash: str,
    model_id: str,
    run_id: str,
    fi: FileInfo,
    db: Database,
    stats: PhaseStats,
) -> None:
    msg = str(exc)
    logger.error("Inference error for %s: %s", file_hash[:8], msg)
    try:
        db.upsert_known_file(file_hash, mime=fi.mime, file_path=fi.local_path)
        db.record_infer_result(
            run_id=run_id,
            file_hash=file_hash,
            model_id=model_id,
            success=False,
            duration_ms=0,
            error_message=msg[:500],
        )
        db.commit()
    except Exception as db_exc:
        logger.error("Failed to record error for %s in DB: %s", file_hash[:8], db_exc)
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
        old_attrs = termios.tcgetattr(fd)
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
