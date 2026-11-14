"""
migrations.py — Sequential, transactional schema migrations using PRAGMA user_version.
Keeps hyvis_db.py clean and ensures existing databases seamlessly upgrade without data loss.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# region Backup Helper


def _backup_database(conn: sqlite3.Connection, db_path: Path, current_version: int) -> None:
    """Safely creates a consistent snapshot of the SQLite database before running migrations."""
    if str(db_path) in (":memory:", "") or not db_path.exists():
        return

    backup_path = db_path.with_name(f"{db_path.name}.backup_v{current_version}")
    logger.info("Creating pre-migration database backup at %s...", backup_path)
    try:
        # contextlib.closing guarantees the backup connection closes immediately on Windows
        with contextlib.closing(sqlite3.connect(backup_path)) as backup_conn:
            conn.backup(backup_conn)
        logger.info("Database backup created successfully.")
    except Exception as exc:
        logger.warning("Could not create database backup: %s (proceeding with migration)", exc)


# endregion


# region To v1


def _migrate_to_v1(conn: sqlite3.Connection) -> None:
    """
    Migration v0 -> v1:
      - Ensures 'known_files' has 'status' column.
      - Replaces multi-row 'push_queue' with one-row-per-file 'payload_json' schema.
      - Preserves and folds any existing pending queue rows into the new format.
    """
    # 1. Ensure known_files has 'status'
    cursor = conn.execute("PRAGMA table_info(known_files)")
    known_cols = {row[1] for row in cursor.fetchall()}
    if known_cols and "status" not in known_cols:
        logger.info("Adding missing 'status' column to known_files table...")
        conn.execute("ALTER TABLE known_files ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")

    # 2. Check push_queue schema
    cursor = conn.execute("PRAGMA table_info(push_queue)")
    queue_cols = {row[1] for row in cursor.fetchall()}

    if "service_key" in queue_cols:
        logger.info("Upgrading push_queue from multi-row to consolidated one-row-per-file schema...")
        rows = conn.execute(
            """
            SELECT file_hash, service_key, action, tags_json, attempts, last_error, created_at
            FROM push_queue
            """
        ).fetchall()

        folded: dict[str, dict[str, Any]] = {}
        for f_hash, s_key, action, tags_json, attempts, last_error, created_at in rows:
            act_key = "delete" if "delete" in str(action).lower() else "add"
            try:
                tags = json.loads(tags_json)
            except Exception:
                tags = []

            if f_hash not in folded:
                folded[f_hash] = {
                    "payload": {},
                    "attempts": attempts,
                    "last_error": last_error,
                    "created_at": created_at,
                }
            else:
                folded[f_hash]["attempts"] = max(folded[f_hash]["attempts"], attempts)
                if last_error:
                    folded[f_hash]["last_error"] = last_error

            svc_entry = folded[f_hash]["payload"].setdefault(s_key, {})
            existing = svc_entry.get(act_key, [])
            svc_entry[act_key] = list(dict.fromkeys(existing + tags))

        conn.execute("DROP TABLE push_queue")
        conn.execute(
            """
            CREATE TABLE push_queue (
                file_hash       TEXT PRIMARY KEY,
                payload_json    TEXT NOT NULL,
                attempts        INTEGER NOT NULL DEFAULT 0,
                last_error      TEXT,
                created_at      TEXT NOT NULL
            )
            """
        )

        for f_hash, data in folded.items():
            conn.execute(
                """
                INSERT INTO push_queue (file_hash, payload_json, attempts, last_error, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    f_hash,
                    json.dumps(data["payload"]),
                    data["attempts"],
                    data["last_error"],
                    data["created_at"],
                ),
            )
        logger.info("Successfully folded and migrated %d pending file(s) in push_queue.", len(folded))
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS push_queue (
                file_hash       TEXT PRIMARY KEY,
                payload_json    TEXT NOT NULL,
                attempts        INTEGER NOT NULL DEFAULT 0,
                last_error      TEXT,
                created_at      TEXT NOT NULL
            )
            """
        )


# endregion


# region To v2


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    """
    Migration v1 -> v2:
      - Adds nullable 'num_frames' column to 'known_files' for multi-frame / animation tracking.
      - Creates 'inference_log' and drops legacy JSON 'inference_cache' table (now moved to cache.db).
      - Creates active-run scratchpad tables: 'active_run', 'run_targets', 'run_progress'.
    """
    # 1. Ensure known_files has 'num_frames'
    cursor = conn.execute("PRAGMA table_info(known_files)")
    known_cols = {row[1] for row in cursor.fetchall()}
    if known_cols and "num_frames" not in known_cols:
        logger.info("Adding 'num_frames' column to known_files table...")
        conn.execute("ALTER TABLE known_files ADD COLUMN num_frames INTEGER")

    # 2. Create inference_log and drop legacy JSON inference_cache
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inference_log (
            file_hash       TEXT NOT NULL,
            model_id        TEXT NOT NULL,
            variant_id      TEXT NOT NULL DEFAULT '',
            vocab_hash      TEXT NOT NULL DEFAULT '',
            filter_hash     TEXT NOT NULL DEFAULT '',
            inferred_at     TEXT NOT NULL,
            PRIMARY KEY (file_hash, model_id),
            FOREIGN KEY (file_hash) REFERENCES known_files(file_hash) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_inference_log_model
            ON inference_log(model_id, file_hash)
        """
    )
    conn.execute("DROP INDEX IF EXISTS idx_cache_lookup")
    conn.execute("DROP TABLE IF EXISTS inference_cache")

    # 3. Create active-run scratchpad tables
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS active_run (
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            config_toml     TEXT NOT NULL,
            config_path     TEXT,
            mode            TEXT NOT NULL CHECK (mode IN ('default', 'infer_only')),
            force_mode      INTEGER NOT NULL DEFAULT 0 CHECK (force_mode IN (0, 1)),
            hyvis_version   TEXT NOT NULL,
            started_at      TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_targets (
            file_hash       TEXT PRIMARY KEY,
            state           TEXT NOT NULL CHECK (state IN ('pending', 'done', 'rejected')),
            mime            TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_progress (
            file_hash            TEXT NOT NULL,
            model_id             TEXT NOT NULL,
            staged_payload_json  TEXT NOT NULL,
            PRIMARY KEY (file_hash, model_id),
            FOREIGN KEY (file_hash) REFERENCES run_targets(file_hash) ON DELETE CASCADE
        )
        """
    )


# endregion


# region Migration Runner

MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _migrate_to_v1,
    _migrate_to_v2,
]

LATEST_VERSION: int = len(MIGRATIONS)


def apply_migrations(conn: sqlite3.Connection, db_path: Path | str | None = None) -> None:
    """
    Check PRAGMA user_version and sequentially execute any pending migrations
    inside truly atomic DDL transactions.
    """
    row = conn.execute("PRAGMA user_version").fetchone()
    current_version: int = row[0] if row else 0

    if current_version >= LATEST_VERSION:
        return

    logger.debug("Database user_version=%d (target=%d)", current_version, LATEST_VERSION)

    # Pre-migration safety backup on existing databases
    if db_path:
        _backup_database(conn, Path(db_path), current_version)

    dropped_legacy_cache = current_version < 2 <= LATEST_VERSION

    for idx in range(current_version, LATEST_VERSION):
        target_version = idx + 1
        migration_fn = MIGRATIONS[idx]
        fn_name = getattr(migration_fn, "__name__", f"step_{target_version}")
        logger.info("Applying database migration %d -> %d (%s)...", idx, target_version, fn_name)

        # Force an explicit transaction so DDL (DROP TABLE, CREATE TABLE) is 100% atomic
        old_isolation = conn.isolation_level
        try:
            conn.isolation_level = None  # Autocommit mode for manual transaction control
            conn.execute("BEGIN IMMEDIATE")
            migration_fn(conn)
            conn.execute(f"PRAGMA user_version = {target_version}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            logger.exception("Migration %d -> %d failed; rolled back all changes.", idx, target_version)
            raise
        finally:
            conn.isolation_level = old_isolation

    # Reclaim space outside transaction if legacy JSON cache was dropped
    if dropped_legacy_cache and str(db_path or "") not in (":memory:", ""):
        old_isolation = conn.isolation_level
        try:
            conn.isolation_level = None
            logger.info("Reclaiming disk space after legacy cache removal (VACUUM)...")
            conn.execute("VACUUM")
        except Exception as exc:
            logger.warning("Post-migration VACUUM skipped: %s", exc)
        finally:
            conn.isolation_level = old_isolation

    logger.info("Database migrations complete. Current schema version: v%d", LATEST_VERSION)


# endregion
