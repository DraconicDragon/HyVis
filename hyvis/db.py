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
from typing import Any, Literal, Self

logger = logging.getLogger(__name__)

_DEFAULT_MIN_CACHE_SCORE = 0.01

FileStatus = Literal["active", "missing_on_disk", "deleted_from_hydrus", "error"]
TagActionType = Literal["add", "delete"]
PushAction = Literal["add_tags", "delete_tags"]


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

-- 3. Push Queue: Dedicated to-do list for Hydrus tag additions & cleanups (1 row per file)
CREATE TABLE IF NOT EXISTS push_queue (
    file_hash       TEXT PRIMARY KEY,
    payload_json    TEXT NOT NULL, -- JSON object: {"<service_key>": {"add": [...], "delete": [...]}}
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cache_lookup
    ON inference_cache(model_id, file_hash);
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

        is_new_db = not self._path.exists()

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            timeout=30.0,  # Avoid lock contention failures
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")  # Fast & completely safe in WAL mode
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA cache_size = -65536")  # 64MiB
        self._conn.executescript(_SCHEMA_SQL)

        # Apply sequential versioned migrations
        from hyvis.db_migrations import LATEST_VERSION, apply_migrations

        if is_new_db:
            # New installation: schema is already current, stamp version directly
            self._conn.execute(f"PRAGMA user_version = {LATEST_VERSION}")
        else:
            # Existing database: inspect user_version and upgrade if older
            apply_migrations(self._conn, db_path=self._path)

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
        status: FileStatus = "active",
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

    def mark_file_status(self, file_hash: str, status: FileStatus) -> None:
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
        category_scores: dict[str, dict[str, float]],
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
            # NOTE: as of writing, only usage of this function already pre-filters to min cache score, so this is just here as fallback, or if its called somewhere else i guess
            pruned_categories: dict[str, dict[str, float]] = {}
            for cat, tag_map in category_scores.items():
                filtered = {tag: round(score, 4) for tag, score in tag_map.items() if score >= self._min_cache_score}
                if filtered:
                    pruned_categories[cat] = filtered

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
        action: PushAction = "add_tags",
    ) -> None:
        """
        Enqueue tags to be pushed to or removed from Hydrus for a file.
        Payload format: {"<service_key>": {"add": [tags...], "delete": [tags...]}}
        """
        if not tags:
            return

        act_key: TagActionType = "delete" if action == "delete_tags" else "add"

        row = self.conn.execute(
            "SELECT payload_json FROM push_queue WHERE file_hash = ?",
            (file_hash,),
        ).fetchone()

        if row:
            try:
                payload = json.loads(row[0])
            except Exception:
                payload = {}
        else:
            payload = {}

        svc_entry = payload.setdefault(service_key, {})
        existing = svc_entry.get(act_key, [])
        merged = list(dict.fromkeys(existing + [str(t) for t in tags]))
        svc_entry[act_key] = merged

        self.conn.execute(
            """
            INSERT INTO push_queue (file_hash, payload_json, attempts, last_error, created_at)
            VALUES (?, ?, 0, NULL, ?)
            ON CONFLICT(file_hash) DO UPDATE SET
                payload_json = excluded.payload_json,
                attempts     = 0,
                last_error   = NULL,
                created_at   = excluded.created_at
            """,
            (file_hash, json.dumps(payload), _now_iso()),
        )
        self.batch_tick()

    def has_pending_pushes(self) -> bool:
        """Check if any actionable files remain in the push queue."""
        row = self.conn.execute("SELECT 1 FROM push_queue WHERE attempts < 3 LIMIT 1").fetchone()
        return row is not None

    def get_pending_push_count(self) -> int:
        """Return the total count of actionable files in the push queue."""
        row = self.conn.execute("SELECT COUNT(*) FROM push_queue WHERE attempts < 3").fetchone()
        return row[0] if row else 0

    def fetch_push_batch(
        self,
        limit: int = 50,
        max_attempts: int = 3,
    ) -> list[tuple[str, dict[str, dict[str, list[str]]]]]:
        """
        Fetch a batch of pending file payloads that have not exceeded max_attempts.
        Returns: list of (file_hash, payload_dict)
        """
        rows = self.conn.execute(
            """
            SELECT file_hash, payload_json
            FROM push_queue
            WHERE attempts < ?
            ORDER BY rowid
            LIMIT ?
            """,
            (max_attempts, limit),
        ).fetchall()

        results: list[tuple[str, dict[str, dict[str, list[str]]]]] = []
        for file_hash, payload_json in rows:
            try:
                payload = json.loads(payload_json)
            except Exception as exc:
                logger.error("Corrupt payload_json for %s: %s", file_hash[:8], exc)
                payload = {}
            results.append((file_hash, payload))
        return results

    def record_push_attempt_error(self, file_hash: str, error_msg: str) -> None:
        """Increment attempt counter and record error message for a failed push file."""
        self.conn.execute(
            """
            UPDATE push_queue
            SET attempts = attempts + 1, last_error = ?
            WHERE file_hash = ?
            """,
            (str(error_msg)[:500], file_hash),
        )
        self.commit()

    def remove_from_push_queue(
        self,
        file_hashes: Sequence[str],
    ) -> None:
        """
        Remove successfully pushed files from the queue.
        file_hashes: Sequence of file_hash strings.
        """
        if not file_hashes:
            return

        with self.transaction():
            self.conn.executemany(
                "DELETE FROM push_queue WHERE file_hash = ?",
                [(h,) for h in file_hashes],
            )

    def clear_file_from_push_queue(self, file_hash: str) -> None:
        """Remove a specific file from the queue (e.g. if deleted in Hydrus)."""
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
