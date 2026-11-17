"""
main.py Entry point.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import signal
import sys
from pathlib import Path
from typing import Any

import tomli_w

from hyvis.bg_imports import start_imports, wait_for_imports
from hyvis.cli import get_version, parse_args
from hyvis.cli_display import connect_hydrus, print_confirmation, print_run_summary
from hyvis.config import AppConfig
from hyvis.db import (
    CacheDatabase,
    Database,
    DryRunDatabase,
    RunMode,
    RunTargetState,
    default_cache_path,
    hash_api_key,
)
from hyvis.hydrus import FileInfo, HydrusConnectionError, HydrusError, validate_service_keys
from hyvis.logging_utils import (  # noqa: F401
    BOLD,
    CYAN,
    DIM,
    GREEN,
    MAGENTA,
    RED,
    RESET,
    YELLOW,
    ColorFormatter,
    _c,
    setup_logging,
)
from hyvis.progress import Progress, clear_line, inline_progress

logger = logging.getLogger(__name__)


def _resolve_db_path(raw_path: str | Path) -> Path:
    p = Path(raw_path)
    return p if p.is_absolute() else (Path.cwd() / p)


def _clear_video_frame_cache(cfg: AppConfig, db_path: Path) -> int:
    """Remove temporary extracted video frames and report how many folders were removed."""
    from hyvis.video.frame_cache import FrameDiskCache, resolve_frame_cache_dir

    removed = FrameDiskCache(resolve_frame_cache_dir(cfg, db_path=db_path)).clear_all()
    if removed > 0:
        print(_c(f"  Removed {removed} temporary video frame cache folder(s).", DIM))
    return removed


def _apply_cli_overrides(cfg: AppConfig, args: Any) -> AppConfig:
    if args.api_url or args.api_key:
        hydrus_updates: dict[str, str] = {}
        if args.api_url:
            hydrus_updates["api_url"] = args.api_url.rstrip("/")
        if args.api_key:
            hydrus_updates["api_key"] = args.api_key
        new_hydrus = cfg.hydrus.model_copy(update=hydrus_updates)
        cfg = cfg.model_copy(update={"hydrus": new_hydrus})

    if args.db is not None or args.cache_db is not None:
        db_updates: dict[str, Any] = {}
        if args.db is not None:
            db_updates["path"] = str(args.db)
        if args.cache_db is not None:
            db_updates["cache_path"] = str(args.cache_db)
        new_db = cfg.database.model_copy(update=db_updates)
        cfg = cfg.model_copy(update={"database": new_db})

    return cfg


def _load_config_or_error(config_path: Path) -> AppConfig | None:
    if not config_path.exists():
        print(_c(f"ERROR: Config file not found: {config_path}", RED), file=sys.stderr)
        return None
    try:
        return AppConfig.from_file(config_path)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        print(_c(f"ERROR: Failed to parse config: {exc}", RED), file=sys.stderr)
        return None


def _verify_client_binding(
    db: Database,
    cfg: AppConfig,
    service_name_by_key: dict[str, str],
    args: Any,
) -> bool:
    """
    Verify that the connected Hydrus client matches the fingerprint stored in `hyvis.db`.
    Prevents accidentally mixing `inference_log` / `push_queue` across different Hydrus clients.
    Returns True if safe to proceed, False if aborted/blocked.
    """
    cur_url = cfg.hydrus.api_url.strip().rstrip("/")
    cur_key_hash = hash_api_key(cfg.hydrus.api_key)
    cur_services = frozenset(service_name_by_key.keys())

    saved = db.get_client_binding()
    if saved is None:
        if not args.dry_run:
            db.set_client_binding(cur_url, cur_key_hash, cur_services)
        return True

    url_changed = saved.api_url != cur_url
    key_changed = saved.api_key_hash != cur_key_hash
    removed_services = saved.service_keys - cur_services
    added_services = cur_services - saved.service_keys

    # Same URL & API key, and only new services were added -> update seamlessly
    if not url_changed and not key_changed and not removed_services:
        if added_services and not args.dry_run:
            logger.info("Detected %d new Hydrus service(s); updating stored client binding.", len(added_services))
            db.set_client_binding(cur_url, cur_key_hash, cur_services)
        return True

    # Mismatch detected: warn before any writes occur
    print()
    print(_c("  ⚠ WARNING: Hydrus Client Mismatch Detected", YELLOW, BOLD))
    print("  This database (hyvis.db) was previously used with a different Hydrus client or configuration:")
    if url_changed:
        print(f"    • API URL   : {saved.api_url}  ->  {cur_url}")
    if key_changed:
        print("    • API Key   : changed (different Hydrus client or rotated key)")
    if removed_services or added_services:
        print(f"    • Services  : {len(removed_services)} missing from client, {len(added_services)} new")
        for missing_k in sorted(removed_services)[:5]:
            print(_c(f"        - missing service key: {missing_k}", DIM))
        if len(removed_services) > 5:
            print(_c(f"        ... and {len(removed_services) - 5} more", DIM))

    pending_pushes = db.get_pending_push_count()
    if pending_pushes > 0:
        print(
            _c(
                f"  ⚠ push_queue currently has {pending_pushes} unpushed file(s) from the previous client!",
                RED,
                BOLD,
            )
        )

    print()
    print(
        _c(
            "  Tip: Sharing one hyvis.db across different Hydrus clients will skip files or fail pushes.\n"
            "       Use a separate --db path (e.g. '--db data/test.db') while keeping '--cache-db data/cache.db'\n"
            "       to safely reuse your raw prediction cache.",
            CYAN,
        )
    )
    print()

    if args.update_client_binding:
        if not args.dry_run:
            db.set_client_binding(cur_url, cur_key_hash, cur_services)
            logger.info("Updated Hydrus client binding in hyvis.db via --update-client-binding.")
        return True

    if args.yes or args.dry_run:
        print(
            _c(
                "ERROR: Aborted due to Hydrus client mismatch. "
                "Use a separate --db path, or pass --update-client-binding if this is the same Hydrus client.",
                RED,
            ),
            file=sys.stderr,
        )
        return False

    try:
        ans = input("  Update stored client binding to this client and continue? [y/N]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print(_c("\nAborted.", YELLOW))
        return False

    if ans in ("y", "yes"):
        db.set_client_binding(cur_url, cur_key_hash, cur_services)
        logger.info("Updated Hydrus client binding in hyvis.db after user confirmation.")
        return True

    print(_c("\nAborted.", YELLOW))
    return False


# region Main coroutine


async def main() -> int:
    args = parse_args()

    # Print invocation CLI arguments immediately
    if len(sys.argv) > 1:
        cmd_args = " ".join(shlex.quote(arg) for arg in sys.argv[1:])
        print(_c(f"Executing HyVis with arguments: {cmd_args}\n", DIM))

    is_resume = bool(args.resume)
    effective_force = bool(args.force)
    cfg: AppConfig | None = None

    # 1. Load initial config / handle --resume or interrupted active_run detection
    if is_resume:
        if args.db is not None:
            db_path = _resolve_db_path(args.db)
        elif args.config is not None:
            probe_cfg = _load_config_or_error(args.config)
            if probe_cfg is None:
                return 1
            db_path = _resolve_db_path(probe_cfg.database.path)
        else:
            db_path = _resolve_db_path("data/hyvis.db")

        if not db_path.is_file():
            print(_c(f"ERROR: Database not found at {db_path}; nothing to resume.", RED), file=sys.stderr)
            return 1

        with Database(db_path) as db:
            active = db.get_active_run()

        if active is None:
            print(_c(f"No unfinished run found in {db_path}. Nothing to resume.", YELLOW))
            return 0

        cfg = AppConfig.from_toml_string(active.config_toml)
        cfg = _apply_cli_overrides(cfg, args)
        effective_force = active.force_mode
        if not args.infer_only and active.mode == RunMode.INFER_ONLY:
            args.infer_only = True
    else:
        assert args.config is not None
        cfg = _load_config_or_error(args.config)
        if cfg is None:
            return 1
        cfg = _apply_cli_overrides(cfg, args)
        db_path = _resolve_db_path(cfg.database.path)

        # Check for interrupted active_run before proceeding with a normal run
        if not args.dry_run and not args.clear_cache and not args.push_only and db_path.is_file():
            with Database(db_path) as db:
                active = db.get_active_run()
                if active is not None:
                    pending_n = len(db.get_run_targets(RunTargetState.PENDING))
                    if args.discard_run:
                        db.clear_active_run()
                        _clear_video_frame_cache(cfg, db_path)
                        print(_c(f"  Discarded previous unfinished run ({pending_n} pending file(s)).\n", YELLOW))
                    elif args.yes:
                        print(
                            _c(
                                f"ERROR: An unfinished run from {active.started_at} ({pending_n} pending file(s)) "
                                f"exists in {db_path}.",
                                RED,
                            ),
                            file=sys.stderr,
                        )
                        print(
                            "  Pass --resume to finish it, or --discard-run to abandon it and start fresh.",
                            file=sys.stderr,
                        )
                        return 1
                    else:
                        print(
                            _c(
                                f"  Found unfinished run from {active.started_at} ({pending_n} pending file(s)).",
                                YELLOW,
                            )
                        )
                        try:
                            ans = (
                                input("  [R]esume previous run / [D]iscard & start new / [A]bort [r/d/A]: ")
                                .strip()
                                .lower()
                            )
                        except (KeyboardInterrupt, EOFError):
                            print(_c("\nAborted.", YELLOW))
                            return 0

                        if ans in ("d", "discard"):
                            db.clear_active_run()
                            _clear_video_frame_cache(cfg, db_path)
                            print(_c("  Discarded previous run.\n", YELLOW))
                        elif ans in ("r", "resume"):
                            is_resume = True
                            cfg = AppConfig.from_toml_string(active.config_toml)
                            cfg = _apply_cli_overrides(cfg, args)
                            effective_force = active.force_mode
                            if not args.infer_only and active.mode == RunMode.INFER_ONLY:
                                args.infer_only = True
                            print(_c("  Switching to resume mode.\n", GREEN))
                        else:
                            print(_c("\nAborted.", YELLOW))
                            return 0

    assert cfg is not None
    explicit_cache_override_note = args.db is not None and args.cache_db is None and bool(cfg.database.cache_path)
    cache_path = _resolve_db_path(cfg.database.cache_path) if cfg.database.cache_path else default_cache_path(db_path)
    if explicit_cache_override_note:
        print(
            _c(
                f"  Note: --db overrode main DB to '{db_path}', while cache.db remains '{cache_path}' "
                "from config (pass --cache-db to override).",
                YELLOW,
            )
        )

    # region Logging Setup
    effective_log_level = args.log_level or cfg.hyvis.log_level
    log_file_path = setup_logging(
        effective_log_level,
        log_dir=cfg.hyvis.log_dir,
        max_log_files=cfg.hyvis.max_log_files,
        config_path=args.config,
    )
    if log_file_path:
        logger.debug("Session logging active at: %s", log_file_path)
    # endregion

    from hyvis.extra_hashes import load_extra_hashes

    effective_infer_only = args.infer_only or cfg.hyvis.infer_only
    effective_no_wait = args.no_wait or cfg.hydrus.no_wait

    if args.dry_run:
        mode = "dry_run"
    elif effective_infer_only:
        mode = "infer_only"
    else:
        mode = "default"

    db_cls = DryRunDatabase if args.dry_run else Database

    # region Early Action: Clear Cache
    if args.clear_cache:
        logger.info("Executing action: --clear-cache on %s", cache_path)

        if not cache_path.is_file():
            removed_fc = _clear_video_frame_cache(cfg, db_path)
            if removed_fc == 0:
                print("Inference cache is already empty (cache.db does not exist).")
            return 0

        with CacheDatabase(cache_path) as cdb:
            stats_info = cdb.get_stats()
            count = int(stats_info["predictions"])
            if count == 0:
                removed_fc = _clear_video_frame_cache(cfg, db_path)
                if removed_fc == 0:
                    print("Inference cache is already empty.")
                    logger.info("Inference cache is already empty.")
                return 0

            size_mb = stats_info["size_bytes"] / (1024 * 1024)
            print(_c(f"  Warning: This will delete {count} cached prediction record(s) ({size_mb:.1f} MB).", YELLOW))
            print("  Re-running models on uncached filter changes will require model inference.")
            if not args.yes:
                try:
                    ans = input("  Proceed? [y/N]: ").strip().lower()
                    if ans not in ("y", "yes"):
                        print(_c("\nAborted.", YELLOW))
                        logger.info("Cache clear aborted by user.")
                        return 0
                except (KeyboardInterrupt, EOFError):
                    print(_c("\n\nAborted.", YELLOW))
                    logger.info("Cache clear aborted by interrupt.")
                    return 0

            deleted = cdb.clear_cache()
            print(_c(f"\n  Successfully cleared {deleted} cached records and reclaimed disk space.", GREEN))
            logger.info("Cleared %d cached records from %s", deleted, cache_path)

            _clear_video_frame_cache(cfg, db_path)
            return 0

    # region Early Action: Push Only
    if args.push_only:
        logger.info("Executing action: --push-only on %s", db_path)
        with Database(db_path) as db:
            pending_count = db.get_pending_push_count()
            failed_count = db.get_failed_push_count()
            if pending_count == 0:
                print("Push queue is empty. All tags are already in Hydrus.")
                if failed_count > 0:
                    print(
                        _c(
                            f"  Note: {failed_count} dead-letter file(s) failed permanently after max attempts.",
                            YELLOW,
                        )
                    )
                logger.info("Push queue is empty. No operations needed.")
                return 0

            hydrus, service_name_by_key, _, _, _ = connect_hydrus(cfg)
            if not _verify_client_binding(db, cfg, service_name_by_key, args):
                return 1

            print()
            print(_c(f"  Pending push operations: {pending_count}", BOLD, CYAN))
            print()
            from hyvis.push_service import drain_push_queue

            push_progress = Progress(total=pending_count)
            push_progress.reset_start_time()
            ok, err = await drain_push_queue(
                db,
                hydrus,
                wait_for_hydrus=not effective_no_wait,
                progress=push_progress,
            )
            push_progress.finish()

            print()
            print(_c(f"  Push Complete: {ok} succeeded, {err} failed", BOLD, GREEN if err == 0 else YELLOW))
            logger.info("Push-only complete: %d succeeded, %d failed", ok, err)
            return 0 if err == 0 else 1

    # Validate config
    has_extra_hashes = args.extra_hash_file is not None
    errors = cfg.hyvis_validate(has_extra_hashes=has_extra_hashes or is_resume)
    if errors:
        print(_c("ERROR: Invalid configuration:", RED), file=sys.stderr)
        for e in errors:
            print(f"  • {e}", file=sys.stderr)
            logger.error("Configuration validation error: %s", e)
        return 1

    # region Collect Target Files (Resume vs Fresh Preflight)
    hydrus = None
    service_name_by_key: dict[str, str] = {}
    hydrus_version = "offline"
    api_version = "offline"
    boot_time = "offline"

    file_infos: list[FileInfo] = []
    rejected_mimes: set[str]
    all_rejected_hashes: list[str]
    tag_query_counts: list[int] = []
    page_query_counts: list[int] = []
    extra_count = 0

    if is_resume:
        with Database(db_path) as db:
            pending_targets = db.get_run_targets(RunTargetState.PENDING)
            rejected_targets = db.get_run_targets(RunTargetState.REJECTED)
            pending_hashes = [h for h, _, _ in pending_targets]
            cached_paths = db.bulk_get_known_paths(pending_hashes)
            cached_frames = db.bulk_get_known_frames(pending_hashes)

        for h, _, m in pending_targets:
            file_infos.append(
                FileInfo(
                    file_hash=h,
                    mime=m or "",
                    size=0,
                    local_path=cached_paths.get(h),
                    num_frames=cached_frames.get(h),
                )
            )

        all_rejected_hashes = [h for h, _, _ in rejected_targets]
        rejected_mimes = {m for _, _, m in rejected_targets if m}
        missing_paths = [fi for fi in file_infos if not fi.local_path or not Path(fi.local_path).is_file()]

        # Connect to Hydrus if needed for pushing (default mode) or resolving missing paths
        if mode == "default" or missing_paths:
            hydrus, service_name_by_key, hydrus_version, api_version, boot_time = connect_hydrus(cfg)
            logger.info("Connected to Hydrus v%s (API v%s) at %s", hydrus_version, api_version, cfg.hydrus.api_url)
            try:
                validate_service_keys(cfg, service_name_by_key)
            except HydrusError as e:
                print(_c(f"ERROR: {e}", RED), file=sys.stderr)
                return 1

            with Database(db_path) as db:
                if not _verify_client_binding(db, cfg, service_name_by_key, args):
                    return 1

            if missing_paths:
                assert hydrus is not None
                hydrus.resolve_paths(
                    missing_paths,
                    progress_callback=lambda d, t: inline_progress("Resolving missing paths", d, t),
                )
                clear_line()
                with Database(db_path) as db:
                    for fi in missing_paths:
                        if fi.local_path:
                            db.upsert_file(
                                fi.file_hash, file_path=fi.local_path, mime=fi.mime, num_frames=fi.num_frames
                            )

    else:
        hydrus, service_name_by_key, hydrus_version, api_version, boot_time = connect_hydrus(cfg)
        logger.info("Connected to Hydrus v%s (API v%s) at %s", hydrus_version, api_version, cfg.hydrus.api_url)

        try:
            validate_service_keys(cfg, service_name_by_key)
        except HydrusError as e:
            print(_c(f"ERROR: {e}", RED), file=sys.stderr)
            logger.error("Service key validation failed: %s", e)
            return 1

        with db_cls(db_path) as db:
            if not _verify_client_binding(db, cfg, service_name_by_key, args):
                return 1

        if cfg.hydrus.tag_queries and not any(q.tags for q in cfg.hydrus.tag_queries):
            print(
                _c(
                    "  Warning: all configured Hydrus tag queries are empty; "
                    + "only extra hashes or page queries can produce files.",
                    YELLOW,
                )
            )
            logger.warning("All configured tag queries are empty.")

        extra_hash_values: list[str] = []
        if args.extra_hash_file is not None:
            try:
                extra_hash_values = load_extra_hashes(args.extra_hash_file)
                logger.info("Loaded %d extra hash(es) from %s", len(extra_hash_values), args.extra_hash_file)
            except OSError as exc:
                print(_c(f"ERROR: Failed to read extra hash file: {exc}", RED), file=sys.stderr)
                logger.error("Failed to read extra hash file: %s", exc)
                return 1
            except ValueError as exc:
                print(_c(f"ERROR: Invalid extra hash file: {exc}", RED), file=sys.stderr)
                logger.error("Invalid extra hash file: %s", exc)
                return 1

        from hyvis.preflight import run_preflight

        try:
            preflight_data = run_preflight(
                cfg,
                hydrus=hydrus,
                extra_hashes=extra_hash_values,
                progress_callback=lambda stage, cur, tot: inline_progress(stage, cur or 0, tot or 1) if tot else None,
            )
        except HydrusConnectionError as exc:
            print(_c(f"\nERROR: Hydrus connection lost during preflight: {exc}", RED), file=sys.stderr)
            logger.error("Hydrus connection lost during preflight: %s", exc)
            return 1
        except HydrusError as exc:
            print(_c(f"\nERROR: Hydrus query failed: {exc}", RED), file=sys.stderr)
            logger.error("Hydrus query failed: %s", exc)
            return 1

        clear_line()

        file_infos = preflight_data.candidate_file_infos
        rejected_mimes = preflight_data.rejected_mimes
        all_rejected_hashes = preflight_data.rejected_hashes
        tag_query_counts = preflight_data.tag_query_counts
        page_query_counts = preflight_data.page_query_counts
        extra_count = len(extra_hash_values)

        logger.info(
            "Preflight complete: %d candidate files | %d MIME rejected (%s)",
            len(file_infos),
            len(all_rejected_hashes),
            ", ".join(sorted(rejected_mimes)) if rejected_mimes else "none",
        )

        if all_rejected_hashes:
            rejected_list = ", ".join(sorted(rejected_mimes)) if rejected_mimes else "unknown"
            print(_c(f"  Rejected {len(all_rejected_hashes)} files with unsupported MIME types: {rejected_list}", DIM))

        if not file_infos and not extra_count:
            print(_c("\nNo files matched the configured queries. Nothing to do.", YELLOW))
            logger.info("No candidate files matched queries. Exiting.")
            return 0

        # Resolve local paths and previously discovered frame counts using known_files cache first
        with db_cls(db_path) as db:
            all_cand_hashes = [fi.file_hash for fi in file_infos]
            cached_paths = db.bulk_get_known_paths(all_cand_hashes)
            cached_frames = db.bulk_get_known_frames(all_cand_hashes)

        files_to_resolve_online = []
        for fi in file_infos:
            if fi.num_frames is None and fi.file_hash in cached_frames:
                fi.num_frames = cached_frames[fi.file_hash]
            cached_p = cached_paths.get(fi.file_hash)
            if cached_p and Path(cached_p).is_file():
                fi.local_path = cached_p
            else:
                files_to_resolve_online.append(fi)

        if files_to_resolve_online:
            try:
                hydrus.resolve_paths(
                    files_to_resolve_online,
                    progress_callback=lambda d, t: inline_progress("Resolving paths online", d, t),
                )
                clear_line()
                with db_cls(db_path) as db:
                    for fi in files_to_resolve_online:
                        if fi.local_path:
                            db.upsert_file(
                                fi.file_hash,
                                file_path=fi.local_path,
                                mime=fi.mime,
                                num_frames=fi.num_frames,
                            )
            except HydrusConnectionError as exc:
                print(_c(f"\nERROR: Hydrus connection lost during path resolution: {exc}", RED), file=sys.stderr)
                logger.error("Hydrus connection lost during path resolution: %s", exc)
                return 1

    # endregion

    mime_rejected = len(all_rejected_hashes)
    no_path_count = sum(1 for fi in file_infos if not fi.local_path)
    if no_path_count:
        print(f"  {_c(f'Warning: {no_path_count} files have no local path (will be skipped)', YELLOW)}")
        logger.warning("%d files have no local path on disk and will be skipped.", no_path_count)

    actionable_files = [fi for fi in file_infos if fi.local_path]
    actionable_count = len(actionable_files)

    # region Pre-compute Model Work Plans (Skip / CPU Re-filter / GPU)
    from hyvis.inference import ModelPassPlan, PhaseStats, infer_files, plan_model_pass

    plans: list[ModelPassPlan] = []
    last_active_model_for_file: dict[str, str] = {}
    model_meta_by_id: dict[str, tuple[str, str, str]] = {}

    with db_cls(db_path) as db, CacheDatabase(cache_path, read_only=args.dry_run) as cdb:
        for m_cfg in cfg.inference.models:
            plan = plan_model_pass(
                m_cfg,
                actionable_files,
                config=cfg,
                db=db,
                cache_db=cdb,
                force=effective_force,
                is_resume=is_resume,
            )
            plans.append(plan)

            vocab_hash = ""
            if plan.cache_model_row is not None:
                v = cdb.get_vocab(plan.cache_model_row)
                if v is not None:
                    vocab_hash = v.vocab_hash
            model_meta_by_id[m_cfg.model_id] = (plan.resolved_variant_id, vocab_hash, plan.filter_hash)

            for fi in plan.refilter_files:
                last_active_model_for_file[fi.file_hash] = m_cfg.model_id
            for fi in plan.files_to_infer:
                last_active_model_for_file[fi.file_hash] = m_cfg.model_id

    # Start background PyTorch/ONNX imports ONLY if at least one file actually needs model inference
    infer_backends_needed = [p.model_cfg.backend for p in plans if p.files_to_infer]
    if infer_backends_needed:
        start_imports(infer_backends_needed)

    # region Confirmation print
    print_confirmation(
        cfg,
        actionable_count,
        extra_count,
        force=effective_force,
        mode=mode,
        service_name_by_key=service_name_by_key,
        hydrus_version=hydrus_version,
        api_version=api_version,
        boot_time=boot_time,
        tag_query_counts=tag_query_counts,
        page_query_counts=page_query_counts,
        mime_rejected=mime_rejected,
        rejected_mimes=rejected_mimes,
        is_resume=is_resume,
        model_plans=plans,
    )

    if actionable_count == 0:
        if is_resume and not args.dry_run:
            with Database(db_path) as db:
                db.clear_active_run()
        print(_c("No actionable files. Exiting.", YELLOW))
        logger.info("No actionable files with local paths. Exiting.")
        return 0

    # region Previewing
    if cfg.hydrus.preview and not args.no_preview and hydrus is not None:
        p = cfg.hydrus.preview
        try:
            preview_hashes = [fi.file_hash for fi in actionable_files]
            focused_any = False

            if (p.page_name and preview_hashes) or (p.rejected_page_name and all_rejected_hashes):
                print("Setting up preview...")

            if p.page_name and preview_hashes:
                print(_c(f"  Sending {len(preview_hashes)} file(s) to preview page '{p.page_name}'...", DIM))
                pk = hydrus.setup_preview_page(p.page_name, preview_hashes, p.page_index, focus=False)
                hydrus.focus_page(pk)
                focused_any = True
                print(_c("    Done.", GREEN))
                logger.info("Sent %d files to preview page '%s'", len(preview_hashes), p.page_name)

            if p.rejected_page_name and all_rejected_hashes:
                print(
                    _c(
                        f"  Sending {len(all_rejected_hashes)} file(s) "
                        + f"to rejected preview page '{p.rejected_page_name}'...",
                        DIM,
                    )
                )
                rpk = hydrus.setup_preview_page(
                    p.rejected_page_name, all_rejected_hashes, p.rejected_page_index, focus=False
                )
                if not focused_any:
                    hydrus.focus_page(rpk)
                print(_c("    Done.", GREEN))
                logger.info(
                    "Sent %d files to rejected preview page '%s'", len(all_rejected_hashes), p.rejected_page_name
                )

        except HydrusError as exc:
            print(_c(f"\nERROR: Failed to set up preview page: {exc}", RED), file=sys.stderr)
            logger.error("Failed to set up preview page: %s", exc)
            return 1

        print()

    if not args.yes:
        previous_sigint_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            input("  Press ENTER to start, or Ctrl+C to abort: ")
        except (KeyboardInterrupt, EOFError):
            print(_c("\n\nAborted.", YELLOW))
            logger.info("Execution aborted at confirmation prompt.")
            return 0
        finally:
            signal.signal(signal.SIGINT, previous_sigint_handler)

    print()
    logger.info(
        "Starting processing pipeline: mode=%s, force=%s, resume=%s, actionable_files=%d",
        mode,
        effective_force,
        is_resume,
        actionable_count,
    )

    if infer_backends_needed:
        wait_for_imports()

    # region Database & Inference Execution
    total_infer_ok = total_infer_err = 0
    total_refiltered = 0
    total_push_ok = total_push_err = 0
    total_skipped = 0
    total_zero_tags = 0
    run_status = "done"

    with db_cls(db_path) as db, CacheDatabase(cache_path, read_only=args.dry_run) as cdb:
        if mode != "dry_run" and db.has_pending_pushes():
            print(
                _c(
                    "  Note: there are files with unpushed inference results. Run with --push-only to push them.",
                    YELLOW,
                )
            )
            print()

        # Lock active run scratchpad before starting inference passes (if fresh non-dry run)
        if not is_resume and mode != "dry_run":
            frozen_toml = tomli_w.dumps(cfg.model_dump(mode="json", exclude_none=True))
            db.start_active_run(
                frozen_toml,
                config_path=str(args.config) if args.config else None,
                mode=RunMode.INFER_ONLY if effective_infer_only else RunMode.DEFAULT,
                force_mode=effective_force,
                hyvis_version=get_version(),
                candidate_files=[(fi.file_hash, fi.mime) for fi in actionable_files],
                rejected_files=[(h, None) for h in all_rejected_hashes],
            )

        for plan in plans:
            model_cfg = plan.model_cfg
            try:
                import vibe

                model_info = vibe.describe(model_cfg.model_id)
                display_name = model_info.identity.display_name
            except Exception:
                display_name = model_cfg.model_id

            var_note = f", variant: {model_cfg.variant_id}" if model_cfg.variant_id else ""
            print(f"  Using model: {_c(display_name, BOLD, CYAN)} {_c(f'(ID: {model_cfg.model_id}{var_note})', DIM)}")
            print()

            infer_stats: PhaseStats | None
            if mode in ("default", "infer_only", "dry_run"):
                progress = Progress(total=actionable_count)
                infer_stats = await infer_files(
                    model_cfg,
                    actionable_files,
                    config=cfg,
                    db=db,
                    cache_db=cdb,
                    progress=progress,
                    force=effective_force,
                    plan=plan,
                    last_active_model_for_file=last_active_model_for_file,
                    model_meta_by_id=model_meta_by_id,
                )
                progress.finish()

                total_infer_ok += infer_stats.ok
                total_refiltered += infer_stats.refiltered
                total_infer_err += infer_stats.errors
                total_skipped += infer_stats.skipped
                total_zero_tags += infer_stats.zero_tags

                processed_count = infer_stats.ok + infer_stats.refiltered + infer_stats.skipped
                active_count = infer_stats.ok + infer_stats.refiltered
                files_with_tags = active_count - infer_stats.zero_tags
                tag_label = "tag" if infer_stats.total_tags_enqueued == 1 else "tags"

                print(f"\n  {_c('Inference summary:', BOLD)}")
                print(
                    f"    Processed : {processed_count} files "
                    f"({infer_stats.ok} inferred, {infer_stats.refiltered} re-filtered (cpu), {infer_stats.skipped} skipped)"
                )
                print(f"    Enqueued  : {files_with_tags} files ({infer_stats.total_tags_enqueued} {tag_label})")
                if infer_stats.zero_tags > 0:
                    print(f"    Zero tags : {infer_stats.zero_tags} files (all predictions below threshold)")
                if infer_stats.errors > 0:
                    print(f"    Errors    : {infer_stats.errors}")

                if infer_stats.aborted_early:
                    run_status = "aborted"
                    print(_c(f"\n  ABORTED: {infer_stats.abort_reason}", RED))
                    if mode != "dry_run":
                        print(
                            _c(
                                "  Run progress saved. Resume with 'hyvis --resume' or abandon with '--discard-run'.",
                                YELLOW,
                            )
                        )
                    logger.warning(
                        "Pipeline aborted during model '%s': %s", model_cfg.model_id, infer_stats.abort_reason
                    )
                    break

        # Inference passes complete -> wipe active_run scratchpad before pushing
        if run_status == "done" and mode != "dry_run":
            db.clear_active_run()

        # region Trailing Push & Cleanup
        if run_status == "done" and mode == "default":
            pending_count = db.get_pending_push_count()

            if pending_count > 0:
                if hydrus is None:
                    hydrus, _, _, _, _ = connect_hydrus(cfg)

                print()
                print(_c("  ══ Pushing Results to Hydrus ══════════════════════════════", BOLD, CYAN))
                print()
                logger.info("Beginning push phase: %d file(s) with pending tags in queue", pending_count)
                from hyvis.push_service import drain_push_queue

                push_progress = Progress(total=pending_count)
                push_progress.reset_start_time()

                p_ok, p_err = await drain_push_queue(
                    db,
                    hydrus,
                    wait_for_hydrus=not effective_no_wait,
                    progress=push_progress,
                )
                total_push_ok += p_ok
                total_push_err += p_err

                push_progress.finish()
                logger.info("Push phase complete: %d succeeded, %d failed", p_ok, p_err)

        if mode != "dry_run":
            dead_letters = db.get_failed_push_count()
            if dead_letters > 0:
                print(
                    _c(
                        f"  ⚠ {dead_letters} file(s) in push_queue reached max retry attempts and were sidelined.",
                        YELLOW,
                    )
                )

    # region Run summary
    total_active = total_infer_ok + total_refiltered
    total_files_with_tags = max(0, total_active - total_zero_tags)
    print_run_summary(
        run_status=run_status,
        mode=mode,
        total_infer_ok=total_infer_ok,
        total_infer_err=total_infer_err,
        total_skipped=total_skipped,
        total_push_ok=total_push_ok,
        total_push_err=total_push_err,
        total_zero_tags=total_zero_tags,
        total_files_with_tags=total_files_with_tags,
        total_refiltered=total_refiltered,
    )

    return 0 if run_status == "done" else 1


# region Entry point


def cli() -> None:
    raise SystemExit(asyncio.run(main()))


if __name__ == "__main__":
    cli()
