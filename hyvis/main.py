"""
main.py Entry point.

Usage:
    python3 main.py CONFIG_PATH [--yes] [--force] [...etc...]

Arguments:
    CONFIG_PATH     Path to the TOML configuration file.

Options:
    --api-url       Override hydrus.api_url from config.
    --api-key       Override hydrus.api_key from config.
    --extra-hash-file  Path to a text file containing one sha256 hash per line. (for wd-e621-hydrus-tagger parity)
    --yes           Skip all interactive confirmation prompts.
    --force         Re-process files even if already cached (infer + push).
    --infer-only    Run inference and cache results; do not push to Hydrus.
    --push-only     Push cached results to Hydrus; do not run inference.
    --log-level     DEBUG, INFO, WARNING, ERROR (default: config or WARNING).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
import signal
import sys
import uuid
from pathlib import Path
from typing import Any

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


def _parse_version_number(value: str) -> int | None:
    try:
        return int(str(value).strip())
    except ValueError:
        return None


# region CLI


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="hyvis",
        description="Tag files from Hydrus using image tagging models.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("config", metavar="CONFIG_PATH", type=Path, help="Path to the TOML configuration file.")
    parser.add_argument("--api-url", default=None, help="Override hydrus.api_url from config.")
    parser.add_argument("--api-key", default=None, help="Override hydrus.api_key from config.")
    parser.add_argument(
        "--extra-hash-file",
        default=None,
        type=Path,
        help="Path to a text file containing one sha256 hash per line. (for wd-e621-hydrus-tagger parity)",
    )
    parser.add_argument("--yes", "-y", action="store_true", help="Skip all confirmation prompts.")
    parser.add_argument("--force", "-f", action="store_true", help="Ignore the DB cache; re-process all matched files.")
    parser.add_argument("--infer-only", action="store_true", help="Run inference only; do not push results to Hydrus.")
    parser.add_argument("--push-only", action="store_true", help="Push cached results to Hydrus; skip inference.")
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: config or WARNING).",
    )
    return parser.parse_args()


# region Hydrus connection helpers


def _connect_hydrus(cfg: Any, args: argparse.Namespace) -> tuple[Any, dict[str, str], str, str, str]:
    """
    Connect to Hydrus, verify the key, and return:
        (hydrus_client, service_name_by_key, hydrus_version, api_version, boot_time)
    Exits on connection failure.
    """
    from .hydrus import HydrusClient, HydrusConnectionError, HydrusError

    hydrus = HydrusClient(cfg.hydrus.api_url, cfg.hydrus.api_key)

    print(_c("Connecting to Hydrus...", DIM), end="\r", flush=True)
    try:
        hydrus.verify_connection()
    except HydrusConnectionError as exc:
        print(_c(f"\nERROR: Cannot connect to Hydrus: {exc}", RED), file=sys.stderr)
        sys.exit(1)
    except HydrusError as exc:
        print(_c(f"\nERROR: Hydrus API error: {exc}", RED), file=sys.stderr)
        sys.exit(1)

    hydrus_version = api_version = boot_time = "unknown"
    logger = logging.getLogger(__name__)

    try:
        version_info = hydrus.get_version_info()
        hydrus_version = str(
            version_info.get("hydrus_version")
            or version_info.get("client_version")
            or version_info.get("version")
            or "unknown"
        )
        api_version = str(version_info.get("api_version") or version_info.get("version") or "unknown")
    except Exception as exc:
        logger.warning("Hydrus version lookup failed: %s", exc)

    hydrus_version_number = _parse_version_number(hydrus_version)
    if hydrus_version_number is not None and hydrus_version_number >= 672:
        try:
            client_info = hydrus.get_client_info()
            boot_time_value = client_info.get("boot_time")
            if isinstance(boot_time_value, (int, float)):
                from .hydrus import format_boot_time

                boot_time = format_boot_time(float(boot_time_value))
        except Exception as exc:
            logger.warning("Hydrus client info lookup failed: %s", exc)
    else:
        boot_time = "(requires Hydrus v672+ / API v92+)"

    try:
        services = hydrus.get_services().get("services", {})
        service_name_by_key = {key: str(info.get("name", key)) for key, info in services.items()}
    except Exception as exc:
        logger.warning("Hydrus get_services failed: %s", exc)
        service_name_by_key = {}

    return hydrus, service_name_by_key, hydrus_version, api_version, boot_time


# region Confirmation screen


def _print_confirmation(
    config: Any,
    file_count: int,
    extra_hash_count: int,
    force: bool,
    mode: str,
    service_name_by_key: dict[str, str],
    hydrus_version: str,
    api_version: str,
    boot_time: str,
) -> None:
    inf = config.inference
    hydrus = config.hydrus

    mode_label = {
        "default": "Infer + Push",
        "infer_only": "Infer only  (Hydrus push skipped)",
        "push_only": "Push only   (no inference)",
    }.get(mode, mode)

    print()
    print(_c("  ═══ HyVis | Operation Confirmation ════════════════════════════════", BOLD, CYAN))
    print()
    print(_c("  Mode", BOLD) + f"  {_c(mode_label, YELLOW, BOLD)}")
    print()

    # Hydrus
    print(_c(f"  Hydrus v{hydrus_version} | API v{api_version}", BOLD))
    print(f"    URL        {_c(hydrus.api_url, CYAN)}")
    print(f"    Boot time  {_c(boot_time, GREEN)}")
    print()

    if mode != "push_only":
        # Queries
        print(_c("  File Queries", BOLD))
        for q in hydrus.file_queries:
            if q.tag_service_keys:
                names = []
                for key in q.tag_service_keys:
                    name = service_name_by_key.get(key, "(unknown service)")
                    names.append(f"{_c(name, BOLD, CYAN)} {_c(key, DIM)}")
                svc = ", ".join(names)
            else:
                svc = _c("(all known tags)", DIM)
            print(f"    service  {svc}")
            for tag in q.tags:
                print(f"      tag    {tag}")
        print()

        # File count
        count_col = YELLOW if file_count == 0 else GREEN
        forced_note = "  (--force: cache bypassed)" if force else ""
        extra_note = (
            f"  (+{extra_hash_count} extra hash{'es' if extra_hash_count != 1 else ''})" if extra_hash_count else ""
        )
        print(
            f"  {_c('Files to process', BOLD)}  {_c(str(file_count), count_col, BOLD)}"
            f"{_c(extra_note, DIM)}{_c(forced_note, DIM)}"
        )
        print()

    if mode != "infer_only":
        if mode == "push_only":
            # Output services
            print(_c("  Output Tag Services (global)", BOLD))
            for svc in hydrus.output_tag_services:
                name = service_name_by_key.get(svc.key, "(unknown service)")
                print(f"    {_c(name, BOLD, CYAN)} {_c(svc.key, DIM)}")
            print()

    if mode != "push_only":
        # Models
        print(_c("  Models", BOLD))
        for i, m in enumerate(inf.models, 1):
            print(f"    {i}. {_c(m.model_id, BOLD)}")
            print(f"       source           {m.source}")
            print(
                f"                        device={m.device}  backend={m.backend or 'auto'}  precision={m.precision}  batch={m.batch_size}"
            )
            eff_svcs = config.resolved_output_tag_services(m)
            print("       output services")
            for s in eff_svcs:
                name = service_name_by_key.get(s.key, s.key)
                print(f"         {_c(name, BOLD, CYAN)} {_c(s.key, DIM)}")
            if m.output_filter is not None:
                print(f"       output_filter    (overrides: {', '.join(m.output_filter._raw_keys)})")
        print()

        # Output Filter
        of = config.output_filter
        print(_c("  Output Filter (global)", BOLD))
        print(f"    prefer TLT          {of.prefer_tag_level_thresholds}")
        print(f"    TLT offset          {of.tag_level_threshold_relative_offset}")
        print(f"    default threshold   {of.default_threshold}")
        if of.category_thresholds:
            print("    category thresholds")
            for cat, cfg_ in of.category_thresholds.items():
                tlt_note = _c(" [overrides TLT]", DIM) if cfg_.override_tlt else ""
                print(f"      {cat:<14} {cfg_.threshold:.2f}{tlt_note}")
        cats = of.output_categories
        print(f"    output categories   {', '.join(cats) if cats else '(all)'}")

        # tag inclusions/exclusions
        if of.include_tags:
            print(_c(f"    include tags        {', '.join(of.include_tags)}", GREEN))
        if of.exclude_tags:
            print(_c(f"    exclude tags        {', '.join(of.exclude_tags)}", RED))

        print()

        if of.tag_prefix_mapping:
            print("    tag prefix mapping")
            for cat, prefix in of.tag_prefix_mapping.items():
                display_prefix = f"'{prefix}'" if prefix else _c("(none)", DIM)
                print(f"      {cat:<14} → {display_prefix}")
        if of.max_tags_per_category:
            print(f"    max tags / category  {of.max_tags_per_category}")
        print(f"\n    log level           {_c(config.hyvis.log_level, YELLOW, BOLD)}")
        print()

    # Backup Reminder
    print(_c("      It is strongly recommended to create/update your Hydrus backup.", RED))
    print()


# region Main coroutine


async def main() -> int:
    args = _parse_args()

    if args.infer_only and args.push_only:
        print(_c("ERROR: --infer-only and --push-only are mutually exclusive.", RED), file=sys.stderr)
        return 1

    mode = "infer_only" if args.infer_only else "push_only" if args.push_only else "default"

    # Load + validate config
    if not args.config.exists():
        print(_c(f"ERROR: Config file not found: {args.config}", RED), file=sys.stderr)
        return 1

    from .config import AppConfig

    try:
        cfg = AppConfig.from_file(args.config)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        print(_c(f"ERROR: Failed to parse config: {exc}", RED), file=sys.stderr)
        return 1

    errors = cfg.validate()
    if errors:
        print(_c("ERROR: Invalid configuration:", RED), file=sys.stderr)
        for e in errors:
            print(f"  • {e}", file=sys.stderr)
        return 1

    # Logging
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
    hydrus, service_name_by_key, hydrus_version, api_version, boot_time = _connect_hydrus(cfg, args)

    if mode != "push_only" and cfg.hydrus.file_queries and not any(q.tags for q in cfg.hydrus.file_queries):
        print(
            _c("  Warning: all configured Hydrus file queries are empty; only extra hashes can produce files.", YELLOW)
        )

    # For push-only we don't need to collect files from Hydrus.
    file_infos = []
    actionable_count = 0
    extra_count = 0

    if mode != "push_only":
        from .extra_hashes import load_extra_hashes

        # Collect candidate files
        from .hydrus import HydrusConnectionError

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

        def _inline_progress(label: str, done: int, total: int) -> None:
            pct = done / max(total, 1) * 100
            line = _c(f"  {label}: {done}/{total} ({pct:.0f}%)", DIM)
            sys.stdout.write(f"\r{line}   ")
            sys.stdout.flush()

        def _clear_line() -> None:
            sys.stdout.write("\r" + " " * 80 + "\r")
            sys.stdout.flush()

        if total_raw:
            try:
                file_infos, rejected_mimes = hydrus.filter_by_mime(
                    hash_list,
                    progress_callback=lambda d, t: _inline_progress("Filtering metadata", d, t),
                )
            except HydrusConnectionError as exc:
                print(_c(f"\nERROR: Hydrus connection lost during metadata fetch: {exc}", RED), file=sys.stderr)
                return 1

            _clear_line()

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
                    progress_callback=lambda d, t: _inline_progress("Resolving paths", d, t),
                )
            except HydrusConnectionError as exc:
                print(_c(f"\nERROR: Hydrus connection lost during path resolution: {exc}", RED), file=sys.stderr)
                return 1

            _clear_line()

            no_path_count = sum(1 for fi in file_infos if not fi.local_path)
            if no_path_count:
                print(f"  {_c(f'Warning: {no_path_count} files have no local path (will be skipped)', YELLOW)}")

            actionable_count = sum(1 for fi in file_infos if fi.local_path)

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
            else:
                print(_c("  Warning: extra hash file was empty.", YELLOW))

        if not file_infos:
            if args.extra_hash_file is not None:
                print(_c("\nNo files matched the configured queries or extra-hash list. Nothing to do.", YELLOW))
            else:
                print(_c("\nNo files matched the configured queries. Nothing to do.", YELLOW))
            return 0

    # Confirmation screen
    _print_confirmation(
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

    # Prompt to close Hydrus (free up RAM for the model).
    if mode in ("infer_only", "default"):
        print()
        print(_c("  File paths have been collected from Hydrus.", GREEN))
        if mode == "infer_only":
            print(_c("  You may now close Hydrus to free memory before inference begins.", YELLOW))
            prompt_text = "  Press ENTER when ready to start inference: "
        else:
            print(
                _c(
                    "  You may now close Hydrus to free memory | inference will cache results and retry pushes if needed.",
                    YELLOW,
                )
            )
            prompt_text = "  Press ENTER to start inference: "

        if not args.yes:
            previous_sigint_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, signal.default_int_handler)
            try:
                input(prompt_text)
            except (KeyboardInterrupt, EOFError):
                print(_c("\n\nAborted.", YELLOW))
                return 0
            finally:
                signal.signal(signal.SIGINT, previous_sigint_handler)

    print()

    # Database
    from .db import Database

    run_id = str(uuid.uuid4())
    try:
        config_toml = args.config.read_text()
    except OSError as exc:
        print(_c(f"ERROR: Failed to read config file: {exc}", RED), file=sys.stderr)
        return 1

    db_path = Path(cfg.database.path)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path

    from .inference import PhaseStats, infer_files, push_cached_to_hydrus
    from .progress import Progress

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

            # --- Phase 1: Inference ---
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

            # --- Phase 2: Push to Hydrus ---
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

            # Account for push errors if Phase 2 was skipped (meaning everything succeeded
            # or nothing was tried).
            if mode == "default" and not run_push_pass:
                if infer_stats is not None:
                    total_push_err += infer_stats.push_errors

    # Final stats
    print()
    print(_c("  ══ Run Complete ══════════════════════════════", BOLD))
    print(f"  Status    : {_c(run_status, GREEN if run_status == 'done' else YELLOW)}")
    print(f"  Run ID    : {_c(run_id, DIM)}")
    if mode in ("default", "infer_only"):
        print(f"  Inferred  : {total_infer_ok} ok / {total_infer_err} errors / {total_skipped} skipped")
    if mode in ("default", "push_only"):
        print(f"  Pushed    : {total_push_ok} ok / {total_push_err} errors")
        if total_push_err:
            print(_c("  Tip: run with --push-only to retry failed pushes.", DIM))
    print()

    return 0 if run_status == "done" else 1


# region Entry point


def cli() -> None:
    raise SystemExit(asyncio.run(main()))


if __name__ == "__main__":
    cli()
