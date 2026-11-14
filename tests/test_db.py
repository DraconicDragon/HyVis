"""
test_db.py — High-value persistence and schema migration tests.
Executes in under 0.1s in memory without requiring a GPU or Hydrus.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from hyvis.db import Database, RunMode, RunTargetState
from hyvis.db.migrations import LATEST_VERSION, apply_migrations

# region Queue Consolidation Tests


def test_enqueue_push_consolidation_single_file() -> None:
    """Verify that multiple enqueue_push calls for the same file merge into one payload row."""
    with Database(":memory:") as db:
        file_hash = "a" * 64
        # Satisfy foreign key constraint
        db.upsert_file(file_hash, file_path="/fake/path.png", mime="image/png", num_frames=1)

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


def test_active_run_staging_and_atomic_completion() -> None:
    """Verify multi-model staging in run_progress commits atomically and clears scratchpad."""
    with Database(":memory:") as db:
        h1 = "1" * 64
        h2 = "2" * 64
        rej = "3" * 64
        db.upsert_file(h1, file_path="/a.png", mime="image/png")

        db.start_active_run(
            "[hydrus]\napi_url='http://127.0.0.1:45869'",
            config_path="config.toml",
            mode=RunMode.DEFAULT,
            force_mode=False,
            hyvis_version="v0.1.0",
            candidate_files=[(h1, "image/png"), (h2, "image/jpeg")],
            rejected_files=[(rej, "video/mp4")],
        )

        active = db.get_active_run()
        assert active is not None
        assert active.mode == RunMode.DEFAULT
        assert len(db.get_run_targets(RunTargetState.PENDING)) == 2
        assert db.get_run_targets(RunTargetState.REJECTED) == [(rej, "rejected", "video/mp4")]

        # Model A stages tags for h1 (NOT in push_queue or inference_log yet!)
        db.stage_model_progress(h1, "model_a", {"svc_1": ["1girl", "solo"]})
        assert db.get_pending_push_count() == 0
        assert db.get_inference_filter_hashes([h1], "model_a") == {}

        # Model B finishes h1 and commits everything atomically
        staged = db.get_staged_progress(h1)
        assert staged["model_a"]["svc_1"] == ["1girl", "solo"]

        db.commit_file_completion(
            h1,
            completed_models=[
                ("model_a", "", "vh_a", "fh_a"),
                ("model_b", "bf16", "vh_b", "fh_b"),
            ],
            add_by_service={"svc_1": ["1girl", "solo", "ai:tagged"]},
            delete_by_service={"svc_1": ["queue:tagme"]},
        )

        # Verify atomic state transition
        assert db.get_pending_push_count() == 1
        assert db.get_staged_progress(h1) == {}
        assert db.get_inference_filter_hashes([h1], "model_a") == {h1: "fh_a"}
        assert db.get_inference_filter_hashes([h1], "model_b") == {h1: "fh_b"}
        assert db.get_run_targets(RunTargetState.DONE) == [(h1, "done", "image/png")]

        # Clear scratchpad
        db.clear_active_run()
        assert db.get_active_run() is None
        assert db.get_run_targets() == []


# endregion


# region Schema Migration Tests


def test_migration_v0_to_latest_folds_unpushed_queue(tmp_path: Path) -> None:
    """
    Construct a real v0 database (old multi-row schema with service_key column),
    run apply_migrations, and assert that it upgrades cleanly through v1 -> LATEST_VERSION.
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

        conn.execute(
            """
            INSERT INTO push_queue VALUES
            ('hash1', 'svc_model', 'add_tags', '["solo", "smile"]', 0, NULL, '2026-01-01'),
            ('hash1', 'svc_local', 'add_tags', '["ai:tagged"]', 1, 'Timeout', '2026-01-01')
            """
        )
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
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == LATEST_VERSION

        cursor = conn.execute("PRAGMA table_info(push_queue)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "service_key" not in cols
        assert "payload_json" in cols

        rows = conn.execute(
            "SELECT file_hash, payload_json, attempts, last_error FROM push_queue ORDER BY file_hash"
        ).fetchall()
        assert len(rows) == 2

        h1, p1_json, attempts1, last_err1 = rows[0]
        assert h1 == "hash1"
        assert attempts1 == 1
        assert last_err1 == "Timeout"
        p1 = json.loads(p1_json)
        assert p1["svc_model"]["add"] == ["solo", "smile"]
        assert p1["svc_local"]["add"] == ["ai:tagged"]

        h2, p2_json, _, _ = rows[1]
        assert h2 == "hash2"
        p2 = json.loads(p2_json)
        assert p2["svc_model"]["delete"] == ["temp:tag"]


def test_migration_v1_to_v2_preserves_inference_history_and_drops_json_cache(tmp_path: Path) -> None:
    """Verify v1 -> v2 migrates inference_cache completions into inference_log and drops legacy table."""
    db_file = tmp_path / "v1.db"

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
            CREATE TABLE inference_cache (
                file_hash    TEXT NOT NULL,
                model_id     TEXT NOT NULL,
                raw_json     TEXT NOT NULL,
                inferred_at  TEXT NOT NULL,
                PRIMARY KEY (file_hash, model_id)
            );
            CREATE TABLE push_queue (
                file_hash    TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                attempts     INTEGER NOT NULL DEFAULT 0,
                last_error   TEXT,
                created_at   TEXT NOT NULL
            );
            PRAGMA user_version = 1;
            """
        )
        conn.execute("INSERT INTO known_files VALUES ('h1', '/a.png', 'image/png', 'active', '2026-01-01')")
        conn.execute("INSERT INTO inference_cache VALUES ('h1', 'wd-swinv2-v3', '{}', '2026-01-01')")
        # Orphan row without matching known_files entry should be safely ignored
        conn.execute("INSERT INTO inference_cache VALUES ('orphan', 'wd-swinv2-v3', '{}', '2026-01-01')")

    with Database(db_file) as db:
        ver = db.conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == LATEST_VERSION

        # Legacy table dropped
        old_tbl = db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='inference_cache'"
        ).fetchone()
        assert old_tbl is None

        # Completion preserved with legacy sentinel filter hash
        hashes = db.get_inference_filter_hashes(["h1", "orphan"], "wd-swinv2-v3")
        assert hashes == {"h1": "__v1_legacy__"}


def test_new_database_stamps_latest_version_without_backup(tmp_path: Path) -> None:
    """Verify that a brand-new database gets stamped with LATEST_VERSION directly and creates no backup files."""
    db_file = tmp_path / "fresh.db"
    assert not db_file.exists()

    with Database(db_file) as db:
        ver = db.conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == LATEST_VERSION

    backup_files = list(tmp_path.glob("*.backup*"))
    assert len(backup_files) == 0


# endregion
