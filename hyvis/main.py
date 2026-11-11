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

from hyvis.bg_imports import start_imports, wait_for_imports
from hyvis.cli import parse_args
from hyvis.cli_display import connect_hydrus, print_confirmation, print_run_summary
from hyvis.hydrus import HydrusConnectionError, HydrusError, validate_service_keys
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


# region Main coroutine


async def main() -> int:
    args = parse_args()

    # Print invocation CLI arguments immediately
    if len(sys.argv) > 1:
        cmd_args = " ".join(shlex.quote(arg) for arg in sys.argv[1:])
        print(_c(f"Executing HyVis with arguments: {cmd_args}\n", DIM))

    # Every file hash processed/targeted during entire script run
    touched_hashes: set[str] = set()

    # Load + validate config
    if not args.config.exists():
        print(_c(f"ERROR: Config file not found: {args.config}", RED), file=sys.stderr)
        return 1

    from hyvis.config import AppConfig

    try:
        cfg = AppConfig.from_file(args.config)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        print(_c(f"ERROR: Failed to parse config: {exc}", RED), file=sys.stderr)
        return 1

    # Merge CLI overrides into the Pydantic config object
    if args.api_url or args.api_key:
        hydrus_updates: dict[str, str] = {}
        if args.api_url:
            hydrus_updates["api_url"] = args.api_url.rstrip("/")
        if args.api_key:
            hydrus_updates["api_key"] = args.api_key

        new_hydrus = cfg.hydrus.model_copy(update=hydrus_updates)
        cfg = cfg.model_copy(update={"hydrus": new_hydrus})

    # region Logging Setup
    effective_log_level = args.log_level or cfg.hyvis.log_level
    log_file_path = setup_logging(
        effective_log_level,
        log_to_file=cfg.hyvis.log_to_file,
        log_dir=cfg.hyvis.log_dir,
        max_log_files=cfg.hyvis.max_log_files,
        config_path=args.config,
    )
    if log_file_path:
        logger.debug("Session logging active at: %s", log_file_path)
    # endregion

    from hyvis.db import Database
    from hyvis.dry_run import DryRunDatabase
    from hyvis.extra_hashes import load_extra_hashes

    # Resolve CLI vs. TOML precedence (CLI flag overrides TOML setting)
    effective_infer_only = args.infer_only or cfg.hyvis.infer_only
    effective_no_wait = args.no_wait or cfg.hydrus.no_wait

    if args.dry_run:
        mode = "dry_run"
    elif effective_infer_only:
        mode = "infer_only"
    else:
        mode = "default"

    db_cls = DryRunDatabase if args.dry_run else Database

    # Resolve database path upfront
    db_path = Path(cfg.database.path)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path

    # region Early Action: Clear Cache
    if args.clear_cache:
        logger.info("Executing action: --clear-cache on %s", db_path)
        with Database(db_path, min_cache_score=cfg.database.min_cache_score) as db:
            row = db.conn.execute("SELECT COUNT(*) FROM inference_cache").fetchone()
            count = row[0] if row else 0
            if count == 0:
                print("Inference cache is already empty.")
                logger.info("Inference cache is already empty.")
                return 0

            print(_c(f"  Warning: This will delete {count} cached inference records.", YELLOW))
            print("  Re-running models on these files will require full GPU inference.")
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

            deleted = db.clear_raw_cache()
            print(_c(f"\n  Successfully cleared {deleted} cached records and reclaimed database space.", GREEN))
            logger.info("Cleared %d cached records from %s", deleted, db_path)
            return 0

    # region Early Action: Push Only
    if args.push_only:
        logger.info("Executing action: --push-only on %s", db_path)
        with Database(db_path, min_cache_score=cfg.database.min_cache_score) as db:
            pending_count = db.get_pending_push_count()
            if pending_count == 0:
                print("Push queue is empty. All tags are already in Hydrus.")
                logger.info("Push queue is empty. No operations needed.")
                return 0

            hydrus, _, _, _, _ = connect_hydrus(cfg, args)

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

    # preload heavy imports in background
    backends = [m.backend for m in cfg.inference.models]
    start_imports(backends)

    # Pass the presence of an extra hash file to the validator
    has_extra_hashes = args.extra_hash_file is not None
    errors = cfg.hyvis_validate(has_extra_hashes=has_extra_hashes)
    if errors:
        print(_c("ERROR: Invalid configuration:", RED), file=sys.stderr)
        for e in errors:
            print(f"  • {e}", file=sys.stderr)
            logger.error("Configuration validation error: %s", e)
        return 1

    # --- Connect to Hydrus (always needed: confirmation screen + file paths) ---
    hydrus, service_name_by_key, hydrus_version, api_version, boot_time = connect_hydrus(cfg, args)
    logger.info("Connected to Hydrus v%s (API v%s) at %s", hydrus_version, api_version, cfg.hydrus.api_url)

    # Validate all Hydrus service keys before showing confirmation
    try:
        validate_service_keys(cfg, service_name_by_key)
    except HydrusError as e:
        print(_c(f"ERROR: {e}", RED), file=sys.stderr)
        logger.error("Service key validation failed: %s", e)
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

    # 1. Ingest extra hashes file if provided
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

    # 2. Run unified preflight collection and MIME filtering
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
    mime_rejected = len(all_rejected_hashes)
    extra_count = len(extra_hash_values)

    logger.info(
        "Preflight complete: %d candidate files | %d MIME rejected (%s)",
        len(file_infos),
        mime_rejected,
        ", ".join(sorted(rejected_mimes)) if rejected_mimes else "none",
    )

    if mime_rejected:
        rejected_list = ", ".join(sorted(rejected_mimes)) if rejected_mimes else "unknown"
        print(_c(f"  Rejected {mime_rejected} files with unsupported MIME types: {rejected_list}", DIM))

    if not file_infos and not extra_count:
        print(_c("\nNo files matched the configured queries. Nothing to do.", YELLOW))
        logger.info("No candidate files matched queries. Exiting.")
        return 0

    # 3. Smart Path Resolution: Check local SQLite cache first to avoid redundant HTTP calls
    with db_cls(db_path, min_cache_score=cfg.database.min_cache_score) as db:
        cached_paths = db.bulk_get_known_paths([fi.file_hash for fi in file_infos])

    files_to_resolve_online = []
    for fi in file_infos:
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
            with db_cls(db_path, min_cache_score=cfg.database.min_cache_score) as db:
                for fi in files_to_resolve_online:
                    if fi.local_path:
                        db.upsert_file(fi.file_hash, file_path=fi.local_path, mime=fi.mime)
        except HydrusConnectionError as exc:
            print(_c(f"\nERROR: Hydrus connection lost during path resolution: {exc}", RED), file=sys.stderr)
            logger.error("Hydrus connection lost during path resolution: %s", exc)
            return 1

    no_path_count = sum(1 for fi in file_infos if not fi.local_path)
    if no_path_count:
        # todo: print the hashes (newline separated) that have no path associated with them, generally this shouldnt even show up(?) if they were correctly deleted in hydrus
        # files that are deleted dont(?) show up in a query's results 
        print(f"  {_c(f'Warning: {no_path_count} files have no local path (will be skipped)', YELLOW)}")
        logger.warning("%d files have no local path on disk and will be skipped.", no_path_count)

    actionable_count = sum(1 for fi in file_infos if fi.local_path)
    touched_hashes.update(fi.file_hash for fi in file_infos if fi.local_path)

    logger.info(
        "Path resolution: %d cached in DB, %d resolved online, %d missing on disk. Actionable: %d",
        len(cached_paths),
        len(files_to_resolve_online),
        no_path_count,
        actionable_count,
    )

    # region Confirmation print
    print_confirmation(
        cfg,
        actionable_count,
        extra_count,
        force=args.force,
        mode=mode,
        service_name_by_key=service_name_by_key,
        hydrus_version=hydrus_version,
        api_version=api_version,
        boot_time=boot_time,
        tag_query_counts=tag_query_counts,
        page_query_counts=page_query_counts,
        mime_rejected=mime_rejected,
        rejected_mimes=rejected_mimes,
    )

    if actionable_count == 0:
        print(_c("No actionable files. Exiting.", YELLOW))
        logger.info("No actionable files with local paths. Exiting.")
        return 0

    # region Previewing
    if cfg.hydrus.preview and not args.no_preview:
        p = cfg.hydrus.preview
        try:
            preview_hashes = [fi.file_hash for fi in file_infos if fi.local_path]
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
        "Starting processing pipeline: mode=%s, force=%s, actionable_files=%d", mode, args.force, actionable_count
    )

    # Wait for background libraries to finish loading before starting database work
    wait_for_imports()

    # region Database & Inference Execution
    from hyvis.inference import PhaseStats, infer_files

    total_infer_ok = total_infer_err = 0
    total_push_ok = total_push_err = 0
    total_skipped = 0
    total_zero_tags = 0
    run_status = "done"

    with db_cls(db_path, min_cache_score=cfg.database.min_cache_score) as db:
        if mode != "dry_run" and db.has_pending_pushes():
            print(
                _c(
                    "  Note: there are files with unpushed inference results. Run with --push-only to push them.",
                    YELLOW,
                )
            )
            print()

        for model_cfg in cfg.inference.models:
            try:
                import vibe

                model_info = vibe.describe(model_cfg.model_id)
                display_name = model_info.identity.display_name
            except Exception:
                display_name = model_cfg.model_id

            var_note = f", variant: {model_cfg.variant_id}" if model_cfg.variant_id else ""
            print(f"  Using model: {_c(display_name, BOLD, CYAN)} {_c(f'(ID: {model_cfg.model_id}{var_note})', DIM)}")
            print()

            # region P1: Inference
            infer_stats: PhaseStats | None = None
            if mode in ("default", "infer_only", "dry_run"):
                progress = Progress(total=actionable_count)
                infer_stats = await infer_files(
                    model_cfg,
                    file_infos,
                    config=cfg,
                    db=db,
                    progress=progress,
                    force=args.force,
                )
                progress.finish()

                total_infer_ok += infer_stats.ok
                total_infer_err += infer_stats.errors
                total_skipped += infer_stats.skipped
                total_zero_tags += infer_stats.zero_tags

                processed_count = infer_stats.ok + infer_stats.skipped
                files_with_tags = processed_count - infer_stats.zero_tags
                tag_label = "tag" if infer_stats.total_tags_enqueued == 1 else "tags"

                print(f"\n  {_c('Inference summary:', BOLD)}")
                print(
                    f"    Processed : {processed_count} files ({infer_stats.ok} newly inferred, {infer_stats.skipped} cached)"
                )
                print(f"    Enqueued  : {files_with_tags} files ({infer_stats.total_tags_enqueued} {tag_label})")
                if infer_stats.zero_tags > 0:
                    print(f"    Zero tags : {infer_stats.zero_tags} files (all predictions below threshold)")
                if infer_stats.errors > 0:
                    print(f"    Errors    : {infer_stats.errors}")

                if infer_stats.aborted_early:
                    run_status = "aborted"
                    print(_c(f"\n  ABORTED: {infer_stats.abort_reason}", RED))
                    logger.warning(
                        "Pipeline aborted during model '%s': %s", model_cfg.model_id, infer_stats.abort_reason
                    )
                    break

        # region Trailing Push & Cleanup
        if run_status == "done" and mode == "default":
            pending_count = db.get_pending_push_count()

            if pending_count > 0:
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

    # region Run summary
    total_files_with_tags = (total_infer_ok + total_skipped) - total_zero_tags
    print_run_summary(
        run_status=run_status,
        run_id="(queue)",
        mode=mode,
        total_infer_ok=total_infer_ok,
        total_infer_err=total_infer_err,
        total_skipped=total_skipped,
        total_push_ok=total_push_ok,
        total_push_err=total_push_err,
        total_zero_tags=total_zero_tags,
        total_files_with_tags=total_files_with_tags,
    )

    return 0 if run_status == "done" else 1


# region Entry point


def cli() -> None:
    raise SystemExit(asyncio.run(main()))


if __name__ == "__main__":
    cli()
