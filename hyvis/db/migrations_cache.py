"""
migrations_cache.py — Sequential, transactional schema migrations for cache.db.

Keeps cached prediction data intact across schema upgrades instead of deleting
and re-creating cache.db on every version bump.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from hyvis.db.migration_base import MigrationFn, run_migrations

logger = logging.getLogger(__name__)


def _migrate_cache_to_v2(conn: sqlite3.Connection) -> None:
    """
    Migration v1 -> v2:
      - Adds 'prep_hash' column to 'predictions' so video/animation sampling changes
        invalidate only multi-frame cache entries without touching static image caches.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(predictions)").fetchall()}
    if cols and "prep_hash" not in cols:
        logger.info("Adding 'prep_hash' column to cache predictions table...")
        conn.execute("ALTER TABLE predictions ADD COLUMN prep_hash TEXT NOT NULL DEFAULT ''")


#: Current schema version for cache.db.
CACHE_LATEST_VERSION = 2

#: Maps target schema version -> migration function.
CACHE_MIGRATIONS: dict[int, MigrationFn] = {
    2: _migrate_cache_to_v2,
}


def apply_cache_migrations(conn: sqlite3.Connection, db_path: Path | str | None = None) -> None:
    """
    Upgrade an existing cache.db schema forward to CACHE_LATEST_VERSION.
    Skips file-copy backups because cache.db can be multi-GB and all DDL runs atomically.
    """
    run_migrations(
        conn,
        CACHE_MIGRATIONS,
        latest_version=CACHE_LATEST_VERSION,
        db_path=db_path,
        label="Cache database",
        create_backup=False,
    )
