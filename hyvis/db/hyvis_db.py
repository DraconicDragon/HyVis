"""
hyvis_db.py — High-scale, resilient SQLite operational state for HyVis (`hyvis.db`).

Features:
  - WAL mode with atomic batching (prevents disk thrashing on 100k+ files).
  - Permanent file tracking & completion ledger (`known_files`, `inference_log`) decoupled from disposable cache.db.
  - Active-run scratchpad (`active_run`, `run_targets`, `run_progress`) for crash-proof multi-model staging and `--resume`.
  - Dedicated push queue with tag merging and retry counters (resilient to Hydrus downtime and missing files).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self

logger = logging.getLogger(__name__)

MAX_PUSH_ATTEMPTS = 3


class FileStatus(StrEnum):
    ACTIVE = "active"
    MISSING_ON_DISK = "missing_on_disk"
    DELETED_FROM_HYDRUS = "deleted_from_hydrus"
    ERROR = "error"


class RunTargetState(StrEnum):
    PENDING = "pending"
    DONE = "done"
    REJECTED = "rejected"


class RunMode(StrEnum):
    DEFAULT = "default"
    INFER_ONLY = "infer_only"


TagActionType = Literal["add", "delete"]
PushAction = Literal["add_tags", "delete_tags"]


@dataclass(frozen=True)
class ActiveRunInfo:
    """Snapshot metadata for an active or unfinished inference run."""

    config_toml: str
    config_path: str | None
    mode: RunMode
    force_mode: bool
    hyvis_version: str
    started_at: str


@dataclass(frozen=True)
class ClientBinding:
    """Recorded Hydrus client fingerprint bound to this operational database."""

    api_url: str
    api_key_hash: str
    service_keys: frozenset[str]


def hash_api_key(api_key: str) -> str:
    """Return a short non-reversible fingerprint of the Hydrus API key."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ActiveRunInspection:
    """Read-only snapshot of an active/unfinished run and its locked target hashes."""

    run: ActiveRunInfo
    pending_hashes: list[str]
    done_count: int
    rejected_hashes: list[str]
    rejected_mimes: set[str]


@dataclass(frozen=True)
class ClientBindingInspection:
    """Read-only snapshot of the bound Hydrus client fingerprint and pending queue state."""

    binding: ClientBinding
    pending_push_count: int


# region Schema Definition

_SCHEMA_SQL = """
-- 1. Tracked Files: Stores physical paths, frame counts, and health status
CREATE TABLE IF NOT EXISTS known_files (
    file_hash       TEXT PRIMARY KEY,
    file_path       TEXT,
    mime            TEXT,
    num_frames      INTEGER,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'missing_on_disk', 'deleted_from_hydrus', 'error')),
    last_seen_at    TEXT NOT NULL
);

-- 2. Permanent Completion Ledger: Decoupled from disposable cache.db
CREATE TABLE IF NOT EXISTS inference_log (
    file_hash       TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    variant_id      TEXT NOT NULL DEFAULT '',
    vocab_hash      TEXT NOT NULL DEFAULT '',
    filter_hash     TEXT NOT NULL DEFAULT '',
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

-- 4. Active Run Metadata (0 rows when idle, max 1 row while in-flight / interrupted)
CREATE TABLE IF NOT EXISTS active_run (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    config_toml     TEXT NOT NULL,
    config_path     TEXT,
    mode            TEXT NOT NULL CHECK (mode IN ('default', 'infer_only')),
    force_mode      INTEGER NOT NULL DEFAULT 0 CHECK (force_mode IN (0, 1)),
    hyvis_version   TEXT NOT NULL,
    started_at      TEXT NOT NULL
);

-- 5. Active Run Targets: Locked preflight candidate & rejected file hashes (0 rows when idle)
CREATE TABLE IF NOT EXISTS run_targets (
    file_hash       TEXT PRIMARY KEY,
    state           TEXT NOT NULL CHECK (state IN ('pending', 'done', 'rejected')),
    mime            TEXT
);

-- 6. Active Run Staging: Intermediate multi-model tag payloads before atomic commit (0 rows when idle)
CREATE TABLE IF NOT EXISTS run_progress (
    file_hash            TEXT NOT NULL,
    model_id             TEXT NOT NULL,
    staged_payload_json  TEXT NOT NULL, -- JSON object: {"<service_key>": ["tag1", ...]}
    PRIMARY KEY (file_hash, model_id),
    FOREIGN KEY (file_hash) REFERENCES run_targets(file_hash) ON DELETE CASCADE
);

-- 7. Database Metadata: Key-value store for client binding and operational metadata
CREATE TABLE IF NOT EXISTS db_meta (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_inference_log_model
    ON inference_log(model_id, file_hash);
"""

# endregion


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    """Resilient SQLite database manager designed for high file counts."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._batch_uncommitted = 0

    # region Connection Lifecycle

    def open(self) -> None:
        if self._conn is not None:
            return

        is_new_db = str(self._path) == ":memory:" or not self._path.exists()

        if str(self._path) != ":memory:":
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

        # Apply sequential versioned migrations BEFORE executing _SCHEMA_SQL on existing DBs
        from hyvis.db.migrations import LATEST_VERSION, apply_migrations

        if is_new_db:
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.execute(f"PRAGMA user_version = {LATEST_VERSION}")
        else:
            apply_migrations(self._conn, db_path=self._path)
            self._conn.executescript(_SCHEMA_SQL)

        self._conn.commit()
        logger.debug("HyVis database opened at %s (WAL mode)", self._path)

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
        num_frames: int | None = None,
        status: FileStatus | str = FileStatus.ACTIVE,
    ) -> None:
        """Record or update known file metadata."""
        status_val = status.value if isinstance(status, FileStatus) else str(status)
        self.conn.execute(
            """
            INSERT INTO known_files (file_hash, file_path, mime, num_frames, status, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_hash) DO UPDATE SET
                file_path    = COALESCE(excluded.file_path, known_files.file_path),
                mime         = COALESCE(excluded.mime, known_files.mime),
                num_frames   = COALESCE(excluded.num_frames, known_files.num_frames),
                status       = excluded.status,
                last_seen_at = excluded.last_seen_at
            """,
            (file_hash, file_path, mime, num_frames, status_val, _now_iso()),
        )
        self.batch_tick()

    def mark_file_status(self, file_hash: str, status: FileStatus | str) -> None:
        """Update file status (e.g. 'missing_on_disk' or 'deleted_from_hydrus')."""
        status_val = status.value if isinstance(status, FileStatus) else str(status)
        self.conn.execute(
            "UPDATE known_files SET status = ? WHERE file_hash = ?",
            (status_val, file_hash),
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

    def get_all_known_hashes(self) -> list[str]:
        """Return all file hashes tracked in known_files (used for orphan cache purging)."""
        rows = self.conn.execute("SELECT file_hash FROM known_files").fetchall()
        return [r[0] for r in rows]

    # endregion

    # region Inference Completion Ledger

    def record_inference(
        self,
        file_hash: str,
        model_id: str,
        *,
        variant_id: str = "",
        vocab_hash: str = "",
        filter_hash: str = "",
    ) -> None:
        """Record completion of a (file_hash, model_id) pair in the permanent ledger."""
        self.conn.execute(
            """
            INSERT INTO inference_log (file_hash, model_id, variant_id, vocab_hash, filter_hash, inferred_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_hash, model_id) DO UPDATE SET
                variant_id  = excluded.variant_id,
                vocab_hash  = excluded.vocab_hash,
                filter_hash = excluded.filter_hash,
                inferred_at = excluded.inferred_at
            """,
            (file_hash, model_id, variant_id, vocab_hash, filter_hash, _now_iso()),
        )
        self.batch_tick()

    def get_inference_filter_hashes(
        self,
        file_hashes: Sequence[str],
        model_id: str,
    ) -> dict[str, str]:
        """
        Return {file_hash: filter_hash} for files already recorded for `model_id`.
        Files missing from the returned dict have never been processed by `model_id`.
        """
        if not file_hashes:
            return {}

        results: dict[str, str] = {}
        for chunk in self._chunk_list(file_hashes, 900):
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"""
                SELECT file_hash, filter_hash FROM inference_log
                WHERE model_id = ? AND file_hash IN ({placeholders})
                """,
                [model_id, *chunk],
            ).fetchall()
            for f_hash, f_filt in rows:
                results[f_hash] = f_filt
        return results

    # endregion

    # region Active Run Scratchpad & Staging

    def get_active_run(self) -> ActiveRunInfo | None:
        """Return the active/unfinished run metadata if one exists, else None."""
        row = self.conn.execute(
            "SELECT config_toml, config_path, mode, force_mode, hyvis_version, started_at FROM active_run WHERE id = 1"
        ).fetchone()
        if row is None:
            return None

        return ActiveRunInfo(
            config_toml=row[0],
            config_path=row[1],
            mode=RunMode(row[2]),
            force_mode=bool(row[3]),
            hyvis_version=row[4],
            started_at=row[5],
        )

    def start_active_run(
        self,
        config_toml: str,
        *,
        config_path: str | None = None,
        mode: RunMode | str = RunMode.DEFAULT,
        force_mode: bool = False,
        hyvis_version: str,
        candidate_files: Sequence[tuple[str, str | None]],
        rejected_files: Sequence[tuple[str, str | None]] = (),
    ) -> None:
        """
        Atomically initialize a new active run, replacing any discarded scratchpad state.
        `candidate_files` and `rejected_files` are sequences of (file_hash, mime).
        """
        mode_val = mode.value if isinstance(mode, RunMode) else str(mode)
        with self.transaction():
            self.conn.execute("DELETE FROM run_progress")
            self.conn.execute("DELETE FROM run_targets")
            self.conn.execute("DELETE FROM active_run")

            self.conn.execute(
                """
                INSERT INTO active_run (id, config_toml, config_path, mode, force_mode, hyvis_version, started_at)
                VALUES (1, ?, ?, ?, ?, ?, ?)
                """,
                (config_toml, config_path, mode_val, int(bool(force_mode)), hyvis_version, _now_iso()),
            )

            target_rows: list[tuple[str, str, str | None]] = [
                (h, RunTargetState.PENDING.value, mime) for h, mime in candidate_files
            ]
            target_rows.extend((h, RunTargetState.REJECTED.value, mime) for h, mime in rejected_files)

            if target_rows:
                self.conn.executemany(
                    "INSERT OR REPLACE INTO run_targets (file_hash, state, mime) VALUES (?, ?, ?)",
                    target_rows,
                )

    def get_run_targets(
        self,
        state: RunTargetState | str | None = None,
    ) -> list[tuple[str, str, str | None]]:
        """
        Retrieve target files for the active run.
        Returns: list of (file_hash, state, mime).
        """
        if state is not None:
            state_val = state.value if isinstance(state, RunTargetState) else str(state)
            rows = self.conn.execute(
                "SELECT file_hash, state, mime FROM run_targets WHERE state = ?",
                (state_val,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT file_hash, state, mime FROM run_targets").fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def stage_model_progress(
        self,
        file_hash: str,
        model_id: str,
        staged_payload: Mapping[str, Sequence[str]],
    ) -> None:
        """
        Stage one model's filtered tags (`{service_key: [tags...]}`) in `run_progress`
        while waiting for remaining models in a multi-model run.
        """
        clean_payload = {k: list(v) for k, v in staged_payload.items() if v}
        self.conn.execute(
            """
            INSERT INTO run_progress (file_hash, model_id, staged_payload_json)
            VALUES (?, ?, ?)
            ON CONFLICT(file_hash, model_id) DO UPDATE SET
                staged_payload_json = excluded.staged_payload_json
            """,
            (file_hash, model_id, json.dumps(clean_payload, separators=(",", ":"))),
        )
        self.batch_tick()

    def get_staged_hashes_for_model(self, model_id: str) -> set[str]:
        """Return file hashes already staged in `run_progress` for `model_id` (used on resume)."""
        rows = self.conn.execute(
            "SELECT file_hash FROM run_progress WHERE model_id = ?",
            (model_id,),
        ).fetchall()
        return {r[0] for r in rows}

    def get_staged_progress(self, file_hash: str) -> dict[str, dict[str, list[str]]]:
        """Return all staged model payloads for `file_hash`: `{model_id: {svc_key: [tags]}}`."""
        rows = self.conn.execute(
            "SELECT model_id, staged_payload_json FROM run_progress WHERE file_hash = ?",
            (file_hash,),
        ).fetchall()
        result: dict[str, dict[str, list[str]]] = {}
        for m_id, payload_json in rows:
            try:
                result[m_id] = json.loads(payload_json)
            except Exception:
                result[m_id] = {}
        return result

    def commit_file_completion(
        self,
        file_hash: str,
        *,
        completed_models: Sequence[tuple[str, str, str, str]],
        add_by_service: Mapping[str, Sequence[str]] | None = None,
        delete_by_service: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        """
        Atomically finalize a file once all remaining models in the run have finished it:
          1. Enqueue merged add/delete tag payloads into `push_queue` (if any).
          2. Record each completed `(model_id, variant_id, vocab_hash, filter_hash)` in `inference_log`.
          3. Mark `run_targets.state = 'done'` and delete staging rows from `run_progress`.
        """
        if add_by_service or delete_by_service:
            self._upsert_push_payload(
                file_hash,
                add_by_service=add_by_service or {},
                delete_by_service=delete_by_service or {},
            )

        now_str = _now_iso()
        for model_id, variant_id, vocab_hash, filter_hash in completed_models:
            self.conn.execute(
                """
                INSERT INTO inference_log (file_hash, model_id, variant_id, vocab_hash, filter_hash, inferred_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_hash, model_id) DO UPDATE SET
                    variant_id  = excluded.variant_id,
                    vocab_hash  = excluded.vocab_hash,
                    filter_hash = excluded.filter_hash,
                    inferred_at = excluded.inferred_at
                """,
                (file_hash, model_id, variant_id, vocab_hash, filter_hash, now_str),
            )

        self.conn.execute(
            "UPDATE run_targets SET state = ? WHERE file_hash = ?",
            (RunTargetState.DONE.value, file_hash),
        )
        self.conn.execute("DELETE FROM run_progress WHERE file_hash = ?", (file_hash,))
        self.batch_tick()

    def clear_active_run(self) -> None:
        """Wipe active-run scratchpad tables (`active_run`, `run_targets`, `run_progress`)."""
        with self.transaction():
            self.conn.execute("DELETE FROM run_progress")
            self.conn.execute("DELETE FROM run_targets")
            self.conn.execute("DELETE FROM active_run")

    # endregion

    # region Push Queue (The Hydrus To-Do List)

    def _upsert_push_payload(
        self,
        file_hash: str,
        *,
        add_by_service: Mapping[str, Sequence[str]],
        delete_by_service: Mapping[str, Sequence[str]],
    ) -> None:
        """Merge service-grouped add and delete tag lists into `push_queue` for one file."""
        has_any = any(add_by_service.values()) or any(delete_by_service.values())
        if not has_any:
            return

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

        for svc_key, tags in add_by_service.items():
            if not tags:
                continue
            svc_entry = payload.setdefault(svc_key, {})
            existing = svc_entry.get("add", [])
            svc_entry["add"] = list(dict.fromkeys(existing + [str(t) for t in tags]))

        for svc_key, tags in delete_by_service.items():
            if not tags:
                continue
            svc_entry = payload.setdefault(svc_key, {})
            existing = svc_entry.get("delete", [])
            svc_entry["delete"] = list(dict.fromkeys(existing + [str(t) for t in tags]))

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

        if action == "delete_tags":
            self._upsert_push_payload(file_hash, add_by_service={}, delete_by_service={service_key: tags})
        else:
            self._upsert_push_payload(file_hash, add_by_service={service_key: tags}, delete_by_service={})
        self.batch_tick()

    def has_pending_pushes(self) -> bool:
        """Check if any actionable files remain in the push queue."""
        row = self.conn.execute(
            "SELECT 1 FROM push_queue WHERE attempts < ? LIMIT 1",
            (MAX_PUSH_ATTEMPTS,),
        ).fetchone()
        return row is not None

    def get_pending_push_count(self) -> int:
        """Return the total count of actionable files in the push queue."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM push_queue WHERE attempts < ?",
            (MAX_PUSH_ATTEMPTS,),
        ).fetchone()
        return row[0] if row else 0

    def get_failed_push_count(self) -> int:
        """Return the count of dead-letter files that exceeded max push attempts."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM push_queue WHERE attempts >= ?",
            (MAX_PUSH_ATTEMPTS,),
        ).fetchone()
        return row[0] if row else 0

    def fetch_push_batch(
        self,
        limit: int = 50,
        max_attempts: int = MAX_PUSH_ATTEMPTS,
    ) -> list[tuple[str, dict[str, dict[TagActionType, list[str]]]]]:
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

        results: list[tuple[str, dict[str, dict[TagActionType, list[str]]]]] = []
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

    # region DB Meta & Client Binding

    def get_meta(self, key: str) -> str | None:
        """Retrieve a metadata value by key from db_meta."""
        row = self.conn.execute("SELECT value FROM db_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Persist a metadata key-value pair into db_meta."""
        self.conn.execute(
            """
            INSERT INTO db_meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self.commit()

    def get_client_binding(self) -> ClientBinding | None:
        """Return the recorded Hydrus client binding for this DB, or None if unbound."""
        api_url = self.get_meta("client_api_url")
        api_key_hash = self.get_meta("client_api_key_hash")
        services_raw = self.get_meta("client_service_keys")

        if api_url is None or api_key_hash is None or services_raw is None:
            return None

        try:
            services_list = json.loads(services_raw)
            service_keys = frozenset(str(k) for k in services_list)
        except Exception:
            return None

        return ClientBinding(
            api_url=api_url,
            api_key_hash=api_key_hash,
            service_keys=service_keys,
        )

    def set_client_binding(
        self,
        api_url: str,
        api_key_hash: str,
        service_keys: Iterable[str] | Sequence[str] | set[str] | frozenset[str],
    ) -> None:
        """Record or overwrite the Hydrus client binding fingerprint in db_meta."""
        norm_url = api_url.strip().rstrip("/")
        sorted_keys = sorted(str(k) for k in service_keys)
        with self.transaction():
            for k, v in (
                ("client_api_url", norm_url),
                ("client_api_key_hash", api_key_hash),
                ("client_service_keys", json.dumps(sorted_keys)),
            ):
                self.conn.execute(
                    """
                    INSERT INTO db_meta (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (k, v),
                )

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


def inspect_active_run_readonly(db_path: str | Path) -> ActiveRunInspection | None:
    """
    Safely inspect an existing hyvis.db in strict read-only URI mode without
    creating files on disk or running migrations. Returns None if no active run exists.
    """
    path = Path(db_path)
    if str(path) == ":memory:" or not path.is_file():
        return None

    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        row = conn.execute(
            "SELECT config_toml, config_path, mode, force_mode, hyvis_version, started_at FROM active_run WHERE id = 1"
        ).fetchone()
        if row is None:
            return None

        run_info = ActiveRunInfo(
            config_toml=row[0],
            config_path=row[1],
            mode=RunMode(row[2]),
            force_mode=bool(row[3]),
            hyvis_version=row[4],
            started_at=row[5],
        )

        target_rows = conn.execute("SELECT file_hash, state, mime FROM run_targets").fetchall()
        pending_hashes: list[str] = []
        done_count = 0
        rejected_hashes: list[str] = []
        rejected_mimes: set[str] = set()

        for f_hash, state, mime in target_rows:
            if state == RunTargetState.PENDING.value:
                pending_hashes.append(f_hash)
            elif state == RunTargetState.DONE.value:
                done_count += 1
            elif state == RunTargetState.REJECTED.value:
                rejected_hashes.append(f_hash)
                if mime:
                    rejected_mimes.add(mime)

        return ActiveRunInspection(
            run=run_info,
            pending_hashes=pending_hashes,
            done_count=done_count,
            rejected_hashes=rejected_hashes,
            rejected_mimes=rejected_mimes,
        )
    except (sqlite3.Error, ValueError):
        # Safely handles unmigrated v1 databases or transient read locks
        return None
    finally:
        if conn is not None:
            conn.close()


def inspect_client_binding_readonly(db_path: str | Path) -> ClientBindingInspection | None:
    """
    Safely inspect the recorded Hydrus client binding and pending push count in strict
    read-only URI mode without creating files or running migrations.
    """
    path = Path(db_path)
    if str(path) == ":memory:" or not path.is_file():
        return None

    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        rows = conn.execute(
            "SELECT key, value FROM db_meta WHERE key IN ('client_api_url', 'client_api_key_hash', 'client_service_keys')"
        ).fetchall()
        meta = dict(rows)
        if len(meta) < 3:
            return None

        services_list = json.loads(meta["client_service_keys"])
        binding = ClientBinding(
            api_url=meta["client_api_url"],
            api_key_hash=meta["client_api_key_hash"],
            service_keys=frozenset(str(k) for k in services_list),
        )

        q_row = conn.execute(
            "SELECT COUNT(*) FROM push_queue WHERE attempts < ?",
            (MAX_PUSH_ATTEMPTS,),
        ).fetchone()
        pending_pushes = int(q_row[0]) if q_row else 0

        return ClientBindingInspection(binding=binding, pending_push_count=pending_pushes)
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        return None
    finally:
        if conn is not None:
            conn.close()
