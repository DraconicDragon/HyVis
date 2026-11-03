"""
test_db.py — High-value persistence and schema migration tests.
Executes in under 0.1s in memory without requiring a GPU or Hydrus.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from hyvis.db import Database
from hyvis.db_migrations import LATEST_VERSION, apply_migrations

# region Queue Consolidation Tests


def test_enqueue_push_consolidation_single_file() -> None:
    """Verify that multiple enqueue_push calls for the same file merge into one payload row."""
    with Database(":memory:") as db:
        file_hash = "a" * 64
        # Satisfy foreign key constraint
        db.upsert_file(file_hash, file_path="/fake/path.png", mime="image/png")

        # 1. Enqueue model tags on Service A (add)
        db.enqueue_push(file_hash, "svc_a", ["1girl", "blue_hair"], action="add_tags")

        # 2. Enqueue post-run extra tag on Service B (add)
        db.enqueue_push(file_hash, "svc_b", ["ai:tagged"], action="add_tags")

        # 3. Enqueue cleanup tag on Service A (delete)
        db.enqueue_push(file_hash, "svc_a", ["temp:queue"], action="delete_tags")

        # Must report exactly 1 pending file in the queue
        assert db.get_pending_push_count() == 1
        assert db.has_pending_pushes() is True

        batch = db.fetch_push_batch(limit=10)
        assert len(batch) == 1

        returned_hash, payload = batch[0]
        assert returned_hash == file_hash

        # Verify payload structure matches {"<svc>": {"add": [...], "delete": [...]}}
        assert payload["svc_a"]["add"] == ["1girl", "blue_hair"]
        assert payload["svc_a"]["delete"] == ["temp:queue"]
        assert payload["svc_b"]["add"] == ["ai:tagged"]

        # 4. Remove from queue
        db.remove_from_push_queue([file_hash])
        assert db.get_pending_push_count() == 0
        assert db.has_pending_pushes() is False


# endregion


# region Schema Migration Tests


def test_migration_v0_to_v1_folds_unpushed_queue(tmp_path: Path) -> None:
    """
    Construct a real v0 database (old multi-row schema with service_key column),
    run apply_migrations, and assert that it folds into the new one-row schema without data loss.
    """
    db_file = tmp_path / "legacy.db"

    # 1. Manually setup the old v0 schema
    with sqlite3.connect(db_file) as conn:
        conn.executescript(
            """
            CREATE TABLE known_files (
                file_hash    TEXT PRIMARY KEY,
                file_path    TEXT,
                mime         TEXT,
                status       TEXT NOT NULL DEFAULT 'active',
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE push_queue (
                file_hash    TEXT NOT NULL,
                service_key  TEXT NOT NULL,
                action       TEXT NOT NULL,
                tags_json    TEXT NOT NULL,
                attempts     INTEGER NOT NULL DEFAULT 0,
                last_error   TEXT,
                created_at   TEXT NOT NULL,
                PRIMARY KEY (file_hash, service_key, action)
            );
            PRAGMA user_version = 0;
            """
        )

        # Insert 2 old rows for File 1 (simulating Bulder's unpushed tags)
        conn.execute(
            """
            INSERT INTO push_queue VALUES
            ('hash1', 'svc_model', 'add_tags', '["solo", "smile"]', 0, NULL, '2026-01-01'),
            ('hash1', 'svc_local', 'add_tags', '["ai:tagged"]', 1, 'Timeout', '2026-01-01')
            """
        )
        # Insert 1 old row for File 2
        conn.execute(
            """
            INSERT INTO push_queue VALUES
            ('hash2', 'svc_model', 'delete_tags', '["temp:tag"]', 0, NULL, '2026-01-01')
            """
        )

    # 2. Run migrations
    with sqlite3.connect(db_file) as conn:
        apply_migrations(conn, db_path=db_file)

    # 3. Verify upgraded schema and folded contents
    with sqlite3.connect(db_file) as conn:
        # Version must now be 1
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == 1

        # Columns must be the new one-row format (no service_key column)
        cursor = conn.execute("PRAGMA table_info(push_queue)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "service_key" not in cols
        assert "payload_json" in cols

        # Exactly 2 rows (one for hash1, one for hash2)
        rows = conn.execute(
            "SELECT file_hash, payload_json, attempts, last_error FROM push_queue ORDER BY file_hash"
        ).fetchall()
        assert len(rows) == 2

        # Check hash1
        h1, p1_json, attempts1, last_err1 = rows[0]
        assert h1 == "hash1"
        assert attempts1 == 1  # Preserved highest attempt count
        assert last_err1 == "Timeout"
        p1 = json.loads(p1_json)
        assert p1["svc_model"]["add"] == ["solo", "smile"]
        assert p1["svc_local"]["add"] == ["ai:tagged"]

        # Check hash2
        h2, p2_json, _, _ = rows[1]
        assert h2 == "hash2"
        p2 = json.loads(p2_json)
        assert p2["svc_model"]["delete"] == ["temp:tag"]


def test_new_database_stamps_latest_version_without_backup(tmp_path: Path) -> None:
    """Verify that a brand-new database gets stamped with LATEST_VERSION directly and creates no backup files."""
    db_file = tmp_path / "fresh.db"
    assert not db_file.exists()

    with Database(db_file) as db:
        ver = db.conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == LATEST_VERSION

    # No phantom .backup_v0 files should exist
    backup_files = list(tmp_path.glob("*.backup*"))
    assert len(backup_files) == 0


# endregion
