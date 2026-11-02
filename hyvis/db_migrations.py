"""
migrations.py — Sequential, transactional schema migrations using PRAGMA user_version.
Keeps db.py clean and ensures existing databases seamlessly upgrade without data loss.
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


# region Migration Runner

MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _migrate_to_v1,
]


def apply_migrations(conn: sqlite3.Connection, db_path: Path | str | None = None) -> None:
    """
    Check PRAGMA user_version and sequentially execute any pending migrations
    inside truly atomic DDL transactions.
    """
    row = conn.execute("PRAGMA user_version").fetchone()
    current_version: int = row[0] if row else 0
    total_migrations = len(MIGRATIONS)

    if current_version >= total_migrations:
        return

    logger.debug("Database user_version=%d (target=%d)", current_version, total_migrations)

    # Only create a backup if the database already contains existing data (skips brand new databases)
    has_data = False
    try:
        has_data = conn.execute("SELECT 1 FROM known_files LIMIT 1").fetchone() is not None
    except Exception:
        pass

    if db_path and has_data:
        _backup_database(conn, Path(db_path), current_version)

    for idx in range(current_version, total_migrations):
        target_version = idx + 1
        migration_fn = MIGRATIONS[idx]
        fn_name = getattr(migration_fn, "__name__", f"step_{target_version}")
        logger.info("Applying database migration %d -> %d (%s)...", idx, target_version, fn_name)

        # Force an explicit transaction so DDL (DROP TABLE, CREATE TABLE) is 100% atomic
        old_isolation = conn.isolation_level
        try:
            conn.isolation_level = None  # Autocommit mode to allow manual transaction control
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

    logger.info("Database migrations complete. Current schema version: v%d", total_migrations)


# endregion
