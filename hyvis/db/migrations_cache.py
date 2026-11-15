"""
migrations_cache.py — Sequential, transactional schema migrations for cache.db.

Keeps cached GPU prediction data intact across schema upgrades instead of deleting
and re-creating cache.db on every version bump.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from hyvis.db.migration_base import MigrationFn, run_migrations

#: Current schema version for cache.db (v1 = initial sparse binary cache schema).
CACHE_LATEST_VERSION = 1

#: Maps target schema version -> migration function (e.g. `{2: _migrate_to_v2}`).
CACHE_MIGRATIONS: dict[int, MigrationFn] = {}


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
