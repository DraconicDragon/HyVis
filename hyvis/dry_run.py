"""
dry_run.py — Read-only database shadow for test and dry-run execution.
Guarantees zero SQLite disk writes while maintaining an in-memory overlay
so multi-model inference checks and pipeline counters work completely naturally.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from hyvis.db import _DEFAULT_MIN_CACHE_SCORE, _SCHEMA_SQL, Database, FileStatus, PushAction

logger = logging.getLogger(__name__)


class DryRunDatabase(Database):
    """
    Read-only Database wrapper for simulation and dry-runs.
    Reads existing cache without modification and drops all write requests safely.
    """

    def __init__(self, path: str | Path, *, min_cache_score: float = _DEFAULT_MIN_CACHE_SCORE) -> None:
        super().__init__(path, min_cache_score=min_cache_score)
        self._dry_run_cached_models: set[tuple[str, str]] = set()
        self.simulated_enqueued_tags: int = 0
        self.simulated_files_with_tags: set[str] = set()

    def open(self) -> None:
        if self._conn is not None:
            return

        # If database file exists, open it in strict query-only mode
        if self._path.is_file():
            self._conn = sqlite3.connect(
                str(self._path),
                check_same_thread=False,
                timeout=30.0,
            )
            self._conn.execute("PRAGMA query_only = ON;")
            logger.debug("DryRunDatabase opened existing DB at %s (read-only mode)", self._path)
        else:
            # If DB doesn't exist yet, run on an in-memory mock so zero files are created on disk
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.execute("PRAGMA query_only = ON;")
            logger.debug("DryRunDatabase initialized in-memory mock schema (disk untouched)")

    def commit(self) -> None:
        """No-op: Never commit changes in dry run mode."""
        self._batch_uncommitted = 0

    def batch_tick(self, batch_size: int = 50) -> None:
        """No-op."""
        del batch_size

    def upsert_file(
        self,
        file_hash: str,
        *,
        file_path: str | None = None,
        mime: str | None = None,
        status: FileStatus = "active",
    ) -> None:
        """No-op: Drop file metadata persistence."""
        del file_hash, file_path, mime, status

    def mark_file_status(self, file_hash: str, status: FileStatus) -> None:
        """No-op."""
        del file_hash, status

    def save_raw_cache(
        self,
        file_hash: str,
        model_id: str,
        category_scores: dict[str, dict[str, float]],
        *,
        enabled: bool = True,
    ) -> None:
        """
        Record completion in an in-memory set so that multi-model dependencies
        (e.g. checking if Model 1 finished before Model 2) resolve naturally.
        """
        del category_scores, enabled
        self._dry_run_cached_models.add((file_hash, model_id))

    def has_raw_cache(self, file_hash: str, model_id: str) -> bool:
        """Check in-memory dry-run completions first, then fall back to real DB cache."""
        if (file_hash, model_id) in self._dry_run_cached_models:
            return True
        return super().has_raw_cache(file_hash, model_id)

    def bulk_already_inferred(self, file_hashes: Sequence[str], model_id: str) -> set[str]:
        """Combine real DB cached entries with in-memory dry-run completions."""
        base_cached = super().bulk_already_inferred(file_hashes, model_id)
        simulated = {h for h in file_hashes if (h, model_id) in self._dry_run_cached_models}
        return base_cached | simulated

    def enqueue_push(
        self,
        file_hash: str,
        service_key: str,
        tags: Sequence[str],
        action: PushAction = "add_tags",
    ) -> None:
        """Track tag counts and file matches in-memory without touching SQLite."""
        del service_key, action
        if tags:
            self.simulated_enqueued_tags += len(tags)
            self.simulated_files_with_tags.add(file_hash)

    def record_push_attempt_error(self, file_hash: str, service_key: str, action: str, error_msg: str) -> None:
        del file_hash, service_key, action, error_msg

    def remove_from_push_queue(self, tasks: Sequence[tuple[str, str, str]]) -> None:
        del tasks

    def clear_file_from_push_queue(self, file_hash: str) -> None:
        del file_hash
