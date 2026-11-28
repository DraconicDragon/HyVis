from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from collections import deque
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import vibe
from vibe import InferenceCancelled, TagResult
from vibe.transforms import TagLevelThresholds

from hyvis.db import CacheDatabase, FileStatus
from hyvis.logging_utils import BOLD, GREEN, MAGENTA, _c

if TYPE_CHECKING:
    from hyvis.config import AppConfig, ModelConfig
    from hyvis.db import Database
    from hyvis.hydrus import FileInfo
    from hyvis.progress import Progress

from hyvis.inference.cancel import start_cancel_listener
from hyvis.inference.planner import (
    ModelPassPlan,
    compute_filter_hash,
    is_known_multiframe,
    plan_model_pass,
)
from hyvis.tag_filter import (
    build_transform_pipeline,
    build_transform_pipeline_from_vocab,
    compile_output_filter,
    extract_tags,
)

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
    is_multiframe: bool = False,
    video_filter_hashes: Mapping[str, str] | None = None,
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
        if is_multiframe and video_filter_hashes and staged_m_id in video_filter_hashes:
            fh = video_filter_hashes[staged_m_id]
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
    video_filter_hashes = {
        m.model_id: compute_filter_hash(config, m, video_prep_hash=plan.video_prep_hash)
        for m in config.inference.models
    }

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
            has_video_adjust = config.video.enabled and config.video.threshold_adjust_value != 0.0
            video_cpu_pipeline = (
                build_transform_pipeline_from_vocab(vocab, eff_filter, video_cfg=config.video)
                if has_video_adjust and any(is_known_multiframe(fi) for fi in plan.refilter_files)
                else cpu_pipeline
            )

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
                    active_pipeline = video_cpu_pipeline if is_known_multiframe(fi) else cpu_pipeline
                    filtered_result = active_pipeline(raw_result)
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
                        is_multiframe=is_known_multiframe(fi),
                        video_filter_hashes=video_filter_hashes,
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
    from hyvis.video.extractor import build_video_plan, is_animated_image, iter_extract_frames, probe_media

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
            has_video_adjust = config.video.enabled and config.video.threshold_adjust_value != 0.0
            video_pipeline = (
                build_transform_pipeline(session, eff_filter, video_cfg=config.video)
                if has_video_adjust and multiframe_files
                else pipeline
            )

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

            listener, stop_cancel = start_cancel_listener(session, model_cfg.model_id)

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

                active_pipeline = video_pipeline if is_known_multiframe(fi) else pipeline
                filtered_result = active_pipeline(raw_result)
                tag_records = extract_tags(filtered_result, output_filter=compiled_filter)
                prefixed_tags = [tr.prefixed_tag for tr in tag_records]
                if not prefixed_tags:
                    stats.zero_tags += 1

                is_final = _finalize_file(
                    file_hash=fi.file_hash,
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
                    is_multiframe=is_known_multiframe(fi),
                    video_filter_hashes=video_filter_hashes,
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
                    FrameDiskCache(
                        resolve_frame_cache_dir(config, db_path=getattr(db, "_path", None)),
                        image_format=config.video.frame_cache_format,
                        quality=config.video.frame_cache_quality,
                        effort=config.video.frame_cache_effort,
                    )
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
                            f_plan = build_video_plan(
                                probe,
                                config=plan_cfg,
                                extract_keyframes=config.video.extract_keyframes,
                                max_keyframes=config.video.max_keyframes,
                            )
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
                            if not isinstance(item.input_ref, tuple):
                                raise TypeError(
                                    f"Pipeline desync: expected (file_hash, frame_idx, expected_n) tuple, got {type(item.input_ref).__name__}"
                                )

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
                    if total_frames_inferred > 0:
                        fr_elapsed = max(time.monotonic() - multiframe_t0, 1e-3)
                        logger.debug(
                            "Multi-frame pass for '%s': inferred %d frame(s) across %d file(s) in %.1fs (%.1f fr/s).",
                            model_cfg.model_id,
                            total_frames_inferred,
                            len(multiframe_files),
                            fr_elapsed,
                            total_frames_inferred / fr_elapsed,
                        )
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


# endregion
