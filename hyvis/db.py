"""
db.py — High-scale, resilient SQLite persistence for HyVis.

Features:
  - WAL mode with atomic batching (prevents disk thrashing on 100k+ files).
  - Raw un-culled inference cache with configurable noise pruning.
  - Dedicated push queue with tag merging and retry counters (resilient to Hydrus downtime, restarts, and missing files).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

logger = logging.getLogger(__name__)

_DEFAULT_MIN_CACHE_SCORE = 0.01

# region Schema Definition

_SCHEMA_SQL = """
-- 1. Tracked Files: Stores physical paths and status
CREATE TABLE IF NOT EXISTS known_files (
    file_hash       TEXT PRIMARY KEY,
    file_path       TEXT,
    mime            TEXT,
    status          TEXT NOT NULL DEFAULT 'active', -- 'active', 'missing_on_disk', 'deleted_from_hydrus'
    last_seen_at    TEXT NOT NULL
);

-- 2. Raw Inference Cache: Stores un-culled model tag predictions (pruned to min_score)
CREATE TABLE IF NOT EXISTS inference_cache (
    file_hash       TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    raw_json        TEXT NOT NULL,
    inferred_at     TEXT NOT NULL,
    PRIMARY KEY (file_hash, model_id),
    FOREIGN KEY (file_hash) REFERENCES known_files(file_hash) ON DELETE CASCADE
);

-- 3. Push Queue: Dedicated to-do list for Hydrus tag additions & cleanups
CREATE TABLE IF NOT EXISTS push_queue (
    file_hash       TEXT NOT NULL,
    service_key     TEXT NOT NULL,
    action          TEXT NOT NULL, -- 'add_tags', 'delete_tags'
    tags_json       TEXT NOT NULL, -- JSON array of tags: ["tag1", "tag2"]
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (file_hash, service_key, action)
);

CREATE INDEX IF NOT EXISTS idx_cache_lookup
    ON inference_cache(model_id, file_hash);

CREATE INDEX IF NOT EXISTS idx_queue_service
    ON push_queue(service_key);
"""

# endregion


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    """Resilient SQLite database manager designed for high file counts."""

    def __init__(self, path: str | Path, *, min_cache_score: float = _DEFAULT_MIN_CACHE_SCORE) -> None:
        self._path = Path(path)
        self._min_cache_score = max(0.0, float(min_cache_score))
        self._conn: sqlite3.Connection | None = None
        self._batch_uncommitted = 0

    # region Connection Lifecycle

    def open(self) -> None:
        if self._conn is not None:
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            timeout=30.0,  # Avoid lock contention failures
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")  # Fast & completely safe in WAL mode
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA_SQL)

        # Migration: add missing columns if upgrading from an older HyVis database
        cursor = self._conn.execute("PRAGMA table_info(known_files)")
        known_cols = {row[1] for row in cursor.fetchall()}
        if known_cols and "status" not in known_cols:
            self._conn.execute("ALTER TABLE known_files ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")

        cursor = self._conn.execute("PRAGMA table_info(push_queue)")
        queue_cols = {row[1] for row in cursor.fetchall()}
        if queue_cols and "attempts" not in queue_cols:
            self._conn.execute("ALTER TABLE push_queue ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        if queue_cols and "last_error" not in queue_cols:
            self._conn.execute("ALTER TABLE push_queue ADD COLUMN last_error TEXT")

        self._conn.commit()
        logger.debug("HyVis database opened at %s (WAL mode, min_cache_score=%.3f)", self._path, self._min_cache_score)

    def close(self) -> None:
        if self._conn is not None:
            self.commit()  # Flush any uncommitted batch before closing
            self._conn.close()
            self._conn = None
            logger.debug("HyVis database closed")

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not open. Call open() or use as context manager.")
        return self._conn

    def commit(self) -> None:
        """Force an immediate disk commit."""
        if self._conn is not None:
            self._conn.commit()
            self._batch_uncommitted = 0

    def batch_tick(self, batch_size: int = 50) -> None:
        """Increment write counter and commit every batch_size items."""
        self._batch_uncommitted += 1
        if self._batch_uncommitted >= batch_size:
            self.commit()

    # endregion

    # region File Tracking

    def upsert_file(
        self,
        file_hash: str,
        *,
        file_path: str | None = None,
        mime: str | None = None,
        status: str = "active",
    ) -> None:
        """Record or update known file metadata."""
        self.conn.execute(
            """
            INSERT INTO known_files (file_hash, file_path, mime, status, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(file_hash) DO UPDATE SET
                file_path    = COALESCE(excluded.file_path, known_files.file_path),
                mime         = COALESCE(excluded.mime, known_files.mime),
                status       = excluded.status,
                last_seen_at = excluded.last_seen_at
            """,
            (file_hash, file_path, mime, status, _now_iso()),
        )
        self.batch_tick()

    def mark_file_status(self, file_hash: str, status: str) -> None:
        """Update file status (e.g. 'missing_on_disk' or 'deleted_from_hydrus')."""
        self.conn.execute(
            "UPDATE known_files SET status = ? WHERE file_hash = ?",
            (status, file_hash),
        )
        self.batch_tick(batch_size=10)

    def get_cached_path(self, file_hash: str) -> str | None:
        """Return cached local path if it exists and status is active, else None."""
        row = self.conn.execute(
            "SELECT file_path FROM known_files WHERE file_hash = ? AND status = 'active'",
            (file_hash,),
        ).fetchone()
        return row[0] if row and row[0] else None

    def bulk_get_known_paths(self, file_hashes: Sequence[str]) -> dict[str, str]:
        """Return a mapping of {file_hash: file_path} for active known files."""
        if not file_hashes:
            return {}

        results: dict[str, str] = {}
        for chunk in self._chunk_list(file_hashes, 900):
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"""
                SELECT file_hash, file_path FROM known_files
                WHERE file_hash IN ({placeholders}) AND status = 'active' AND file_path IS NOT NULL
                """,
                chunk,
            ).fetchall()
            for h, p in rows:
                if p:
                    results[h] = p
        return results

    # endregion

    # region Raw Inference Caching

    def save_raw_cache(
        self,
        file_hash: str,
        model_id: str,
        result_dict: dict[str, Any],
        *,
        enabled: bool = True,
    ) -> None:
        """
        Store raw un-culled tag predictions in the cache.
        If enabled is False, stores empty '{}' to record completion without retaining predictions.
        Prunes zero-confidence tags below min_cache_score to keep the DB size minimal.
        """
        if not enabled:
            json_payload = "{}"
        else:
            pruned_categories: dict[str, dict[str, float]] = {}
            raw_cats = result_dict.get("categories") or result_dict.get("tags") or {}

            if isinstance(raw_cats, dict):
                for cat_name, entries in raw_cats.items():
                    cat_dict: dict[str, float] = {}
                    if isinstance(entries, list):
                        for entry in entries:
                            if isinstance(entry, dict) and entry.get("score", 0.0) >= self._min_cache_score:
                                cat_dict[entry["tag"]] = round(float(entry["score"]), 4)
                    elif isinstance(entries, dict):
                        for tag, score in entries.items():
                            if float(score) >= self._min_cache_score:
                                cat_dict[tag] = round(float(score), 4)

                    if cat_dict:
                        pruned_categories[cat_name] = cat_dict

            json_payload = json.dumps(pruned_categories, separators=(",", ":"))

        self.conn.execute(
            """
            INSERT INTO inference_cache (file_hash, model_id, raw_json, inferred_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(file_hash, model_id) DO UPDATE SET
                raw_json    = excluded.raw_json,
                inferred_at = excluded.inferred_at
            """,
            (file_hash, model_id, json_payload, _now_iso()),
        )
        self.batch_tick()

    def get_raw_cache(self, file_hash: str, model_id: str) -> dict[str, dict[str, float]] | None:
        """Retrieve the pruned raw tag predictions for a file/model pair."""
        row = self.conn.execute(
            "SELECT raw_json FROM inference_cache WHERE file_hash = ? AND model_id = ?",
            (file_hash, model_id),
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def has_raw_cache(self, file_hash: str, model_id: str) -> bool:
        """Return True if an inference cache entry exists (even if empty {})."""
        row = self.conn.execute(
            "SELECT 1 FROM inference_cache WHERE file_hash = ? AND model_id = ?",
            (file_hash, model_id),
        ).fetchone()
        return row is not None

    def bulk_already_inferred(self, file_hashes: Sequence[str], model_id: str) -> set[str]:
        """Return the subset of file_hashes that already have cached inference for this model."""
        if not file_hashes:
            return set()

        done: set[str] = set()
        for chunk in self._chunk_list(file_hashes, 900):
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"""
                SELECT file_hash FROM inference_cache
                WHERE model_id = ? AND file_hash IN ({placeholders})
                """,
                [model_id, *chunk],
            ).fetchall()
            done.update(r[0] for r in rows)
        return done

    def clear_raw_cache(self) -> int:
        """Delete all rows from inference_cache and run VACUUM to reclaim disk space."""
        count_row = self.conn.execute("SELECT COUNT(*) FROM inference_cache").fetchone()
        count = count_row[0] if count_row else 0
        self.conn.execute("DELETE FROM inference_cache")
        self.commit()

        # NOTE: Currently supporting python 3.11+, if was 3.12+ we could use autocommit here

        # Temporarily switch to autocommit mode to allow VACUUM to run
        old_isolation = self.conn.isolation_level
        try:
            self.conn.isolation_level = None
            self.conn.execute("VACUUM")
        finally:
            self.conn.isolation_level = old_isolation

        return count

    # endregion

    # region Push Queue (The Hydrus To-Do List)

    def enqueue_push(
        self,
        file_hash: str,
        service_key: str,
        tags: Sequence[str],
        action: str = "add_tags",
    ) -> None:
        """
        Enqueue tags to be pushed to or removed from Hydrus.
        If a task already exists for this (file, service, action), tags are merged without loss.
        """
        if not tags:
            return

        # Check for an existing task to avoid overwriting tags from earlier models
        existing = self.conn.execute(
            "SELECT tags_json FROM push_queue WHERE file_hash = ? AND service_key = ? AND action = ?",
            (file_hash, service_key, action),
        ).fetchone()

        if existing:
            try:
                existing_tags = json.loads(existing[0])
            except Exception:
                existing_tags = []
            merged = list(dict.fromkeys(existing_tags + list(tags)))
            self.conn.execute(
                """
                UPDATE push_queue
                SET tags_json = ?, created_at = ?
                WHERE file_hash = ? AND service_key = ? AND action = ?
                """,
                (json.dumps(merged), _now_iso(), file_hash, service_key, action),
            )
        else:
            self.conn.execute(
                """
                INSERT INTO push_queue (file_hash, service_key, action, tags_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (file_hash, service_key, action, json.dumps(list(tags)), _now_iso()),
            )
        self.batch_tick()

    def has_pending_pushes(self) -> bool:
        """Check if any actionable items remain in the push queue."""
        row = self.conn.execute("SELECT 1 FROM push_queue WHERE attempts < 3 LIMIT 1").fetchone()
        return row is not None

    def get_pending_push_count(self) -> int:
        """Return the total count of actionable operations in the push queue."""
        row = self.conn.execute("SELECT COUNT(*) FROM push_queue WHERE attempts < 3").fetchone()
        return row[0] if row else 0

    def fetch_push_batch(self, limit: int = 50, max_attempts: int = 3) -> list[tuple[str, str, str, list[str]]]:
        """
        Fetch a batch of pending push tasks that have not exceeded max_attempts.
        Returns: list of (file_hash, service_key, action, list_of_tags)
        """
        rows = self.conn.execute(
            """
            SELECT file_hash, service_key, action, tags_json
            FROM push_queue
            WHERE attempts < ?
            LIMIT ?
            """,
            (max_attempts, limit),
        ).fetchall()

        results: list[tuple[str, str, str, list[str]]] = []
        for file_hash, service_key, action, tags_json in rows:
            try:
                tags = json.loads(tags_json)
            except Exception:
                tags = []
            results.append((file_hash, service_key, action, tags))
        return results

    def record_push_attempt_error(self, file_hash: str, service_key: str, action: str, error_msg: str) -> None:
        """Increment attempt counter and record error message for a failed push task."""
        self.conn.execute(
            """
            UPDATE push_queue
            SET attempts = attempts + 1, last_error = ?
            WHERE file_hash = ? AND service_key = ? AND action = ?
            """,
            (str(error_msg)[:500], file_hash, service_key, action),
        )
        self.commit()

    def remove_from_push_queue(
        self,
        tasks: Sequence[tuple[str, str, str]],
    ) -> None:
        """
        Remove successfully pushed tasks from the queue.
        tasks: Sequence of (file_hash, service_key, action)
        """
        if not tasks:
            return

        with self.transaction():
            self.conn.executemany(
                "DELETE FROM push_queue WHERE file_hash = ? AND service_key = ? AND action = ?",
                tasks,
            )

    def clear_file_from_push_queue(self, file_hash: str) -> None:
        """Remove all tasks for a specific file (e.g. if file was deleted in Hydrus)."""
        self.conn.execute("DELETE FROM push_queue WHERE file_hash = ?", (file_hash,))
        self.commit()

    # endregion

    # region Utilities

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Wrap operations in a clean atomic transaction."""
        try:
            yield
            self.commit()
        except Exception:
            if self._conn is not None:
                self._conn.rollback()
            raise

    @staticmethod
    def _chunk_list(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
        for i in range(0, len(items), size):
            yield items[i : i + size]

    # endregion
