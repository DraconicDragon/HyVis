"""
push_service.py — Resilient worker that drains the Hydrus push queue.

Features:
  - Consumes tasks directly from push_queue in SQLite.
  - Automatically suspends if Hydrus goes offline, leaving the queue intact.
  - Increments attempt counter on unrecoverable errors so the queue never enters an infinite loop.
  - Catches 404s (deleted files) and cleans them from the queue permanently.
  - Guarantees successful tasks are always flushed from SQLite on error, disconnect, or Ctrl+C.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time

from hyvis.db import Database
from hyvis.hydrus import HydrusClient, HydrusConnectionError, HydrusError
from hyvis.logging_utils import GREEN, RED, YELLOW, _c
from hyvis.progress import Progress

logger = logging.getLogger(__name__)


async def _wait_for_hydrus_reconnect(hydrus: HydrusClient, wait_interval: float) -> None:
    """Block execution until Hydrus is reachable again."""
    start_time = time.monotonic()
    while True:
        elapsed = int(time.monotonic() - start_time)
        m, s = divmod(elapsed, 60)
        time_str = f"{m:02d}:{s:02d}" if m > 0 else f"{s}s"
        msg = f"  Waiting for Hydrus... ({time_str} elapsed; checking every {wait_interval}s; Ctrl+C to abort)"

        sys.stdout.write(f"\r{_c(msg, YELLOW)}{' ' * 5}")
        sys.stdout.flush()

        try:
            await asyncio.sleep(wait_interval)
            hydrus.verify_connection()
            sys.stdout.write(f"\r{' ' * 80}\r")
            print(_c("\n  Connected successfully! Resuming push.", GREEN))
            return
        except (KeyboardInterrupt, asyncio.CancelledError):
            sys.stdout.write("\n")
            raise KeyboardInterrupt
        except Exception:
            pass


async def drain_push_queue(
    db: Database,
    hydrus: HydrusClient,
    *,
    batch_size: int = 50,
    wait_for_hydrus: bool = True,
    wait_interval: float = 5.0,
    progress: Progress | None = None,
) -> tuple[int, int]:
    """
    Drain pending items from push_queue until empty.
    Returns: (total_pushed_ok, total_errors)
    """
    total_ok = 0
    total_err = 0

    while True:
        # 1. Fetch next batch of actionable tasks (attempts < 3)
        tasks = db.fetch_push_batch(limit=batch_size, max_attempts=3)
        if not tasks:
            break

        successful_tasks: list[tuple[str, str, str]] = []

        try:
            for file_hash, service_key, action, tags in tasks:
                if not tags:
                    successful_tasks.append((file_hash, service_key, action))
                    continue

                try:
                    if action == "add_tags":
                        hydrus.add_tags([file_hash], service_key, tags)
                    elif action == "delete_tags":
                        hydrus.delete_tags([file_hash], [service_key], tags)

                    successful_tasks.append((file_hash, service_key, action))
                    total_ok += 1
                    if progress:
                        progress.tick(processed=1)

                except HydrusConnectionError as exc:
                    if wait_for_hydrus:
                        print(_c("\n  Hydrus connection lost during push.", RED))
                        await _wait_for_hydrus_reconnect(hydrus, wait_interval)
                        break  # Retry remaining items in next while loop iteration
                    else:
                        logger.warning("Hydrus offline. Suspending push: %s", exc)
                        return total_ok, total_err

                except HydrusError as exc:
                    total_err += 1
                    if progress:
                        progress.tick(errors=1)

                    # 404: File was deleted from Hydrus!
                    if hasattr(exc, "status_code") and exc.status_code == 404:
                        logger.warning("File %s was deleted from Hydrus; purging from queue.", file_hash[:8])
                        db.mark_file_status(file_hash, "deleted_from_hydrus")
                        db.clear_file_from_push_queue(file_hash)
                    else:
                        logger.error("Failed pushing to Hydrus for %s: %s", file_hash[:8], exc)
                        # Increment attempts so this broken item does not stall the queue forever
                        db.record_push_attempt_error(file_hash, service_key, action, str(exc))

        finally:
            # 2. Always clear successful tasks, even on early return, disconnect, or Ctrl+C
            if successful_tasks:
                db.remove_from_push_queue(successful_tasks)

    return total_ok, total_err
