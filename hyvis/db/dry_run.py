"""
dry_run.py - Read-only database shadow for test and dry-run execution.
Guarantees zero SQLite disk writes while maintaining an in-memory overlay
so multi-model inference checks and pipeline counters work completely naturally.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path

from hyvis.db.hyvis_db import (
    _SCHEMA_SQL,
    Database,
    FileStatus,
    PushAction,
    RunMode,
    RunTargetState,
)

logger = logging.getLogger(__name__)


class DryRunDatabase(Database):
    """
    Read-only Database wrapper for simulation and dry-runs.
    Reads existing state without modification and tracks simulated outputs in memory.
    """

    def __init__(self, path: str | Path, *, min_cache_score: float = 0.005) -> None:
        super().__init__(path, min_cache_score=min_cache_score)
        # In-memory overlay: {(file_hash, model_id): filter_hash}
        self._dry_run_inferred: dict[tuple[str, str], str] = {}
        # In-memory staging overlay: {file_hash: {model_id: {svc_key: [tags]}}}
        self._dry_run_staged: dict[str, dict[str, dict[str, list[str]]]] = {}
        self.simulated_enqueued_tags: int = 0
        self.simulated_files_with_tags: set[str] = set()

    def open(self) -> None:
        if self._conn is not None:
            return

        # If database file exists, open it in strict query-only mode
        if self._path.is_file():
            self._conn = sqlite3.connect(
                str(self._path),
                check_same_thread=False,
                timeout=30.0,
            )
            self._conn.execute("PRAGMA query_only = ON;")
            logger.debug("DryRunDatabase opened existing DB at %s (read-only mode)", self._path)
        else:
            # If DB doesn't exist yet, run on an in-memory mock so zero files are created on disk
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.execute("PRAGMA query_only = ON;")
            logger.debug("DryRunDatabase initialized in-memory mock schema (disk untouched)")

    def commit(self) -> None:
        """No-op: Never commit changes in dry run mode."""
        self._batch_uncommitted = 0

    def batch_tick(self, batch_size: int = 50) -> None:
        """No-op."""
        del batch_size

    def upsert_file(
        self,
        file_hash: str,
        *,
        file_path: str | None = None,
        mime: str | None = None,
        num_frames: int | None = None,
        status: FileStatus | str = FileStatus.ACTIVE,
    ) -> None:
        """No-op: Drop file metadata persistence."""
        del file_hash, file_path, mime, num_frames, status

    def mark_file_status(self, file_hash: str, status: FileStatus | str) -> None:
        """No-op."""
        del file_hash, status

    # region Inference Log Shadow

    def record_inference(
        self,
        file_hash: str,
        model_id: str,
        *,
        variant_id: str = "",
        vocab_hash: str = "",
        filter_hash: str = "",
    ) -> None:
        del variant_id, vocab_hash
        self._dry_run_inferred[(file_hash, model_id)] = filter_hash

    def get_inference_filter_hashes(
        self,
        file_hashes: Sequence[str],
        model_id: str,
    ) -> dict[str, str]:
        base = super().get_inference_filter_hashes(file_hashes, model_id)
        for h in file_hashes:
            if (h, model_id) in self._dry_run_inferred:
                base[h] = self._dry_run_inferred[(h, model_id)]
        return base

    # endregion

    # region Active Run & Staging Shadow

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
        del config_toml, config_path, mode, force_mode, hyvis_version, candidate_files, rejected_files

    def stage_model_progress(
        self,
        file_hash: str,
        model_id: str,
        staged_payload: Mapping[str, Sequence[str]],
    ) -> None:
        clean = {k: list(v) for k, v in staged_payload.items() if v}
        self._dry_run_staged.setdefault(file_hash, {})[model_id] = clean

    def get_active_run(self) -> None:
        """Dry-run mode ignores any active/interrupted run on disk."""
        return

    def get_staged_hashes_for_model(self, model_id: str) -> set[str]:
        return {h for h, m_map in self._dry_run_staged.items() if model_id in m_map}

    def get_staged_progress(self, file_hash: str) -> dict[str, dict[str, list[str]]]:
        return dict(self._dry_run_staged.get(file_hash, {}))

    def commit_file_completion(
        self,
        file_hash: str,
        *,
        completed_models: Sequence[tuple[str, str, str, str]],
        add_by_service: Mapping[str, Sequence[str]] | None = None,
        delete_by_service: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        for tags in (add_by_service or {}).values():
            if tags:
                self.simulated_enqueued_tags += len(tags)
                self.simulated_files_with_tags.add(file_hash)

        for model_id, _v, _vh, f_hash in completed_models:
            self._dry_run_inferred[(file_hash, model_id)] = f_hash

        self._dry_run_staged.pop(file_hash, None)

    def clear_active_run(self) -> None:
        self._dry_run_staged.clear()

    def get_run_targets(self, state: RunTargetState | str | None = None) -> list[tuple[str, str, str | None]]:
        del state
        return []

    # endregion

    # region Push Queue Shadow

    def enqueue_push(
        self,
        file_hash: str,
        service_key: str,
        tags: Sequence[str],
        action: PushAction = "add_tags",
    ) -> None:
        """Track tag counts and file matches in-memory without touching SQLite."""
        del service_key, action
        if tags:
            self.simulated_enqueued_tags += len(tags)
            self.simulated_files_with_tags.add(file_hash)

    def get_pending_push_count(self) -> int:
        return len(self.simulated_files_with_tags)

    def record_push_attempt_error(self, file_hash: str, error_msg: str) -> None:
        del file_hash, error_msg

    def remove_from_push_queue(self, file_hashes: Sequence[str]) -> None:
        del file_hashes

    def clear_file_from_push_queue(self, file_hash: str) -> None:
        del file_hash

    # endregion
