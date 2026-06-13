"""
db.py Stores more than currently needed, maybe future updates can make use of it
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# region Schema
_SCHEMA_SQL = """
-- One record per execution of main.py
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    config_toml     TEXT NOT NULL,      -- raw config at run time
    started_at      TEXT NOT NULL
);

-- Known file metadata (populated during inference).
CREATE TABLE IF NOT EXISTS known_files (
    file_hash       TEXT PRIMARY KEY,
    mime            TEXT,
    file_path       TEXT
);

-- Raw inference results for potential re-evaluation with changed thresholds.
CREATE TABLE IF NOT EXISTS inference_cache (
    file_hash       TEXT    NOT NULL REFERENCES known_files(file_hash),
    model_id        TEXT    NOT NULL,
    run_id          TEXT    NOT NULL REFERENCES runs(run_id),
    inference_json  TEXT    NOT NULL,   -- TagResult.to_dict() JSON
    PRIMARY KEY (file_hash, model_id)
);

-- Outcome for each (file, model) pair in a run.
-- infer_success tracks whether inference ran and results were saved to cache.
-- push_success tracks whether tags were successfully sent to Hydrus.
-- These are intentionally separate: inference can succeed while push fails
-- (e.g. Hydrus was closed), and push can be retried later without re-running
-- inference.
CREATE TABLE IF NOT EXISTS file_model_results (
    file_hash       TEXT    NOT NULL REFERENCES known_files(file_hash),
    model_id        TEXT    NOT NULL,
    run_id          TEXT    NOT NULL REFERENCES runs(run_id),
    infer_success   INTEGER NOT NULL DEFAULT 0,  -- 1 = inference ok and cached
    push_success    INTEGER NOT NULL DEFAULT 0,  -- 1 = Hydrus push ok
    infer_error     TEXT,
    push_error      TEXT,
    duration_ms     INTEGER,
    processed_at    TEXT    NOT NULL,
    PRIMARY KEY (file_hash, model_id)
);

-- Index for the "has this (hash, model) been successfully inferred?" query.
CREATE INDEX IF NOT EXISTS idx_fmr_hash_model_infer
    ON file_model_results (file_hash, model_id, infer_success);

-- Index for finding files that need their push retried.
CREATE INDEX IF NOT EXISTS idx_fmr_push_pending
    ON file_model_results (model_id, infer_success, push_success);
"""


# region Database


class Database:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None

    # region Lifecycle
    def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()
        logger.debug("Database opened at %s", self._path)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Database":
        self.open()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not open; call open() or use as context manager")
        return self._conn

    # region Runs
    def start_run(
        self,
        run_id: str,
        config_toml: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO runs (run_id, config_toml, started_at)
            VALUES (?, ?, ?)
            """,
            (run_id, config_toml, _now_iso()),
        )
        self.conn.commit()

    # region Cache / deduplication

    def is_already_inferred(self, file_hash: str, model_id: str) -> bool:
        """True if inference has already run successfully for this (file, model)."""
        row = self.conn.execute(
            """
            SELECT 1 FROM file_model_results
             WHERE file_hash = ? AND model_id = ? AND infer_success = 1
             LIMIT 1
            """,
            (file_hash, model_id),
        ).fetchone()
        return row is not None

    def bulk_already_inferred(self, file_hashes: list[str], model_id: str) -> set[str]:
        """Return the subset of hashes that were already inferred successfully."""
        if not file_hashes:
            return set()

        # SQLite has a limit on bind parameters; chunk if needed.
        done: set[str] = set()
        chunk_size = 900
        for start in range(0, len(file_hashes), chunk_size):
            chunk = file_hashes[start : start + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"""
                SELECT DISTINCT file_hash FROM file_model_results
                 WHERE model_id = ? AND infer_success = 1
                   AND file_hash IN ({placeholders})
                """,
                [model_id, *chunk],
            ).fetchall()
            done.update(r[0] for r in rows)
        return done

    def bulk_push_pending(self, model_id: str) -> list[str]:
        """
        Return hashes that were inferred successfully but never pushed to Hydrus.

        These are candidates for a --push-only retry run.
        """
        rows = self.conn.execute(
            """
            SELECT file_hash FROM file_model_results
             WHERE model_id = ? AND infer_success = 1 AND push_success = 0
            """,
            (model_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def bulk_fully_completed(self, file_hashes: list[str], model_ids: list[str]) -> set[str]:
        """Return the subset of file_hashes that have successful inference and push across all specified models."""
        if not file_hashes or not model_ids:
            return set()

        completed: set[str] = set()
        chunk_size = 900
        unique_models = list(set(model_ids))
        for start in range(0, len(file_hashes), chunk_size):
            chunk = file_hashes[start : start + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            model_placeholders = ",".join("?" * len(unique_models))

            rows = self.conn.execute(
                f"""
                SELECT file_hash, COUNT(*) FROM file_model_results
                 WHERE model_id IN ({model_placeholders})
                   AND infer_success = 1 AND push_success = 1
                   AND file_hash IN ({placeholders})
                 GROUP BY file_hash
                """,
                [*unique_models, *chunk],
            ).fetchall()

            for file_hash, count in rows:
                if count == len(unique_models):
                    completed.add(file_hash)
        return completed

    def get_cached_inference(self, file_hash: str, model_id: str) -> dict[str, Any] | None:
        """Return the cached TagResult dict, or None if not cached."""
        row = self.conn.execute(
            "SELECT inference_json FROM inference_cache WHERE file_hash = ? AND model_id = ?",
            (file_hash, model_id),
        ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            logger.warning("Corrupted cache entry for %s / %s", file_hash, model_id)
            return None

    # region Write helpers
    def upsert_known_file(self, file_hash: str, *, mime: str | None, file_path: str | None) -> None:
        self.conn.execute(
            """
            INSERT INTO known_files (file_hash, mime, file_path)
            VALUES (?, ?, ?)
            ON CONFLICT(file_hash) DO UPDATE SET
                mime         = excluded.mime,
                file_path    = excluded.file_path
            """,
            (file_hash, mime, file_path),
        )

    def save_inference_cache(
        self,
        file_hash: str,
        model_id: str,
        run_id: str,
        inference_dict: dict[str, Any],
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO inference_cache (file_hash, model_id, run_id, inference_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(file_hash, model_id) DO UPDATE SET
                run_id         = excluded.run_id,
                inference_json = excluded.inference_json
            """,
            (
                file_hash,
                model_id,
                run_id,
                json.dumps(inference_dict),
            ),
        )

    def record_infer_result(
        self,
        *,
        run_id: str,
        file_hash: str,
        model_id: str,
        success: bool,
        duration_ms: int,
        error_message: str | None = None,
    ) -> None:
        """Record the outcome of an inference attempt for one file."""
        self.conn.execute(
            """
            INSERT INTO file_model_results
                (run_id, file_hash, model_id, infer_success,
                 infer_error, duration_ms, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_hash, model_id) DO UPDATE SET
                run_id        = excluded.run_id,
                infer_success = excluded.infer_success,
                infer_error   = excluded.infer_error,
                duration_ms   = excluded.duration_ms,
                processed_at  = excluded.processed_at
            """,
            (
                run_id,
                file_hash,
                model_id,
                1 if success else 0,
                error_message,
                duration_ms,
                _now_iso(),
            ),
        )

    def record_push_result(
        self,
        *,
        file_hash: str,
        model_id: str,
        success: bool,
        error_message: str | None = None,
    ) -> None:
        """Record the outcome of a Hydrus push attempt for one file.

        The row must already exist (created by record_infer_result).
        """
        self.conn.execute(
            """
            UPDATE file_model_results
               SET push_success = ?,
                   push_error   = ?
             WHERE file_hash = ? AND model_id = ?
            """,
            (1 if success else 0, error_message, file_hash, model_id),
        )

    def commit(self) -> None:
        self.conn.commit()
