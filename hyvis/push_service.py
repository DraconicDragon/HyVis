"""
push_service.py  Centralized engine for pushing results to Hydrus and running tag cleanups.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import TYPE_CHECKING

from hyvis.config import AppConfig
from hyvis.db import Database
from hyvis.hydrus import HydrusClient, HydrusConnectionError, HydrusError
from hyvis.inference import extract_tags
from hyvis.logging_utils import BOLD, CYAN, DIM, GREEN, RED, YELLOW, _c  # noqa: F401

if TYPE_CHECKING:
    from pathlib import Path

    from hyvis.config import ModelConfig

logger = logging.getLogger(__name__)


async def _wait_for_hydrus_reconnect(hydrus: HydrusClient, wait_interval: float) -> None:
    """Helper to block execution until Hydrus is reachable again."""
    print(
        _c(
            f"  Waiting for Hydrus to re-establish... (checking every {wait_interval}s; Ctrl+C to abort)",
            YELLOW,
        )
    )
    while True:
        try:
            await asyncio.sleep(wait_interval)
            hydrus.verify_connection()
            print(_c("  Reconnected! Continuing...", GREEN))
            return
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise KeyboardInterrupt
        except Exception:
            pass


def push_and_cleanup_file(
    *,
    db: Database,
    hydrus: HydrusClient,
    config: AppConfig,
    model_cfg: ModelConfig,
    file_hash: str,
    prefixed_tags: list[str],
) -> bool:
    """
    Push tags for a single file-model result to Hydrus, record the database status,
    and immediately perform tag cleanup if all models for this file are complete.

    Returns True on success, False if pushing failed.
    """
    try:
        for svc in config.resolved_output_tag_services(model_cfg):
            hydrus.add_tags(
                hashes=[file_hash],
                service_key=svc.key,
                tags=prefixed_tags,
            )
        db.record_push_result(
            file_hash=file_hash,
            model_id=model_cfg.model_id,
            success=True,
        )
        db.commit()

        # tag cleanup if all models for this file are fully completed
        model_ids = [m.model_id for m in config.inference.models]
        if len(db.bulk_fully_completed([file_hash], model_ids)) > 0:
            if config.hydrus.remove_tags:
                r_cfg = config.hydrus.remove_tags
                try:
                    hydrus.delete_tags(
                        hashes=[file_hash],
                        service_keys=r_cfg.tag_service_keys,
                        tags=r_cfg.tags,
                    )
                except Exception as cleanup_exc:
                    logger.error("Immediate tag cleanup failed for %s: %s", file_hash[:8], cleanup_exc)
            db.mark_cleanup_done([file_hash], model_ids, done=True)
        return True

    except Exception as exc:
        logger.error(
            "Hydrus push failed for %s: %s | suspending push for remainder of inference. "
            "Run 'hyvis-push-pending' to retry failed pushes later.",
            file_hash[:8],
            exc,
        )
        db.record_push_result(
            file_hash=file_hash,
            model_id=model_cfg.model_id,
            success=False,
            error_message=str(exc)[:500],
        )
        db.commit()
        return False


async def run_push_and_cleanup(
    db_path: Path,
    *,
    api_url_override: str | None = None,
    api_key_override: str | None = None,
    wait_for_hydrus: bool = True,
    wait_interval: float = 5.0,
    consecutive_push_limit: int = 10,
    consecutive_cleanup_limit: int = 5,
    skip_confirm: bool = False,
) -> int:
    """
    Execute push and cleanup passes for all pending entries.

    If wait_for_hydrus is True, it enters a periodic loop when Hydrus is offline.
    Supports graceful KeyboardInterrupt aborts and aborts on excessive consecutive errors.
    """
    # region 1. Initial DB read
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

    # region Warn / Diagnostics
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

    # region 2. Connection Settings 
    # Resolve target Connection settings from DB configurations if overrides are not present
    target_api_url = api_url_override
    target_api_key = api_key_override

    if not target_api_url or not target_api_key:
        run_ids = set()
        if pending_push:
            run_ids.update(run_id for _, _, run_id in pending_push)
        if initial_pending_cleanup:
            run_ids.update(run_id for _, _, run_id in initial_pending_cleanup)

        first_run_id = sorted(run_ids)[0] if run_ids else None
        if first_run_id:
            with Database(db_path) as db:
                config_toml = db.get_config_toml(first_run_id)
            if config_toml:
                try:
                    cfg = AppConfig.from_toml_string(config_toml)
                    if not target_api_url:
                        target_api_url = cfg.hydrus.api_url
                    if not target_api_key:
                        target_api_key = cfg.hydrus.api_key
                except Exception:
                    pass

    if not target_api_url or not target_api_key:
        print(
            _c("ERROR: Could not resolve Hydrus Connection parameters from the database.", RED),
            file=sys.stderr,
        )
        return 1

    # region 3. Verify Connection
    hydrus = HydrusClient(target_api_url, target_api_key)
    connected = False
    first_wait_msg = True

    while not connected:
        try:
            hydrus.verify_connection()
            connected = True
            if not first_wait_msg:
                print(_c("  Connected successfully! Resuming operations.", GREEN))
                print()
        except (HydrusConnectionError, HydrusError) as exc:
            if not wait_for_hydrus:
                print(
                    _c(f"  ERROR: Cannot reach Hydrus at {target_api_url}: {exc}", RED),
                    file=sys.stderr,
                )
                return 1

            if first_wait_msg:
                print(_c(f"  ERROR: Cannot reach Hydrus at {target_api_url}", RED))
                print(
                    _c(
                        f"  Waiting for Hydrus to become available... (checking every {wait_interval}s; Press Ctrl+C to abort)",
                        YELLOW,
                    )
                )
                first_wait_msg = False

            try:
                await asyncio.sleep(wait_interval)
            except (asyncio.CancelledError, KeyboardInterrupt):
                print(_c("\n  Waiting cancelled by user.", YELLOW))
                return 0

    # region 4. Confirm Prompt
    if not skip_confirm:
        try:
            input("  Press ENTER to continue, or Ctrl+C to abort: ")
        except (KeyboardInterrupt, EOFError):
            print(_c("\n  Aborted.", YELLOW))
            return 0
        print()

    # region 5. Push Pass 
    # Group and Execute Push Pass
    total_push_ok = 0
    total_push_err = 0
    consecutive_errors = 0

    if pending_push:
        push_by_run: dict[str, list[tuple[str, str]]] = {}
        for file_hash, model_id, run_id in pending_push:
            push_by_run.setdefault(run_id, []).append((file_hash, model_id))

        try:
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

                    run_api_url = api_url_override or cfg.hydrus.api_url
                    run_api_key = api_key_override or cfg.hydrus.api_key
                    run_hydrus = HydrusClient(run_api_url, run_api_key)

                    model_cfg_by_id = {m.model_id: m for m in cfg.inference.models}

                    for file_hash, model_id in entries:
                        model_cfg = model_cfg_by_id.get(model_id)
                        if model_cfg is None:
                            logger.warning(
                                "model_id %s not found in saved config for run %s, skipping", model_id, run_id
                            )
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

                        pushed = False
                        while not pushed:
                            try:
                                for svc in cfg.resolved_output_tag_services(model_cfg):
                                    run_hydrus.add_tags(hashes=[file_hash], service_key=svc.key, tags=prefixed_tags)
                                db.record_push_result(file_hash=file_hash, model_id=model_id, success=True)
                                db.commit()
                                total_push_ok += 1
                                consecutive_errors = 0
                                print(_c(f"  pushed {file_hash[:8]}  ({len(prefixed_tags)} tags)", DIM))
                                pushed = True
                            except (HydrusConnectionError, HydrusError) as exc:
                                if wait_for_hydrus and isinstance(exc, HydrusConnectionError):
                                    print(_c(f"\n  Hydrus connection lost during push: {exc}", RED))
                                    await _wait_for_hydrus_reconnect(run_hydrus, wait_interval)
                                else:
                                    logger.error("Push failed for %s: %s", file_hash[:8], exc)
                                    db.record_push_result(
                                        file_hash=file_hash,
                                        model_id=model_id,
                                        success=False,
                                        error_message=str(exc)[:500],
                                    )
                                    db.commit()
                                    total_push_err += 1
                                    consecutive_errors += 1
                                    if consecutive_errors >= consecutive_push_limit:
                                        print(
                                            _c(
                                                f"\n  ERROR: Too many consecutive push errors ({consecutive_errors}). Aborting.",
                                                RED,
                                            ),
                                            file=sys.stderr,
                                        )
                                        return 1
                                    pushed = True

        except KeyboardInterrupt:
            print(_c("\n  Aborted during push phase.", YELLOW))
            print(_c(f"  Push summary (prior to abort): {total_push_ok} ok / {total_push_err} errors", BOLD))
            return 1

        print()
        print(_c(f"  Push complete: {total_push_ok} ok / {total_push_err} errors", BOLD))
        print()
    else:
        print("  No pending pushes.")
        print()

    # region 6. Cleanup Pass
    with Database(db_path) as db:
        pending_cleanup = db.get_pending_cleanup()

    total_cleanup_ok = 0
    total_cleanup_err = 0
    consecutive_errors = 0

    if pending_cleanup:
        cleanup_by_run: dict[str, list[tuple[str, str]]] = {}
        for file_hash, model_id, run_id in pending_cleanup:
            cleanup_by_run.setdefault(run_id, []).append((file_hash, model_id))

        try:
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
                        db.mark_cleanup_done(hashes, model_ids, done=True)
                        total_cleanup_ok += len(hashes)
                        print(
                            _c(
                                f"  marked {len(hashes)} file(s) as cleaned (no remove_tags) (run {run_id})",
                                DIM,
                            )
                        )
                        continue

                    run_api_url = api_url_override or cfg.hydrus.api_url
                    run_api_key = api_key_override or cfg.hydrus.api_key
                    run_hydrus = HydrusClient(run_api_url, run_api_key)

                    r_cfg = cfg.hydrus.remove_tags

                    cleaned = False
                    while not cleaned:
                        try:
                            run_hydrus.delete_tags(hashes=hashes, service_keys=r_cfg.tag_service_keys, tags=r_cfg.tags)
                            db.mark_cleanup_done(hashes, model_ids, done=True)
                            total_cleanup_ok += len(hashes)
                            consecutive_errors = 0
                            print(_c(f"  cleaned {len(hashes)} file(s) (run {run_id})", DIM))
                            cleaned = True
                        except (HydrusConnectionError, HydrusError) as exc:
                            if wait_for_hydrus and isinstance(exc, HydrusConnectionError):
                                print(_c(f"\n  Hydrus connection lost during cleanup: {exc}", RED))
                                await _wait_for_hydrus_reconnect(run_hydrus, wait_interval)
                            else:
                                logger.error("Cleanup failed for run %s: %s", run_id[:8], exc)
                                total_cleanup_err += len(hashes)
                                consecutive_errors += 1
                                if consecutive_errors >= consecutive_cleanup_limit:
                                    print(
                                        _c(
                                            f"\n  ERROR: Too many consecutive cleanup errors ({consecutive_errors}). Aborting.",
                                            RED,
                                        ),
                                        file=sys.stderr,
                                    )
                                    return 1
                                cleaned = True

        except KeyboardInterrupt:
            print(_c("\n  Aborted during cleanup phase.", YELLOW))
            print(
                _c(
                    f"  Cleanup summary (prior to abort): {total_cleanup_ok} ok / {total_cleanup_err} errors",
                    BOLD,
                )
            )
            return 1

        print(_c(f"  Cleanup complete: {total_cleanup_ok} ok / {total_cleanup_err} errors", BOLD))
        print()
    else:
        print("  No pending cleanups.")
        print()

    return 0 if (total_push_err == 0 and total_cleanup_err == 0) else 1
