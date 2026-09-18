"""
main.py Entry point.
"""

from __future__ import annotations

import asyncio
import logging
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

    # Resolve CLI vs. TOML precedence (CLI flag overrides TOML setting)
    effective_infer_only = args.infer_only or cfg.hyvis.infer_only
    effective_no_wait = args.no_wait or cfg.hydrus.no_wait
    mode = "infer_only" if effective_infer_only else "default"

    from hyvis.db import Database
    from hyvis.extra_hashes import load_extra_hashes

    # Resolve database path upfront
    db_path = Path(cfg.database.path)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path

    # region Early Action: Clear Cache
    if args.clear_cache:
        with Database(db_path, min_cache_score=cfg.database.min_cache_score) as db:
            row = db.conn.execute("SELECT COUNT(*) FROM inference_cache").fetchone()
            count = row[0] if row else 0
            if count == 0:
                print("Inference cache is already empty.")
                return 0

            print(_c(f"  Warning: This will delete {count} cached inference records.", YELLOW))
            print("  Re-running models on these files will require full GPU inference.")
            if not args.yes:
                try:
                    ans = input("  Proceed? [y/N]: ").strip().lower()
                    if ans not in ("y", "yes"):
                        print(_c("\nAborted.", YELLOW))
                        return 0
                except (KeyboardInterrupt, EOFError):
                    print(_c("\n\nAborted.", YELLOW))
                    return 0

            deleted = db.clear_raw_cache()
            print(_c(f"\n  Successfully cleared {deleted} cached records and reclaimed database space.", GREEN))
            return 0

    # region Early Action: Push Only
    if args.push_only:
        with Database(db_path, min_cache_score=cfg.database.min_cache_score) as db:
            pending_count = db.get_pending_push_count()
            if pending_count == 0:
                print("Push queue is empty. All tags are already in Hydrus.")
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
        return 1

    # region Logging setup
    # todo: treat differently, maybe add -v/--verbose cli arg to make this debug, otherwise PIL log spam
    effective_log_level = args.log_level or cfg.hyvis.log_level
    setup_logging(effective_log_level)

    # --- Connect to Hydrus (always needed: confirmation screen + file paths) ---
    hydrus, service_name_by_key, hydrus_version, api_version, boot_time = connect_hydrus(cfg, args)

    # Validate all Hydrus service keys before showing confirmation
    try:
        validate_service_keys(cfg, service_name_by_key)
    except HydrusError as e:
        print(_c(f"ERROR: {e}", RED), file=sys.stderr)
        return 1

    if cfg.hydrus.tag_queries and not any(q.tags for q in cfg.hydrus.tag_queries):
        print(
            _c(
                "  Warning: all configured Hydrus tag queries are empty; "
                + "only extra hashes or page queries can produce files.",
                YELLOW,
            )
        )

    file_infos = []
    actionable_count = 0
    extra_count = 0

    tag_query_counts: list[int] = []
    page_query_counts: list[int] = []

    # Rejections tracking init
    mime_rejected = 0
    rejected_mimes: set[str] = set()
    all_rejected_hashes: list[str] = []

    # Collect candidate files
    print(_c("Collecting candidate files...           ", DIM), end="\r", flush=True)
    raw_hashes = set()

    try:
        if cfg.hydrus.tag_queries:
            tq_hashes, tag_query_counts = hydrus.collect_candidate_hashes(cfg.hydrus.tag_queries)
            raw_hashes |= tq_hashes

        if cfg.hydrus.page_queries:
            pq_hashes, page_query_counts = hydrus.collect_page_hashes(cfg.hydrus.page_queries)
            raw_hashes |= pq_hashes
    except HydrusConnectionError as exc:
        print(_c(f"\nERROR: Hydrus connection lost while fetching files: {exc}", RED), file=sys.stderr)
        return 1
    except HydrusError as exc:
        print(_c(f"\nERROR: Hydrus query failed: {exc}", RED), file=sys.stderr)
        return 1

    # Filter by MIME
    hash_list = sorted(raw_hashes)
    total_raw = len(hash_list)
    if total_raw:
        print(_c(f"Fetching metadata for {total_raw} candidates...  ", DIM), end="\r", flush=True)

    if total_raw:
        try:
            # Collect main query rejections
            file_infos, r_mimes, r_hashes = hydrus.filter_by_mime(
                hash_list,
                progress_callback=lambda d, t: inline_progress("Filtering metadata", d, t),
            )
            rejected_mimes.update(r_mimes)
            all_rejected_hashes.extend(r_hashes)
        except HydrusConnectionError as exc:
            print(_c(f"\nERROR: Hydrus connection lost during metadata fetch: {exc}", RED), file=sys.stderr)
            return 1

        clear_line()

        mime_rejected = total_raw - len(file_infos)
        if mime_rejected:
            rejected_list = ", ".join(sorted(rejected_mimes)) if rejected_mimes else "unknown"
            print(_c(f"  Rejected {mime_rejected} files with unsupported MIME types: {rejected_list}", DIM))
        if not file_infos and args.extra_hash_file is None:
            print(_c("\nAll files were filtered by MIME type. Nothing to do.", YELLOW))
            return 0

        # Smart Path Resolution: Check local SQLite cache first to avoid 50k+ HTTP calls!
        with Database(db_path, min_cache_score=cfg.database.min_cache_score) as db:
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
                with Database(db_path, min_cache_score=cfg.database.min_cache_score) as db:
                    for fi in files_to_resolve_online:
                        if fi.local_path:
                            db.upsert_file(fi.file_hash, file_path=fi.local_path, mime=fi.mime)
            except HydrusConnectionError as exc:
                print(_c(f"\nERROR: Hydrus connection lost during path resolution: {exc}", RED), file=sys.stderr)
                return 1

        no_path_count = sum(1 for fi in file_infos if not fi.local_path)
        if no_path_count:
            print(f"  {_c(f'Warning: {no_path_count} files have no local path (will be skipped)', YELLOW)}")

        actionable_count = sum(1 for fi in file_infos if fi.local_path)
        touched_hashes.update(fi.file_hash for fi in file_infos if fi.local_path)

    # region Extra hashes file
    if args.extra_hash_file is not None:
        try:
            extra_hash_values = load_extra_hashes(args.extra_hash_file)
        except OSError as exc:
            print(_c(f"ERROR: Failed to read extra hash file: {exc}", RED), file=sys.stderr)
            return 1
        except ValueError as exc:
            print(_c(f"ERROR: Invalid extra hash file: {exc}", RED), file=sys.stderr)
            return 1

        if extra_hash_values:
            print(_c(f"Fetching metadata for {len(extra_hash_values)} extra hashes...  ", DIM), end="\r", flush=True)
            try:
                # Collect extra hash file rejections
                extra_infos, extra_r_mimes, extra_r_hashes = hydrus.filter_by_mime(extra_hash_values)
                extra_mime_rejected = len(extra_hash_values) - len(extra_infos)
                mime_rejected += extra_mime_rejected
                rejected_mimes.update(extra_r_mimes)
                all_rejected_hashes.extend(extra_r_hashes)

                # Smart Path Resolution for extra hashes
                with Database(db_path, min_cache_score=cfg.database.min_cache_score) as db:
                    extra_cached_paths = db.bulk_get_known_paths([fi.file_hash for fi in extra_infos])

                extra_online_resolve = []
                for fi in extra_infos:
                    cached_p = extra_cached_paths.get(fi.file_hash)
                    if cached_p and Path(cached_p).is_file():
                        fi.local_path = cached_p
                    else:
                        extra_online_resolve.append(fi)

                if extra_online_resolve:
                    hydrus.resolve_paths(extra_online_resolve)
                    with Database(db_path, min_cache_score=cfg.database.min_cache_score) as db:
                        for fi in extra_online_resolve:
                            if fi.local_path:
                                db.upsert_file(fi.file_hash, file_path=fi.local_path, mime=fi.mime)

            except HydrusConnectionError as exc:
                print(_c(f"\nERROR: Hydrus connection lost while fetching extra hashes: {exc}", RED), file=sys.stderr)
                return 1

            extra_count = len(extra_infos)
            if extra_r_mimes:
                rejected_list = ", ".join(sorted(extra_r_mimes))
                print(f"\r{_c(f'  Rejected extra hashes with unsupported MIME types: {rejected_list}', DIM)}  ")

            missing_extra = len(extra_hash_values) - extra_count
            if missing_extra:
                print(
                    "\n  "
                    + _c(
                        f"Warning: {missing_extra} extra hash(es) were not found or have no usable local path",
                        YELLOW,
                    )
                )

            if extra_infos:
                file_infos.extend(extra_infos)
                actionable_count = sum(1 for fi in file_infos if fi.local_path)
                touched_hashes.update(fi.file_hash for fi in extra_infos if fi.local_path)
        else:
            print(_c("  Warning: extra hash file was empty.", YELLOW))

    if not file_infos:
        if args.extra_hash_file is not None:
            print(_c("\nNo files matched the configured queries or extra-hash list. Nothing to do.", YELLOW))
        else:
            print(_c("\nNo files matched the configured queries. Nothing to do.", YELLOW))
        return 0

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

        except HydrusError as exc:
            print(_c(f"\nERROR: Failed to set up preview page: {exc}", RED), file=sys.stderr)
            return 1

        print()

    if not args.yes:
        previous_sigint_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            input("  Press ENTER to start, or Ctrl+C to abort: ")
        except (KeyboardInterrupt, EOFError):
            print(_c("\n\nAborted.", YELLOW))
            return 0
        finally:
            signal.signal(signal.SIGINT, previous_sigint_handler)

    print()

    # Wait for background libraries to finish loading before starting database work
    wait_for_imports()

    # region Database & Inference Execution
    from hyvis.inference import PhaseStats, infer_files

    total_infer_ok = total_infer_err = 0
    total_push_ok = total_push_err = 0
    total_skipped = 0
    run_status = "done"

    with Database(db_path, min_cache_score=cfg.database.min_cache_score) as db:
        if db.has_pending_pushes():
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
            print(f"  Using model: {_c(display_name, BOLD, CYAN)} {_c(f'(ID: {model_cfg.model_id})', DIM)}")
            print()

            # region P1: Inference
            infer_stats: PhaseStats | None = None
            if mode in ("default", "infer_only"):
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

                print(f"\n  {_c('Inference summary:', BOLD)}")
                print(f"    Cached OK : {infer_stats.ok}")
                print(f"    Errors    : {infer_stats.errors}")
                print(f"    Skipped   : {infer_stats.skipped}")
                print(f"    Raw tags  : {infer_stats.total_tags_cached}")
                print(f"    Enqueued  : {infer_stats.total_tags_enqueued}")

                if infer_stats.aborted_early:
                    run_status = "aborted"
                    print(_c(f"\n  ABORTED: {infer_stats.abort_reason}", RED))
                    break

        # region Trailing Push & Cleanup
        if run_status == "done" and mode == "default":
            pending_count = db.get_pending_push_count()

            if pending_count > 0:
                print()
                print(_c("  ══ Pushing Results to Hydrus ══════════════════════════════", BOLD, CYAN))
                print()
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

    # region Run summary
    print_run_summary(
        run_status=run_status,
        run_id="(queue)",
        mode=mode,
        total_infer_ok=total_infer_ok,
        total_infer_err=total_infer_err,
        total_skipped=total_skipped,
        total_push_ok=total_push_ok,
        total_push_err=total_push_err,
    )

    return 0 if run_status == "done" else 1


# region Entry point


def cli() -> None:
    raise SystemExit(asyncio.run(main()))


if __name__ == "__main__":
    cli()
