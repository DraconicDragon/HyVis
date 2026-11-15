"""
migration_base.py — Shared transactional SQLite schema migration runner using PRAGMA user_version.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

MigrationFn = Callable[[sqlite3.Connection], None]


def backup_database(conn: sqlite3.Connection, db_path: Path, current_version: int) -> None:
    """Safely create a consistent snapshot of the SQLite database before running migrations."""
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


def run_migrations(
    conn: sqlite3.Connection,
    migrations: Mapping[int, MigrationFn],
    *,
    latest_version: int,
    db_path: Path | str | None = None,
    label: str = "Database",
    create_backup: bool = True,
) -> int:
    """
    Check PRAGMA user_version and sequentially execute any pending migrations up to `latest_version`
    inside truly atomic DDL transactions.

    `migrations` maps target version -> migration function (e.g. `{2: _migrate_to_v2}`).
    Returns the previous schema version prior to migrating.
    """
    row = conn.execute("PRAGMA user_version").fetchone()
    current_version = int(row[0]) if row else 0

    if current_version >= latest_version:
        return current_version

    logger.debug("%s user_version=%d (target=%d)", label, current_version, latest_version)

    if create_backup and db_path:
        backup_database(conn, Path(db_path), current_version)

    for target_version in range(current_version + 1, latest_version + 1):
        migration_fn = migrations.get(target_version)
        if migration_fn is None:
            raise RuntimeError(f"{label} missing migration step for version {target_version - 1} -> {target_version}.")

        fn_name = getattr(migration_fn, "__name__", f"step_{target_version}")
        logger.info(
            "Applying %s migration %d -> %d (%s)...", label.lower(), target_version - 1, target_version, fn_name
        )

        # Force an explicit transaction so DDL (DROP TABLE, CREATE TABLE, ALTER TABLE) is 100% atomic
        old_isolation = conn.isolation_level
        try:
            conn.isolation_level = None  # Autocommit mode for manual transaction control
            conn.execute("BEGIN IMMEDIATE")
            migration_fn(conn)
            conn.execute(f"PRAGMA user_version = {target_version}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            logger.exception(
                "%s migration %d -> %d failed; rolled back all changes.",
                label,
                target_version - 1,
                target_version,
            )
            raise
        finally:
            conn.isolation_level = old_isolation

    logger.info("%s migrations complete. Current schema version: v%d", label, latest_version)
    return current_version
