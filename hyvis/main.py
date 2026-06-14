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

from hyvis.bg_imports import start_imports, wait_for_imports
from hyvis.cli import parse_args
from hyvis.cli_display import connect_hydrus, print_confirmation, print_run_summary
from hyvis.progress import Progress, clear_line, inline_progress

logger = logging.getLogger(__name__)


# region Main coroutine


async def main() -> int:
    args = parse_args()

    if args.infer_only and args.push_only:
        print(_c("ERROR: --infer-only and --push-only are mutually exclusive.", RED), file=sys.stderr)
        return 1

    mode = "infer_only" if args.infer_only else "push_only" if args.push_only else "default"

    # Every file hash processed/targeted during entire script run used for tag removal at the end
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

    errors = cfg.validate()
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

    if mode != "push_only" and cfg.hydrus.file_queries and not any(q.tags for q in cfg.hydrus.file_queries):
        print(
            _c("  Warning: all configured Hydrus file queries are empty; only extra hashes can produce files.", YELLOW)
        )

    # For push-only we don't need to collect files from Hydrus.
    file_infos = []
    actionable_count = 0
    extra_count = 0

    if mode != "push_only":
        from hyvis.extra_hashes import load_extra_hashes

        # Collect candidate files
        from hyvis.hydrus import HydrusConnectionError

        print(_c("Collecting candidate files...           ", DIM), end="\r", flush=True)
        try:
            raw_hashes = hydrus.collect_candidate_hashes(cfg.hydrus.file_queries)
        except HydrusConnectionError as exc:
            print(_c(f"\nERROR: Hydrus connection lost while fetching files: {exc}", RED), file=sys.stderr)
            return 1

        # Filter by MIME
        hash_list = sorted(raw_hashes)
        total_raw = len(hash_list)
        if total_raw:
            print(_c(f"Fetching metadata for {total_raw} candidates...  ", DIM), end="\r", flush=True)

        if total_raw:
            try:
                file_infos, rejected_mimes = hydrus.filter_by_mime(
                    hash_list,
                    progress_callback=lambda d, t: inline_progress("Filtering metadata", d, t),
                )
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
                print(
                    _c(f"Fetching metadata for {len(extra_hash_values)} extra hashes...  ", DIM), end="\r", flush=True
                )
                try:
                    extra_infos, extra_rejected_mimes = hydrus.filter_by_mime(extra_hash_values)
                    extra_infos = hydrus.resolve_paths(extra_infos)
                except HydrusConnectionError as exc:
                    print(
                        _c(f"\nERROR: Hydrus connection lost while fetching extra hashes: {exc}", RED), file=sys.stderr
                    )
                    return 1

                extra_count = len(extra_infos)
                if extra_rejected_mimes:
                    rejected_list = ", ".join(sorted(extra_rejected_mimes))
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
    )

    if mode != "push_only" and actionable_count == 0:
        print(_c("No actionable files. Exiting.", YELLOW))
        return 0

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

    # Wait for the background libraries to finish loading before starting database work
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

    from hyvis.inference import PhaseStats, infer_files, push_cached_to_hydrus

    total_infer_ok = total_infer_err = 0
    total_push_ok = total_push_err = 0
    total_skipped = 0
    run_status = "done"

    with Database(db_path) as db:
        db.start_run(run_id=run_id, config_toml=config_toml)

        for model_cfg in cfg.inference.models:
            print(_c(f"  Using model: {model_cfg.model_id}", BOLD, CYAN))
            print()

            # --- Step 0: push previously-inferred-but-not-pushed ---
            if mode in ("default", "push_only"):
                pending_hashes = db.bulk_push_pending(model_cfg.model_id)
                touched_hashes.update(pending_hashes)

                if pending_hashes and mode == "default":
                    print(_c(f"  Pushing {len(pending_hashes)} previously cached result(s) to Hydrus...", DIM))
                    progress = Progress(total=len(pending_hashes))
                    catchup_stats = await push_cached_to_hydrus(
                        model_cfg,
                        config=cfg,
                        hydrus=hydrus,
                        db=db,
                        progress=progress,
                        force=args.force,
                    )
                    progress.finish()
                    total_push_ok += catchup_stats.push_ok
                    # Note: we don't add catchup_stats.push_errors here because Phase 2
                    # will retry them and report the final outcome.
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
                # Note: we don't add infer_stats.push_errors yet; Phase 2 will account for them.

                print(f"\n  {_c('Inference summary:', BOLD)}")
                print(f"    Cached OK : {infer_stats.ok}")
                print(f"    Errors    : {infer_stats.errors}")
                print(f"    Skipped   : {infer_stats.skipped}")
                print(f"    Tags      : {infer_stats.total_tags_cached}")

                if infer_stats.aborted_early:
                    run_status = "aborted"
                    print(_c(f"\n  ABORTED: {infer_stats.abort_reason}", RED))
                    break

            # region P2: Push to Hydrus
            # In default mode push happens interleaved inside infer_files.
            # We only run a separate push pass here if:
            #   a) push_only mode, or
            #   b) there are pending results (e.g. inference succeeded but push failed).
            pending_after = db.bulk_push_pending(model_cfg.model_id)
            run_push_pass = mode == "push_only" or (mode == "default" and len(pending_after) > 0)

            if run_push_pass:
                if args.force:
                    # In force mode we re-push everything that was ever inferred successfully.
                    row = db.conn.execute(
                        "SELECT COUNT(*) FROM file_model_results WHERE model_id = ? AND infer_success = 1",
                        (model_cfg.model_id,),
                    ).fetchone()
                    push_total = row[0] if row else 0
                else:
                    push_total = len(pending_after)

                if push_total == 0 and mode == "push_only":
                    print(_c("  No cached results pending push for this model.", YELLOW))
                    continue

                if infer_stats is not None and infer_stats.hydrus_suspended:
                    print(
                        _c(
                            f"\n  Hydrus became unreachable during inference. Retrying {push_total} pending pushes...",
                            YELLOW,
                        )
                    )
                elif mode == "default" and push_total > 0:
                    print(_c(f"\n  {push_total} result(s) still pending push. Retrying...", YELLOW))
                else:
                    print()

                print(_c("  Pushing to Hydrus...", DIM))
                progress = Progress(total=max(push_total, 1))
                push_stats = await push_cached_to_hydrus(
                    model_cfg,
                    config=cfg,
                    hydrus=hydrus,
                    db=db,
                    progress=progress,
                    force=args.force,
                )
                progress.finish()

                total_push_ok += push_stats.push_ok
                total_push_err += push_stats.push_errors

                print(f"\n  {_c('Push summary:', BOLD)}")
                print(f"    Pushed OK : {push_stats.push_ok}")
                print(f"    Errors    : {push_stats.push_errors}")
                print(f"    Tags sent : {push_stats.total_tags_pushed}")

                if push_stats.push_errors:
                    print(
                        _c(
                            f"  {push_stats.push_errors} file(s) failed to push. Run again or use --push-only to retry.",
                            YELLOW,
                        )
                    )

                if push_stats.aborted_early:
                    run_status = "aborted"
                    print(_c(f"\n  ABORTED: {push_stats.abort_reason}", RED))
                    break

            # Account for push errors if Phase 2 was skipped
            # (meaning everything succeeded or nothing was tried).
            if mode == "default" and not run_push_pass:
                if infer_stats is not None:
                    total_push_err += infer_stats.push_errors

        # region Tag removal/cleanup
        # NOTE: We don't remove tags alongside pushing others because multi-model configurations may fail for one model
        if mode in ("default", "push_only") and cfg.hydrus.remove_tags and touched_hashes:
            model_ids = [m.model_id for m in cfg.inference.models]

            # Check which files fully completed all models
            successful_hashes = list(db.bulk_fully_completed(list(touched_hashes), model_ids))

            if successful_hashes:
                print(_c(f"  Cleaning up search tags for {len(successful_hashes)} fully processed file(s)...", DIM))
                r_cfg = cfg.hydrus.remove_tags
                try:
                    hydrus.delete_tags(
                        hashes=successful_hashes,
                        service_keys=r_cfg.tag_service_keys,
                        tags=r_cfg.tags,
                    )
                    print(_c("  Cleanup completed successfully.", GREEN))
                except Exception as exc:
                    logger.error("Failed to remove tags %s: %s", r_cfg.tags, exc)
                    print(_c("  Cleanup completed with errors.", RED))
                print()

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
