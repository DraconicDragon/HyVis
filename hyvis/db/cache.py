"""
cache_db.py — High-density, disposable binary prediction cache for HyVis.

Stores raw un-culled tagger outputs in a separate SQLite file (`cache.db`) using
packed sparse binary BLOBs instead of repeated JSON text.

Blob format (versioned by 1-byte header):
  - 0x01: [1B version] + [n * uint16 LE indices] + [n * uint16 LE quantized scores]
  - 0x02: [1B version] + [n * uint32 LE indices] + [n * uint16 LE quantized scores]
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

import numpy as np

if TYPE_CHECKING:
    from vibe.metadata import LabelCatalog
    from vibe.results import TagResult

logger = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = 1

_FORMAT_U16_PLAIN = 1
_FORMAT_U32_PLAIN = 2
_MAX_U16_INDEX = 65535

_CACHE_SCHEMA_SQL = """
-- 1. Registered Models & Vocab Fingerprints
CREATE TABLE IF NOT EXISTS models (
    model_row    INTEGER PRIMARY KEY,
    model_id     TEXT NOT NULL,
    variant_id   TEXT NOT NULL DEFAULT '',
    vocab_hash   TEXT NOT NULL,
    UNIQUE (model_id, variant_id, vocab_hash)
);

-- 2. Per-Model Tag Catalog & Optional Calibrated Thresholds
CREATE TABLE IF NOT EXISTS vocab (
    model_row             INTEGER NOT NULL REFERENCES models(model_row) ON DELETE CASCADE,
    idx                   INTEGER NOT NULL,
    tag                   TEXT NOT NULL,
    category              TEXT NOT NULL,
    calibrated_threshold  REAL,
    PRIMARY KEY (model_row, idx)
) WITHOUT ROWID;

-- 3. Packed Sparse Predictions (1 row per file per model)
CREATE TABLE IF NOT EXISTS predictions (
    model_row    INTEGER NOT NULL REFERENCES models(model_row) ON DELETE CASCADE,
    file_hash    BLOB NOT NULL,          -- 32 raw SHA-256 bytes
    floor_q      INTEGER NOT NULL,       -- Cache pruning floor quantized to [0, 65535]
    n            INTEGER NOT NULL,       -- Number of surviving tag entries
    data         BLOB NOT NULL,          -- Packed binary payload with 1-byte version header
    inferred_at  INTEGER NOT NULL,       -- Unix epoch milliseconds (UTC)
    PRIMARY KEY (model_row, file_hash)
);
"""


# region Encoding & Packing Helpers


def default_cache_path(main_db_path: str | Path) -> Path:
    """Resolve the default disposable cache.db path alongside the main hyvis.db."""
    p = Path(main_db_path)
    if str(p) == ":memory:":
        return Path(":memory:")
    return p.with_name("cache.db")


def quantize_floor(score: float) -> int:
    """Convert a floating-point score floor [0.0, 1.0] into a uint16 integer [0, 65535]."""
    clamped = max(0.0, min(1.0, float(score)))
    return round(clamped * 65535.0)


def encode_file_hash(file_hash: str) -> bytes:
    """Convert a 64-char hex SHA-256 string into 32 raw bytes (with safe fallback for non-hex IDs)."""
    if len(file_hash) == 64:
        try:
            return bytes.fromhex(file_hash)
        except ValueError:
            pass
    return b"raw:" + file_hash.encode("utf-8")


def decode_file_hash(raw: bytes) -> str:
    """Convert raw stored hash bytes back into a canonical string."""
    if len(raw) == 32:
        return raw.hex()
    if raw.startswith(b"raw:"):
        return raw[4:].decode("utf-8")
    return raw.hex()


def pack_sparse_predictions(indices: np.ndarray, scores: np.ndarray) -> tuple[int, bytes]:
    """
    Pack sorted (indices, scores) arrays into a versioned binary BLOB.
    Automatically selects 0x01 (uint16 indices) or 0x02 (uint32 indices) based on max index.
    Returns: (entry_count, packed_bytes)
    """
    n = len(indices)
    if n != len(scores):
        raise ValueError(f"Index/score length mismatch: {n} != {len(scores)}")

    if n == 0:
        return 0, bytes([_FORMAT_U16_PLAIN])

    max_idx = int(indices.max())
    scores_clipped = np.clip(scores, 0.0, 1.0)
    scores_q = np.rint(scores_clipped * 65535.0).astype("<u2")

    if max_idx <= _MAX_U16_INDEX:
        header = bytes([_FORMAT_U16_PLAIN])
        idx_bytes = indices.astype("<u2").tobytes()
    else:
        header = bytes([_FORMAT_U32_PLAIN])
        idx_bytes = indices.astype("<u4").tobytes()

    return n, header + idx_bytes + scores_q.tobytes()


def unpack_sparse_predictions(n: int, data: bytes) -> tuple[np.ndarray, np.ndarray]:
    """
    Unpack a versioned binary BLOB back into (indices: uint32, scores: float32).
    """
    if n == 0:
        return np.empty(0, dtype=np.uint32), np.empty(0, dtype=np.float32)

    if not data:
        raise ValueError("Cannot unpack empty prediction payload when n > 0.")

    fmt = data[0]
    mv = memoryview(data)

    if fmt == _FORMAT_U16_PLAIN:
        expected_len = 1 + (4 * n)
        if len(data) != expected_len:
            raise ValueError(f"Corrupt 0x01 BLOB length: expected {expected_len}, got {len(data)}")
        idx = np.frombuffer(mv[1 : 1 + (2 * n)], dtype="<u2").astype(np.uint32)
        scores_q = np.frombuffer(mv[1 + (2 * n) : expected_len], dtype="<u2")
        scores = scores_q.astype(np.float32) / np.float32(65535.0)
        return idx, scores

    if fmt == _FORMAT_U32_PLAIN:
        expected_len = 1 + (6 * n)
        if len(data) != expected_len:
            raise ValueError(f"Corrupt 0x02 BLOB length: expected {expected_len}, got {len(data)}")
        idx = np.frombuffer(mv[1 : 1 + (4 * n)], dtype="<u4").astype(np.uint32, copy=True)
        scores_q = np.frombuffer(mv[1 + (4 * n) : expected_len], dtype="<u2")
        scores = scores_q.astype(np.float32) / np.float32(65535.0)
        return idx, scores

    raise ValueError(f"Unsupported cache BLOB format version: 0x{fmt:02x}")


# endregion


# region Data Containers


@dataclass(frozen=True)
class CachedVocab:
    """In-memory tag catalog and calibrated threshold (TLT) map loaded from cache.db."""

    model_row: int
    model_id: str
    variant_id: str
    vocab_hash: str
    idx_to_tag: dict[int, str]
    idx_to_category: dict[int, str]
    calibrated_thresholds: dict[str, float]


# endregion


class CacheDatabase:
    """Manager for the disposable raw prediction cache (`cache.db`)."""

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self._path = Path(path)
        self._read_only = read_only
        self._conn: sqlite3.Connection | None = None
        self._batch_uncommitted = 0

    # region Connection Lifecycle

    def open(self) -> None:
        if self._conn is not None:
            return

        is_memory = str(self._path) == ":memory:"

        if self._read_only:
            if not is_memory and self._path.is_file():
                uri = f"{self._path.resolve().as_uri()}?mode=ro"
                self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=30.0)
                self._conn.execute("PRAGMA foreign_keys = ON")
                return
            # Missing cache in read-only / dry-run mode: open ephemeral in-memory schema
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._conn.executescript(_CACHE_SCHEMA_SQL)
            self._conn.execute("PRAGMA query_only = ON")
            return

        if not is_memory:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if self._path.exists() and not self._check_existing_version():
                self._unlink_db_files()

        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            timeout=30.0,
        )

        # Page size must be set before creating the first table
        is_empty = self._conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
        if is_empty:
            self._conn.execute("PRAGMA page_size = 16384")

        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA cache_size = -65536")  # 64MiB
        self._conn.executescript(_CACHE_SCHEMA_SQL)
        self._conn.execute(f"PRAGMA user_version = {CACHE_SCHEMA_VERSION}")
        self._conn.commit()
        logger.debug("Cache database opened at %s (schema v%d)", self._path, CACHE_SCHEMA_VERSION)

    def _check_existing_version(self) -> bool:
        """Verify existing cache.db schema version; return False if mismatched or corrupt."""
        tmp_conn: sqlite3.Connection | None = None
        try:
            tmp_conn = sqlite3.connect(str(self._path), timeout=10.0)
            row = tmp_conn.execute("PRAGMA user_version").fetchone()
            ver = int(row[0]) if row else 0
            if ver != CACHE_SCHEMA_VERSION:
                logger.info(
                    "Cache schema mismatch at %s (found v%d, expected v%d); recreating disposable cache.",
                    self._path,
                    ver,
                    CACHE_SCHEMA_VERSION,
                )
                return False
            return True
        except sqlite3.DatabaseError as exc:
            logger.warning("Cache database %s appears corrupt (%s); recreating disposable cache.", self._path, exc)
            return False
        finally:
            if tmp_conn is not None:
                tmp_conn.close()

    def _unlink_db_files(self) -> None:
        """Remove main cache file and WAL/SHM sidecars cleanly."""
        for suffix in ("", "-wal", "-shm"):
            target = Path(f"{self._path}{suffix}") if suffix else self._path
            try:
                if target.exists():
                    target.unlink()
            except OSError as exc:
                logger.warning("Could not delete stale cache file %s: %s", target, exc)

    def close(self) -> None:
        if self._conn is not None:
            if not self._read_only:
                self.commit()
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("CacheDatabase is not open. Call open() or use as context manager.")
        return self._conn

    def commit(self) -> None:
        if self._conn is not None and not self._read_only:
            self._conn.commit()
            self._batch_uncommitted = 0

    def batch_tick(self, batch_size: int = 50) -> None:
        if self._read_only:
            return
        self._batch_uncommitted += 1
        if self._batch_uncommitted >= batch_size:
            self.commit()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
            self.commit()
        except Exception:
            if self._conn is not None and not self._read_only:
                self._conn.rollback()
            raise

    # endregion

    # region Models & Vocabularies

    def register_model(
        self,
        model_id: str,
        variant_id: str | None,
        catalog: LabelCatalog,
        calibrated_thresholds: Mapping[str, float] | None = None,
    ) -> int:
        """
        Look up or register a (model_id, variant_id, vocab_hash) entry and its tag catalog.
        Returns the integer `model_row`.
        """
        norm_variant = variant_id or ""
        v_hash = catalog.vocab_hash

        row = self.conn.execute(
            "SELECT model_row FROM models WHERE model_id = ? AND variant_id = ? AND vocab_hash = ?",
            (model_id, norm_variant, v_hash),
        ).fetchone()

        if row is not None:
            return int(row[0])

        if self._read_only:
            return -1

        with self.transaction():
            cursor = self.conn.execute(
                "INSERT INTO models (model_id, variant_id, vocab_hash) VALUES (?, ?, ?)",
                (model_id, norm_variant, v_hash),
            )
            assert cursor.lastrowid is not None
            model_row = int(cursor.lastrowid)

            vocab_rows: list[tuple[int, int, str, str, float | None]] = []
            for lbl in catalog.labels:
                cat = str(lbl.category.value if hasattr(lbl.category, "value") else lbl.category)
                raw_cal = (
                    calibrated_thresholds.get(lbl.name, lbl.threshold)
                    if calibrated_thresholds is not None
                    else lbl.threshold
                )
                cal_val = float(raw_cal) if raw_cal is not None else None
                vocab_rows.append((model_row, int(lbl.index), lbl.name, cat, cal_val))

            self.conn.executemany(
                "INSERT INTO vocab (model_row, idx, tag, category, calibrated_threshold) VALUES (?, ?, ?, ?, ?)",
                vocab_rows,
            )

        logger.debug(
            "Registered cache model_row=%d (model=%s, variant=%r, vocab=%s, labels=%d)",
            model_row,
            model_id,
            norm_variant,
            v_hash,
            len(catalog.labels),
        )
        return model_row

    def find_model_rows(self, model_id: str, variant_id: str | None = None) -> list[int]:
        """Return matching `model_row` IDs ordered newest first."""
        norm_variant = variant_id or ""
        rows = self.conn.execute(
            "SELECT model_row FROM models WHERE model_id = ? AND variant_id = ? ORDER BY model_row DESC",
            (model_id, norm_variant),
        ).fetchall()
        return [int(r[0]) for r in rows]

    def get_vocab(self, model_row: int) -> CachedVocab | None:
        """Load the stored vocabulary and calibrated threshold map for a registered `model_row`."""
        meta = self.conn.execute(
            "SELECT model_id, variant_id, vocab_hash FROM models WHERE model_row = ?",
            (model_row,),
        ).fetchone()
        if meta is None:
            return None

        rows = self.conn.execute(
            "SELECT idx, tag, category, calibrated_threshold FROM vocab WHERE model_row = ? ORDER BY idx",
            (model_row,),
        ).fetchall()

        idx_to_tag: dict[int, str] = {}
        idx_to_cat: dict[int, str] = {}
        calibrated_thresholds: dict[str, float] = {}

        for idx, tag, cat, cal in rows:
            i = int(idx)
            idx_to_tag[i] = tag
            idx_to_cat[i] = cat
            if cal is not None:
                calibrated_thresholds[tag] = float(cal)

        return CachedVocab(
            model_row=model_row,
            model_id=meta[0],
            variant_id=meta[1],
            vocab_hash=meta[2],
            idx_to_tag=idx_to_tag,
            idx_to_category=idx_to_cat,
            calibrated_thresholds=calibrated_thresholds,
        )

    # endregion

    # region Predictions Read / Write

    def save_prediction(
        self,
        model_row: int,
        file_hash: str,
        indices: np.ndarray,
        scores: np.ndarray,
        *,
        min_cache_score: float,
    ) -> None:
        """Pack and store sparse raw predictions for one (model_row, file_hash)."""
        if self._read_only or model_row < 0:
            return

        n, blob = pack_sparse_predictions(indices, scores)
        floor_q = quantize_floor(min_cache_score)
        hash_bytes = encode_file_hash(file_hash)
        now_ms = int(time.time() * 1000)

        self.conn.execute(
            """
            INSERT INTO predictions (model_row, file_hash, floor_q, n, data, inferred_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(model_row, file_hash) DO UPDATE SET
                floor_q     = excluded.floor_q,
                n           = excluded.n,
                data        = excluded.data,
                inferred_at = excluded.inferred_at
            """,
            (model_row, hash_bytes, floor_q, n, blob, now_ms),
        )
        self.batch_tick()

    def filter_cached_hashes(
        self,
        model_row: int,
        file_hashes: Sequence[str],
        *,
        max_floor_q: int,
    ) -> set[str]:
        """
        Return the subset of `file_hashes` present in `predictions` for `model_row`
        whose stored `floor_q <= max_floor_q` (deep enough for the requested threshold).
        """
        if not file_hashes or model_row < 0:
            return set()

        bytes_to_hex = {encode_file_hash(h): h for h in file_hashes}
        all_bytes = list(bytes_to_hex.keys())
        found: set[str] = set()

        for i in range(0, len(all_bytes), 900):
            chunk = all_bytes[i : i + 900]
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"""
                SELECT file_hash FROM predictions
                WHERE model_row = ? AND floor_q <= ? AND file_hash IN ({placeholders})
                """,
                [model_row, max_floor_q, *chunk],
            ).fetchall()
            for (raw_h,) in rows:
                orig = bytes_to_hex.get(bytes(raw_h))
                if orig is not None:
                    found.add(orig)

        return found

    def get_prediction(
        self,
        model_row: int,
        file_hash: str,
    ) -> tuple[np.ndarray, np.ndarray, int] | None:
        """
        Retrieve unpacked sparse predictions for one file.
        Returns: (indices: uint32, scores: float32, floor_q: int) or None if missing.
        """
        if model_row < 0:
            return None

        row = self.conn.execute(
            "SELECT n, data, floor_q FROM predictions WHERE model_row = ? AND file_hash = ?",
            (model_row, encode_file_hash(file_hash)),
        ).fetchone()
        if row is None:
            return None

        n, data, floor_q = int(row[0]), bytes(row[1]), int(row[2])
        indices, scores = unpack_sparse_predictions(n, data)
        return indices, scores, floor_q

    @staticmethod
    def reconstruct_tag_result(
        vocab: CachedVocab,
        indices: np.ndarray,
        scores: np.ndarray,
    ) -> TagResult:
        """
        Reconstruct a materialized TagResult containing only the cached surviving entries,
        sorted descending by score per category.
        """
        from vibe.results import TagEntry, TagResult

        categories: dict[str, list[TagEntry]] = {}
        idx_to_tag = vocab.idx_to_tag
        idx_to_cat = vocab.idx_to_category

        for i, s in zip(indices.tolist(), scores.tolist(), strict=True):
            tag = idx_to_tag.get(i)
            cat = idx_to_cat.get(i)
            if tag is not None and cat is not None:
                categories.setdefault(cat, []).append(TagEntry(tag=tag, score=s))

        for entries in categories.values():
            entries.sort(key=lambda e: e.score, reverse=True)

        return TagResult(categories=categories)

    # endregion

    # region Maintenance & Inspection

    def clear_cache(self) -> int:
        """Delete all cached models, vocabularies, and predictions, and VACUUM disk space."""
        if self._read_only:
            return 0

        count_row = self.conn.execute("SELECT COUNT(*) FROM predictions").fetchone()
        count = int(count_row[0]) if count_row else 0

        with self.transaction():
            self.conn.execute("DELETE FROM predictions")
            self.conn.execute("DELETE FROM vocab")
            self.conn.execute("DELETE FROM models")

        old_isolation = self.conn.isolation_level
        try:
            self.conn.isolation_level = None
            self.conn.execute("VACUUM")
        finally:
            self.conn.isolation_level = old_isolation

        return count

    def purge_orphans(self, valid_file_hashes: Sequence[str]) -> int:
        """Remove cached predictions for hashes no longer present in the main library."""
        if self._read_only:
            return 0

        valid_bytes = {encode_file_hash(h) for h in valid_file_hashes}
        existing_rows = self.conn.execute("SELECT DISTINCT file_hash FROM predictions").fetchall()
        orphans = [bytes(r[0]) for r in existing_rows if bytes(r[0]) not in valid_bytes]

        if not orphans:
            return 0

        deleted = 0
        with self.transaction():
            for i in range(0, len(orphans), 900):
                chunk = orphans[i : i + 900]
                placeholders = ",".join("?" * len(chunk))
                cur = self.conn.execute(
                    f"DELETE FROM predictions WHERE file_hash IN ({placeholders})",
                    chunk,
                )
                deleted += cur.rowcount

        return deleted

    def get_stats(self) -> dict[str, Any]:
        """Return summary statistics (model count, prediction count, and size on disk)."""
        models_count = int(self.conn.execute("SELECT COUNT(*) FROM models").fetchone()[0])
        preds_count = int(self.conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0])
        size_bytes = self._path.stat().st_size if str(self._path) != ":memory:" and self._path.is_file() else 0
        return {
            "models": models_count,
            "predictions": preds_count,
            "size_bytes": size_bytes,
        }

    # endregion
