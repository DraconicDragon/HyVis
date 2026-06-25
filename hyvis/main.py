"""
main.py Entry point.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import signal
import sys
import uuid
from pathlib import Path

from hyvis.bg_imports import start_imports, wait_for_imports
from hyvis.cli import parse_args
from hyvis.cli_display import connect_hydrus, print_confirmation, print_run_summary
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

    mode = "infer_only" if args.infer_only else "default"

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

    # preload heavy imports in background
    backends = [m.backend for m in cfg.inference.models]
    start_imports(backends)

    # Pass the presence of an extra hash file to the validator
    has_extra_hashes = args.extra_hash_file is not None
    errors = cfg.validate(has_extra_hashes=has_extra_hashes)
    if errors:
        print(_c("ERROR: Invalid configuration:", RED), file=sys.stderr)
        for e in errors:
            print(f"  • {e}", file=sys.stderr)
        return 1

    # region Logging setup
    # todo: treat differently, maybe add -v/--verbose cli arg to make this debug, otherwise PIL log spam
    effective_log_level = args.log_level or cfg.hyvis.log_level
    setup_logging(effective_log_level)

    # CLI overrides for Hydrus connection
    if args.api_url or args.api_key:
        cfg = dataclasses.replace(
            cfg,
            hydrus=dataclasses.replace(
                cfg.hydrus,
                api_url=(args.api_url or cfg.hydrus.api_url).rstrip("/"),
                api_key=args.api_key or cfg.hydrus.api_key,
            ),
        )

    # --- Connect to Hydrus (always needed: confirmation screen + file paths) ---
    hydrus, service_name_by_key, hydrus_version, api_version, boot_time = connect_hydrus(cfg, args)

    if cfg.hydrus.file_queries and not any(q.tags for q in cfg.hydrus.file_queries):
        print(
            _c(
                "  Warning: all configured Hydrus file queries are empty; only extra hashes or page queries can produce files.",
                YELLOW,
            )
        )

    file_infos = []
    actionable_count = 0
    extra_count = 0

    file_query_counts: list[int] = []
    page_query_counts: list[int] = []

    # Rejections tracking init
    mime_rejected = 0
    rejected_mimes: set[str] = set()
    all_rejected_hashes: list[str] = []

    from hyvis.extra_hashes import load_extra_hashes

    # Collect candidate files
    from hyvis.hydrus import HydrusConnectionError, HydrusError

    print(_c("Collecting candidate files...           ", DIM), end="\r", flush=True)
    raw_hashes = set()

    try:
        if cfg.hydrus.file_queries:
            fq_hashes, file_query_counts = hydrus.collect_candidate_hashes(cfg.hydrus.file_queries)
            raw_hashes |= fq_hashes

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

        try:
            file_infos = hydrus.resolve_paths(
                file_infos,
                progress_callback=lambda d, t: inline_progress("Resolving paths", d, t),
            )
        except HydrusConnectionError as exc:
            print(_c(f"\nERROR: Hydrus connection lost during path resolution: {exc}", RED), file=sys.stderr)
            return 1

        clear_line()

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

                extra_infos = hydrus.resolve_paths(extra_infos)
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
                    f"\n  {_c(f'Warning: {missing_extra} extra hash(es) were not found or have no usable local path', YELLOW)}"
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
        file_query_counts=file_query_counts,
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
                        f"  Sending {len(all_rejected_hashes)} file(s) to rejected preview page '{p.rejected_page_name}'...",
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

    # region Database
    from hyvis.db import Database

    run_id = str(uuid.uuid4())
    try:
        config_toml = args.config.read_text()
    except OSError as exc:
        print(_c(f"ERROR: Failed to read config file: {exc}", RED), file=sys.stderr)
        return 1

    db_path = Path(cfg.database.path)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path

    from hyvis.inference import PhaseStats, infer_files

    total_infer_ok = total_infer_err = 0
    total_push_ok = total_push_err = 0
    total_skipped = 0
    run_status = "done"

    with Database(db_path) as db:
        db.start_run(run_id=run_id, config_toml=config_toml)

        if db.has_pending_push():
            print(
                _c(
                    "  Note: there are files with unpushed inference results. Run 'hyvis-push-pending' to push them.",
                    YELLOW,
                )
            )
            print()

        for model_cfg in cfg.inference.models:
            try:
                import vibe

                model_info = vibe.describe(model_cfg.model_id)
                display_name = model_info.display_name
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
                    run_id=run_id,
                    force=args.force,
                    hydrus=hydrus if mode == "default" else None,
                )
                progress.finish()

                total_infer_ok += infer_stats.ok
                total_infer_err += infer_stats.errors
                total_skipped += infer_stats.skipped
                total_push_ok += infer_stats.push_ok
                total_push_err += infer_stats.push_errors

                print(f"\n  {_c('Inference summary:', BOLD)}")
                print(f"    Cached OK : {infer_stats.ok}")
                print(f"    Errors    : {infer_stats.errors}")
                print(f"    Skipped   : {infer_stats.skipped}")
                print(f"    Tags      : {infer_stats.total_tags_cached}")

                if infer_stats.hydrus_suspended:
                    pending = infer_stats.ok - infer_stats.push_ok
                    print(_c(f"  Interleaved push suspended. {pending} file(s) pending push.", YELLOW))
                elif infer_stats.push_errors:
                    print(_c(f"  {infer_stats.push_errors} file(s) failed to push to Hydrus.", YELLOW))

                if infer_stats.aborted_early:
                    run_status = "aborted"
                    print(_c(f"\n  ABORTED: {infer_stats.abort_reason}", RED))
                    break

    # region Trailing Push & Cleanup
    if run_status == "done" and mode == "default":
        with Database(db_path) as db:
            has_pending = db.has_pending_push() or bool(db.get_pending_cleanup())

        if has_pending:
            print()
            print(_c("  ══ Trailing Push & Cleanup ══════════════════════════════", BOLD, CYAN))
            print()
            from hyvis.push_service import run_push_and_cleanup

            await run_push_and_cleanup(
                db_path=db_path,
                api_url_override=cfg.hydrus.api_url,
                api_key_override=cfg.hydrus.api_key,
                wait_for_hydrus=not args.no_wait,
                wait_interval=5.0,
                skip_confirm=True,  # Bypasses prompt since user confirmed at start
            )

    # Re-calculate totals from DB in case trailing push modified them
    if mode in ("default", "infer_only"):
        with Database(db_path) as db:
            rows = db.conn.execute(
                "SELECT infer_success, push_success FROM file_model_results WHERE run_id = ?", (run_id,)
            ).fetchall()

            total_infer_ok = sum(1 for r in rows if r[0] == 1)
            total_infer_err = sum(1 for r in rows if r[0] == 0)
            total_push_ok = sum(1 for r in rows if r[1] == 1)
            total_push_err = sum(1 for r in rows if r[0] == 1 and r[1] == 0)

    # region Run summary
    print_run_summary(
        run_status=run_status,
        run_id=run_id,
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
