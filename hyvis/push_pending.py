"""
push_pending.py  Entry point for `hyvis-push-pending`.

Reads all files with infer_success=1 / push_success=0  OR
push_success=1 / cleanup_done=0 from the DB, reconstructs the
original run config for each file, and pushes / cleans up accordingly.

Reads the config for each file to get correct tags and output filter settings from DB.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="hyvis-push-pending",
        description="Push unpushed inference results and retry failed tag cleanups from the HyVis database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db-path",
        default=None,
        type=Path,
        help="Path to the HyVis SQLite database (default: data/hyvis.db relative to cwd).",
    )
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt.")
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    from hyvis.config import AppConfig
    from hyvis.db import Database
    from hyvis.hydrus import HydrusClient, HydrusConnectionError, HydrusError
    from hyvis.inference import extract_tags
    from hyvis.logging_utils import BOLD, CYAN, DIM, GREEN, RED, YELLOW, _c  # noqa: F401

    db_path = args.db_path or Path.cwd() / "data" / "hyvis.db"
    if not db_path.exists():
        print(_c(f"ERROR: Database not found at {db_path}", RED), file=sys.stderr)
        return 1

    # === DEBUG RESET ===
    print(_c("DEBUG: Resetting push_success and cleanup_done to 0 for all records...", YELLOW))
    with Database(db_path) as db:
        db.conn.execute("UPDATE file_model_results SET push_success = 0, cleanup_done = 0")
        db.commit()

    with Database(db_path) as db:
        pending_push = db.get_pending_push()
        initial_pending_cleanup = db.get_pending_cleanup()
        failed_inferences = db.get_failed_inferences()
        blocked_by_failures = db.get_cleanup_blocked_by_failed_inference()
        held_by_pending = db.get_cleanup_held_by_pending_pushes_count()

    if not pending_push and not initial_pending_cleanup:
        print("Nothing pending. All files are pushed and cleaned up.")
        if failed_inferences:
            print()
            print(
                _c(
                    f"  Note: There are {len(failed_inferences)} result(s) with failed inferences (infer_success = 0).",
                    YELLOW,
                )
            )
            print(_c("        These require re-running the main inference pipeline to resolve.", YELLOW))
            print()
        return 0

    # Calculate estimated cleanups resulting from the pending pushes
    est_cleanup_active = 0
    est_cleanup_auto = 0

    if pending_push:
        with Database(db_path) as db:
            # Gather unique (file_hash, run_id) pairs to estimate unique file cleanups
            unique_file_runs = {(file_hash, run_id) for file_hash, _, run_id in pending_push}
            unique_run_ids = {run_id for _, run_id in unique_file_runs}

            # Map run_id to whether it requires actual cleanup or auto-marking
            run_cleanup_requires_action: dict[str, bool] = {}
            for r_id in unique_run_ids:
                config_toml = db.get_config_toml(r_id)
                if config_toml:
                    try:
                        cfg = AppConfig.from_toml_string(config_toml)
                        run_cleanup_requires_action[r_id] = bool(cfg.hydrus.remove_tags)
                    except Exception:
                        run_cleanup_requires_action[r_id] = False
                else:
                    run_cleanup_requires_action[r_id] = False

            # Count the unique file cleanups falling into each category
            for _, run_id in unique_file_runs:
                if run_cleanup_requires_action.get(run_id, False):
                    est_cleanup_active += 1
                else:
                    est_cleanup_auto += 1

    print()
    print(_c(f"  Pending pushes  : {len(pending_push)}", BOLD))
    print(_c(f"  Pending cleanups: {len(initial_pending_cleanup)}", BOLD))

    if pending_push:
        est_total = est_cleanup_active + est_cleanup_auto
        details = []
        if est_cleanup_active > 0:
            details.append(f"{est_cleanup_active} via Hydrus tag removal")
        if est_cleanup_auto > 0:
            details.append(f"{est_cleanup_auto} auto-marked completed")
        details_str = f" ({', '.join(details)})" if details else ""
        print(_c(f"  Estimated additional cleanups (after pushes succeed): {est_total}{details_str}", DIM))

    # Print Warnings/Diagnostics for stuck states
    if failed_inferences:
        print()
        print(
            _c(
                f"  WARNING: Found {len(failed_inferences)} result(s) with failed inferences (infer_success = 0).",
                YELLOW,
            )
        )
        print(_c("           These cannot be pushed or cleaned up until you re-run inference on those files.", YELLOW))

    if blocked_by_failures:
        print()
        print(
            _c(
                f"  WARNING: Cleanup is blocked for {len(blocked_by_failures)} file(s) because at least one of their models failed inference.",
                RED,
            )
        )
        print(_c("           (Cleanup requires ALL configured models for a file to succeed).", RED))

    if held_by_pending and pending_push:
        print()
        print(
            _c(
                f"  Note: {held_by_pending} cleanup(s) are temporarily held waiting for pending pushes to complete.",
                DIM,
            )
        )
        print(_c("        These will execute immediately after the pushes succeed during this run.", DIM))
    print()

    if not args.yes:
        try:
            input("  Press ENTER to continue, or Ctrl+C to abort: ")
        except (KeyboardInterrupt, EOFError):
            print(_c("\nAborted.", YELLOW))
            return 0
        print()

    # region Group by run_id so we deserialize each config only once
    # For push: group (file_hash, model_id) by run_id
    push_by_run: dict[str, list[tuple[str, str]]] = {}
    for file_hash, model_id, run_id in pending_push:
        push_by_run.setdefault(run_id, []).append((file_hash, model_id))

    # region Push pass
    total_push_ok = 0
    total_push_err = 0

    if pending_push:
        with Database(db_path) as db:
            for run_id, entries in push_by_run.items():
                config_toml = db.get_config_toml(run_id)
                if config_toml is None:
                    logger.warning("No config found for run_id %s, skipping %d file(s)", run_id, len(entries))
                    total_push_err += len(entries)
                    continue

                try:
                    cfg = AppConfig.from_toml_string(config_toml)
                except Exception as exc:
                    logger.error("Failed to parse saved config for run_id %s: %s", run_id, exc)
                    total_push_err += len(entries)
                    continue

                hydrus = HydrusClient(cfg.hydrus.api_url, cfg.hydrus.api_key)
                try:
                    hydrus.verify_connection()
                except (HydrusConnectionError, HydrusError) as exc:
                    print(_c(f"  ERROR: Cannot reach Hydrus at {cfg.hydrus.api_url}: {exc}", RED), file=sys.stderr)
                    total_push_err += len(entries)
                    continue

                # Build model_id → ModelConfig map for this run's config
                model_cfg_by_id = {m.model_id: m for m in cfg.inference.models}

                for file_hash, model_id in entries:
                    model_cfg = model_cfg_by_id.get(model_id)
                    if model_cfg is None:
                        logger.warning("model_id %s not found in saved config for run %s, skipping", model_id, run_id)
                        total_push_err += 1
                        continue

                    cached = db.get_cached_inference(file_hash, model_id)
                    if cached is None:
                        logger.warning("No cached inference for %s / %s, skipping", file_hash[:8], model_id)
                        total_push_err += 1
                        continue

                    eff = cfg.resolved_output_filter(model_cfg)
                    tag_records = extract_tags(cached, output_filter=eff)
                    prefixed_tags = [tr.prefixed_tag for tr in tag_records]

                    try:
                        for svc in cfg.resolved_output_tag_services(model_cfg):
                            hydrus.add_tags(hashes=[file_hash], service_key=svc.key, tags=prefixed_tags)
                        db.record_push_result(file_hash=file_hash, model_id=model_id, success=True)
                        db.commit()
                        total_push_ok += 1
                        print(_c(f"  pushed {file_hash[:8]}  ({len(prefixed_tags)} tags)", DIM))
                    except Exception as exc:
                        logger.error("Push failed for %s: %s", file_hash[:8], exc)
                        db.record_push_result(
                            file_hash=file_hash, model_id=model_id, success=False, error_message=str(exc)[:500]
                        )
                        db.commit()
                        total_push_err += 1

        print()
        print(_c(f"  Push complete: {total_push_ok} ok / {total_push_err} errors", BOLD))
        print()
    else:
        print("  No pending pushes.")
        print()

    # region Cleanup pass
    total_cleanup_ok = 0
    total_cleanup_err = 0

    with Database(db_path) as db:
        pending_cleanup = db.get_pending_cleanup()

    if pending_cleanup:
        # Group file_hashes by run_id (we only need remove_tags from the config)
        cleanup_by_run: dict[str, list[tuple[str, str]]] = {}
        for file_hash, model_id, run_id in pending_cleanup:
            cleanup_by_run.setdefault(run_id, []).append((file_hash, model_id))

        with Database(db_path) as db:
            for run_id, entries in cleanup_by_run.items():
                config_toml = db.get_config_toml(run_id)
                if config_toml is None:
                    logger.warning(
                        "No config found for run_id %s, skipping cleanup of %d file(s)", run_id, len(entries)
                    )
                    total_cleanup_err += len(entries)
                    continue

                try:
                    cfg = AppConfig.from_toml_string(config_toml)
                except Exception as exc:
                    logger.error("Failed to parse saved config for run_id %s: %s", run_id, exc)
                    total_cleanup_err += len(entries)
                    continue

                hashes = list({fh for fh, _ in entries})
                model_ids = list({mid for _, mid in entries})

                if not cfg.hydrus.remove_tags:
                    # This run had no remove_tags configured; mark as done so it stops appearing
                    db.mark_cleanup_done(hashes, model_ids, done=True)
                    total_cleanup_ok += len(hashes)
                    print(_c(f"  marked {len(hashes)} file(s) as cleaned (no remove_tags) (run {run_id[:8]})", DIM))
                    continue

                hydrus = HydrusClient(cfg.hydrus.api_url, cfg.hydrus.api_key)
                try:
                    hydrus.verify_connection()
                except (HydrusConnectionError, HydrusError) as exc:
                    print(_c(f"  ERROR: Cannot reach Hydrus at {cfg.hydrus.api_url}: {exc}", RED), file=sys.stderr)
                    total_cleanup_err += len(entries)
                    continue

                r_cfg = cfg.hydrus.remove_tags

                try:
                    hydrus.delete_tags(hashes=hashes, service_keys=r_cfg.tag_service_keys, tags=r_cfg.tags)
                    db.mark_cleanup_done(hashes, model_ids, done=True)
                    total_cleanup_ok += len(hashes)
                    print(_c(f"  cleaned {len(hashes)} file(s) (run {run_id[:8]})", DIM))
                except Exception as exc:
                    logger.error("Cleanup failed for run %s: %s", run_id[:8], exc)
                    total_cleanup_err += len(hashes)

        print(_c(f"  Cleanup complete: {total_cleanup_ok} ok / {total_cleanup_err} errors", BOLD))
        print()
    else:
        print("  No pending cleanups.")
        print()

    return 0 if (total_push_err == 0 and total_cleanup_err == 0) else 1


def cli() -> None:
    args = _parse_args()
    from hyvis.logging_utils import setup_logging

    setup_logging(args.log_level)
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    cli()
