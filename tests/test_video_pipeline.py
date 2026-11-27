"""
test_video_pipeline.py — Integration tests for video/animation routing, cache.db v1->v2 migration,
prep_hash invalidation isolation, FrameDiskCache lifecycle, and multi-model streaming inference.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Self
from unittest.mock import MagicMock

import numpy as np
import pytest
import vibe
from PIL import Image
from vibe.image_loading import iter_raw_chunks, normalize_input_format
from vibe.metadata import LabelCatalog, LabelInfo
from vibe.results import InferenceResult, InferenceResultItem, TagResult

from hyvis.config import AppConfig
from hyvis.db import CACHE_SCHEMA_VERSION, CacheDatabase, Database, RunMode
from hyvis.hydrus import FileInfo, HydrusClient
from hyvis.inference import infer_files, plan_model_pass
from hyvis.progress import Progress
from hyvis.video.extractor import ExtractedFrame
from hyvis.video.frame_cache import FrameDiskCache


def _make_video_config(
    *,
    video_enabled: bool = True,
    density_k: float = 2.0,
    default_threshold: float = 0.40,
    models: list[dict[str, Any]] | None = None,
    frame_cache_mode: str = "auto",
    frame_cache_dir: str | None = None,
    db_path: str = "data/hyvis.db",
) -> AppConfig:
    return AppConfig.model_validate(
        {
            "hydrus": {
                "api_url": "http://127.0.0.1:45869",
                "api_key": "test_key",
                "tag_queries": [{"tags": ["system:limit is 10"], "tag_service_keys": []}],
                "output_tag_services": {"keys": ["svc_main"]},
            },
            "inference": {
                "models": models or [{"model_id": "wd-swinv2-v3"}],
            },
            "output_filter": {
                "default_threshold": default_threshold,
                "prefer_tag_level_thresholds": False,
            },
            "database": {
                "path": db_path,
                "min_cache_score": 0.005,
            },
            "video": {
                "enabled": video_enabled,
                "density_k": density_k,
                "min_frames": 4,
                "max_frames": 32,
                "sparse_below_frames": 32,
                "frame_cache_mode": frame_cache_mode,
                "frame_cache_dir": frame_cache_dir,
            },
        }
    )


def _write_animated_webp(path: Path, count: int = 6) -> None:
    frames = [Image.new("RGB", (32, 32), (i * 40, 20, 200 - i * 25)) for i in range(count)]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=80, loop=0)


class _FakeSession:
    """Shared mock vibe.ModelSession satisfying tagger contracts and streaming chunks."""

    def __init__(
        self,
        model_id: str,
        catalog: LabelCatalog,
        score_fn: Callable[[Any, Any], TagResult],
    ) -> None:
        self.model_id = model_id
        self.backend = vibe.Backend.PYTORCH
        self.variant = ""
        self.capabilities = ("LabelCatalogProvider",)
        self.tagger = vibe.TaggerView(catalog=catalog, thresholds=None, recommendation=None)
        self._score_fn = score_fn

    def execution_info(self) -> dict[str, Any]:
        return {"runtime": {"device": "cpu", "precision": {"weight_dtype": "fp32", "compute_dtype": "fp32"}}}

    def is_cancellation_requested(self) -> bool:
        return False

    async def infer_async(
        self,
        images: Any,
        *,
        refs: Any = None,
        batch_size: int = 1,
    ) -> AsyncIterator[InferenceResult]:
        norm = normalize_input_format(images, refs=refs)
        for start_idx, batch_vals, batch_refs in iter_raw_chunks(norm, batch_size=batch_size):
            items = [
                InferenceResultItem(
                    index=start_idx + offset,
                    result=self._score_fn(val, ref),
                    input_ref=ref,
                )
                for offset, (val, ref) in enumerate(zip(batch_vals, batch_refs, strict=True))
            ]
            yield InferenceResult(items=items)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass


# region Cache Migration & Known Frames Persistence


def test_cache_db_v1_to_v2_migration_preserves_predictions(tmp_path: Path) -> None:
    """Verify upgrading cache.db v1 -> v2 adds prep_hash='' without wiping existing predictions."""
    cache_path = tmp_path / "cache.db"
    h_existing = "a" * 64

    # 1. Create a v1 cache.db manually
    with sqlite3.connect(cache_path) as conn:
        conn.executescript(
            """
            CREATE TABLE models (
                model_row    INTEGER PRIMARY KEY,
                model_id     TEXT NOT NULL,
                variant_id   TEXT NOT NULL DEFAULT '',
                vocab_hash   TEXT NOT NULL,
                UNIQUE (model_id, variant_id, vocab_hash)
            );
            CREATE TABLE vocab (
                model_row             INTEGER NOT NULL REFERENCES models(model_row) ON DELETE CASCADE,
                idx                   INTEGER NOT NULL,
                tag                   TEXT NOT NULL,
                category              TEXT NOT NULL,
                calibrated_threshold  REAL,
                PRIMARY KEY (model_row, idx)
            ) WITHOUT ROWID;
            CREATE TABLE predictions (
                model_row    INTEGER NOT NULL REFERENCES models(model_row) ON DELETE CASCADE,
                file_hash    BLOB NOT NULL,
                floor_q      INTEGER NOT NULL,
                n            INTEGER NOT NULL,
                data         BLOB NOT NULL,
                inferred_at  INTEGER NOT NULL,
                PRIMARY KEY (model_row, file_hash)
            );
            PRAGMA user_version = 1;
            """
        )
        conn.execute("INSERT INTO models VALUES (1, 'wd-swinv2-v3', '', 'vh123')")
        conn.execute("INSERT INTO vocab VALUES (1, 0, '1girl', 'general', NULL)")
        # Format 0x01: header(1) + idx(u16=0) + score(u16=52428 ~ 0.8)
        blob = bytes([1]) + np.array([0], dtype="<u2").tobytes() + np.array([52428], dtype="<u2").tobytes()
        conn.execute(
            "INSERT INTO predictions VALUES (1, ?, 328, 1, ?, 1700000000000)",
            (bytes.fromhex(h_existing), blob),
        )

    # 2. Open with CacheDatabase (should migrate in place, preserving row)
    with CacheDatabase(cache_path) as cdb:
        ver = cdb.conn.execute("PRAGMA user_version").fetchone()[0]
        assert ver == CACHE_SCHEMA_VERSION == 2

        cols = {r[1] for r in cdb.conn.execute("PRAGMA table_info(predictions)").fetchall()}
        assert "prep_hash" in cols

        assert cdb.get_stats()["predictions"] == 1
        found = cdb.filter_cached_hashes(1, [h_existing], max_floor_q=328)
        assert found == {h_existing}

        pred = cdb.get_prediction(1, h_existing)
        assert pred is not None
        indices, scores, _ = pred
        assert indices.tolist() == [0]
        assert scores[0] == pytest.approx(0.8, abs=1e-3)


def test_bulk_get_known_frames_roundtrip(tmp_path: Path) -> None:
    """Verify known_files.num_frames is stored and retrieved for --resume."""
    db_path = tmp_path / "hyvis.db"
    h_anim = "1" * 64
    h_unknown = "2" * 64

    with Database(db_path) as db:
        db.upsert_file(h_anim, file_path="/a.webp", mime="image/webp", num_frames=18)
        db.upsert_file(h_unknown, file_path="/b.png", mime="image/png", num_frames=None)
        db.commit()

        assert db.bulk_get_known_frames([h_anim, h_unknown]) == {h_anim: 18}


# endregion


# region MIME Filtering & Prep-Hash Isolation


def test_filter_by_mime_rejects_animations_when_video_disabled() -> None:
    """When allow_video=False, videos/sequences and num_frames>1 animations reject; static GIF/HEIC/QOI/ICO pass."""
    client = HydrusClient("http://127.0.0.1:45869", "key")
    mock_api = MagicMock()
    mock_api.get_file_metadata.return_value = {
        "metadata": [
            {"hash": "1" * 64, "mime": "image/jpeg", "size": 100},
            {"hash": "2" * 64, "mime": "image/webp", "size": 200, "num_frames": 12},
            {"hash": "3" * 64, "mime": "video/mp4", "size": 300, "num_frames": 90},
            {"hash": "4" * 64, "mime": "image/webp", "size": 150, "num_frames": 1},
            {"hash": "5" * 64, "mime": "image/gif", "size": 180, "num_frames": 8},
            {"hash": "6" * 64, "mime": "image/gif", "size": 120, "num_frames": 1},
            {"hash": "7" * 64, "mime": "image/heic", "size": 250},
            {"hash": "8" * 64, "mime": "image/qoi", "size": 90},
            {"hash": "9" * 64, "mime": "image/x-icon", "size": 40},
            {"hash": "a" * 64, "mime": "image/avif-sequence", "size": 310, "num_frames": 15},
        ]
    }
    client._client = mock_api

    all_hashes = ["1" * 64, "2" * 64, "3" * 64, "4" * 64, "5" * 64, "6" * 64, "7" * 64, "8" * 64, "9" * 64, "a" * 64]

    acc_off, rej_mimes_off, rej_hashes_off = client.filter_by_mime(all_hashes, allow_video=False)
    assert {fi.file_hash for fi in acc_off} == {"1" * 64, "4" * 64, "6" * 64, "7" * 64, "8" * 64, "9" * 64}
    assert set(rej_hashes_off) == {"2" * 64, "3" * 64, "5" * 64, "a" * 64}
    assert "image/webp (animated)" in rej_mimes_off
    assert "image/gif (animated)" in rej_mimes_off
    assert "video/mp4" in rej_mimes_off
    assert "image/avif-sequence" in rej_mimes_off

    acc_on, rej_mimes_on, rej_hashes_on = client.filter_by_mime(all_hashes, allow_video=True)
    assert len(acc_on) == 10
    assert not rej_mimes_on and not rej_hashes_on


def test_prep_hash_change_only_invalidates_multiframe_files(tmp_path: Path) -> None:
    """
    Changing video.density_k must force GPU re-inference for videos/animations,
    while leaving static images skipped. Changing output_filter must CPU re-filter both.
    """
    db_path = tmp_path / "hyvis.db"
    cache_path = tmp_path / "cache.db"

    h_static = "1" * 64
    h_video = "2" * 64
    files = [
        FileInfo(h_static, "image/png", 100, "/s.png", num_frames=1),
        FileInfo(h_video, "video/mp4", 500, "/v.mp4", num_frames=120),
    ]

    cfg_v1 = _make_video_config(density_k=2.0, default_threshold=0.40)
    cfg_v2_density = _make_video_config(density_k=4.0, default_threshold=0.40)
    cfg_v2_thresh = _make_video_config(density_k=2.0, default_threshold=0.50)
    m_cfg = cfg_v1.inference.models[0]

    cat = LabelCatalog(
        labels=(LabelInfo(index=0, name="1girl", category="general"),),
        categories=("general",),
    )

    with Database(db_path) as db, CacheDatabase(cache_path) as cdb:
        m_row = cdb.register_model(m_cfg.model_id, "", cat)
        p1 = plan_model_pass(m_cfg, files, config=cfg_v1, db=db, cache_db=cdb, force=False)

        for fi in files:
            db.upsert_file(fi.file_hash, file_path=fi.local_path, mime=fi.mime, num_frames=fi.num_frames)
            db.record_inference(fi.file_hash, m_cfg.model_id, filter_hash=p1.effective_filter_hash(fi))
            cdb.save_prediction(
                m_row,
                fi.file_hash,
                np.array([0], dtype=np.uint32),
                np.array([0.8], dtype=np.float32),
                min_cache_score=0.005,
                prep_hash=p1.video_prep_hash if fi.file_hash == h_video else "",
            )
        db.commit()
        cdb.commit()

        # 1. Density changed: static MUST skip; video MUST go to GPU (not CPU refilter!)
        p_dens = plan_model_pass(m_cfg, files, config=cfg_v2_density, db=db, cache_db=cdb, force=False)
        assert p_dens.skip_hashes == {h_static}
        assert p_dens.refilter_files == []
        assert [fi.file_hash for fi in p_dens.files_to_infer] == [h_video]

        # 2. Threshold changed (density same): BOTH static and video route to CPU re-filter
        p_thresh = plan_model_pass(m_cfg, files, config=cfg_v2_thresh, db=db, cache_db=cdb, force=False)
        assert p_thresh.skip_hashes == set()
        assert {fi.file_hash for fi in p_thresh.refilter_files} == {h_static, h_video}
        assert p_thresh.files_to_infer == []


# endregion


# region FrameDiskCache Unit Tests


def test_frame_disk_cache_lifecycle_and_partial_cleanup(tmp_path: Path) -> None:
    """Verify Q97 JPEG saving, loading, prep_hash validation, partial abort cleanup, and clear_all."""
    fc = FrameDiskCache(tmp_path / "frame_cache")
    fh = "f" * 64

    frames = [
        ExtractedFrame(index=0, source_index=0, timestamp_s=0.0, image=Image.new("RGB", (16, 16), (250, 10, 10))),
        ExtractedFrame(index=1, source_index=5, timestamp_s=0.5, image=Image.new("RGB", (16, 16), (10, 250, 10))),
    ]

    # 1. Save and stream
    out = list(fc.save_and_yield_frames(fh, "prep_aaa", iter(frames)))
    assert len(out) == 2
    assert fc.has_complete_frames(fh, "prep_aaa") is True
    assert fc.has_complete_frames(fh, "prep_bbb") is False  # prep_hash mismatch

    loaded = list(fc.load_frames(fh, "prep_aaa"))
    assert len(loaded) == 2
    assert loaded[0].source_index == 0 and loaded[1].source_index == 5
    assert loaded[0].image.getpixel((8, 8))[0] > 200  # ty:ignore[not-subscriptable]
    assert loaded[1].image.getpixel((8, 8))[1] > 200  # ty:ignore[not-subscriptable]

    # 2. Aborted generator removes incomplete folder
    fh_bad = "e" * 64

    def _broken_stream():
        yield frames[0]
        raise RuntimeError("corrupt mid-stream")

    gen = fc.save_and_yield_frames(fh_bad, "prep_aaa", _broken_stream())
    assert next(gen).index == 0
    with pytest.raises(RuntimeError, match="corrupt"):
        next(gen)
    assert not (fc.root / fh_bad).exists()

    # 3. cleanup_file and clear_all
    assert fc.clear_all() == 1
    assert fc.has_complete_frames(fh, "prep_aaa") is False


# endregion


# region End-to-End Inference Integration (Mocked vibe.load)


def test_unprobed_animated_webp_blocked_when_video_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    If Hydrus metadata omitted num_frames (None) on an animated WebP and video.enabled=False,
    infer_files must detect the animation via header check and skip without invoking vibe.load.
    """
    anim_path = tmp_path / "hidden_anim.webp"
    _write_animated_webp(anim_path, count=5)

    h_anim = "c" * 64
    fi = FileInfo(h_anim, "image/webp", 500, str(anim_path), num_frames=None)

    cfg = _make_video_config(video_enabled=False, db_path=str(tmp_path / "hyvis.db"))
    load_mock = MagicMock(side_effect=AssertionError("vibe.load should not be called!"))
    monkeypatch.setattr(vibe, "load", load_mock)

    with Database(tmp_path / "hyvis.db") as db, CacheDatabase(tmp_path / "cache.db") as cdb:
        stats = asyncio.run(
            infer_files(
                cfg.inference.models[0],
                [fi],
                config=cfg,
                db=db,
                cache_db=cdb,
                progress=Progress(total=1),
                force=False,
            )
        )

    assert stats.skipped == 1
    assert stats.ok == 0
    assert not load_mock.called


def test_multimodel_mixed_static_and_animated_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    End-to-end 2-model run over 1 static PNG + 1 animated WebP (with num_frames=None from Hydrus):
      - Verifies header discovery promotes animated WebP to multiframe pass.
      - Verifies FrameDiskCache saves frames on Model 1, reuses them on Model 2 (no re-extract),
        and deletes the frame folder upon Model 2's final commit.
      - Verifies FrameMaxAggregator preserves a tag that peaks on a single frame.
    """
    static_path = tmp_path / "static.png"
    Image.new("RGB", (32, 32), (10, 10, 200)).save(static_path)

    anim_path = tmp_path / "anim.webp"
    _write_animated_webp(anim_path, count=6)  # 6 distinct colors; R channel = idx * 40

    h_static = "1" * 64
    h_anim = "2" * 64
    files = [
        FileInfo(h_static, "image/png", 100, str(static_path), num_frames=1),
        FileInfo(h_anim, "image/webp", 200, str(anim_path), num_frames=None),
    ]

    fc_dir = tmp_path / "frame_cache"
    cfg = _make_video_config(
        video_enabled=True,
        models=[
            {"model_id": "model_1", "batch_size": 2},
            {"model_id": "model_2", "batch_size": 2},
        ],
        frame_cache_mode="auto",  # >1 model -> disk cache
        frame_cache_dir=str(fc_dir),
        db_path=str(tmp_path / "hyvis.db"),
    )

    cat_1 = LabelCatalog(
        labels=(
            LabelInfo(index=0, name="1girl", category="general"),
            LabelInfo(index=1, name="cameo_tag", category="general"),
        ),
        categories=("general",),
    )
    cat_2 = LabelCatalog(
        labels=(LabelInfo(index=0, name="smile", category="general"),),
        categories=("general",),
    )

    def _score_m1(val: Any, _ref: Any) -> TagResult:
        if isinstance(val, Image.Image):
            r = val.getpixel((16, 16))[0]
            cameo_score = 0.88 if r > 100 else 0.05
            scores = np.array([0.82, cameo_score], dtype=np.float32)
        else:
            scores = np.array([0.75, 0.10], dtype=np.float32)
        return TagResult.from_arrays(tag_names=cat_1.names, scores=scores, category_indices={"general": (0, 1)})

    def _score_m2(_val: Any, _ref: Any) -> TagResult:
        return TagResult.from_arrays(
            tag_names=cat_2.names,
            scores=np.array([0.91], dtype=np.float32),
            category_indices={"general": (0,)},
        )

    def _load(model_id: str, **kwargs: Any) -> _FakeSession:
        if model_id == "model_1":
            return _FakeSession(model_id, cat_1, _score_m1)
        return _FakeSession(model_id, cat_2, _score_m2)

    monkeypatch.setattr(vibe, "load", _load)

    extract_calls = 0
    import hyvis.video.extractor as real_extractor

    orig_iter_extract = real_extractor.iter_extract_frames

    def _counting_extract(*args: Any, **kwargs: Any) -> Any:
        nonlocal extract_calls
        extract_calls += 1
        return orig_iter_extract(*args, **kwargs)

    monkeypatch.setattr(real_extractor, "iter_extract_frames", _counting_extract)

    with Database(tmp_path / "hyvis.db") as db, CacheDatabase(tmp_path / "cache.db") as cdb:
        db.start_active_run(
            "[hydrus]\napi_url='http://127.0.0.1:45869'",
            mode=RunMode.DEFAULT,
            hyvis_version="test",
            candidate_files=[(fi.file_hash, fi.mime) for fi in files],
        )

        last_active = {fi.file_hash: "model_2" for fi in files}
        meta_by_id: dict[str, tuple[str, str, str]] = {}

        async def _run() -> None:
            # Model 1 pass
            s1 = await infer_files(
                cfg.inference.models[0],
                files,
                config=cfg,
                db=db,
                cache_db=cdb,
                progress=Progress(total=2),
                force=False,
                last_active_model_for_file=last_active,
                model_meta_by_id=meta_by_id,
            )
            assert s1.ok == 2 and s1.errors == 0
            # Disk frame cache MUST persist after Model 1 so Model 2 reuses it
            assert (fc_dir / h_anim / "manifest.json").is_file()
            assert extract_calls == 1

            # Model 2 pass
            s2 = await infer_files(
                cfg.inference.models[1],
                files,
                config=cfg,
                db=db,
                cache_db=cdb,
                progress=Progress(total=2),
                force=False,
                last_active_model_for_file=last_active,
                model_meta_by_id=meta_by_id,
            )
            assert s2.ok == 2 and s2.errors == 0
            # Model 2 loaded cached frames from disk instead of re-extracting!
            assert extract_calls == 1
            # Final commit deleted temporary frame folder immediately
            assert not (fc_dir / h_anim).exists()

        asyncio.run(_run())
        db.clear_active_run()

        batch = dict(db.fetch_push_batch(limit=10))
        assert set(batch[h_static]["svc_main"]["add"]) == {"1girl", "smile"}
        # cameo_tag appeared on later frames of anim.webp -> preserved by max merge!
        assert set(batch[h_anim]["svc_main"]["add"]) == {"1girl", "cameo tag", "smile"}

        # Check known_files recorded updated frame count & cache.db stored prep_hash
        assert db.bulk_get_known_frames([h_anim]) == {h_anim: 6}

        m1_row = cdb.find_model_rows("model_1")[0]
        row = cdb.conn.execute(
            "SELECT prep_hash FROM predictions WHERE model_row = ? AND file_hash = ?",
            (m1_row, bytes.fromhex(h_anim)),
        ).fetchone()
        assert row is not None and row[0] == cfg.video.prep_hash()

        # Verify BOTH model_1 and model_2 recorded video_filter_hash (not static_filter_hash)
        # and skip cleanly on a subsequent run when num_frames is restored from known_files.
        known_frames = db.bulk_get_known_frames([h_static, h_anim])
        next_run_files = [
            FileInfo(h_static, "image/png", 100, str(static_path), num_frames=known_frames.get(h_static)),
            FileInfo(h_anim, "image/webp", 200, str(anim_path), num_frames=known_frames.get(h_anim)),
        ]
        for m_cfg in cfg.inference.models:
            next_plan = plan_model_pass(m_cfg, next_run_files, config=cfg, db=db, cache_db=cdb, force=False)
            assert next_plan.skip_hashes == {h_static, h_anim}
            assert next_plan.refilter_files == []
            assert next_plan.files_to_infer == []


def test_frame_cache_corruption_recovery_and_pass_a_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    1. Corrupt a cached JPEG on disk -> validate_frames invalidates and re-extracts without marking file ERROR.
    2. Final model running via Pass A (CPU re-filter) still deletes the temporary frame_cache directory.
    """
    anim_path = tmp_path / "anim.webp"
    _write_animated_webp(anim_path, count=6)

    h_anim = "4" * 64
    fi = FileInfo(h_anim, "image/webp", 200, str(anim_path), num_frames=6)

    fc_dir = tmp_path / "frame_cache"
    cfg = _make_video_config(
        video_enabled=True,
        models=[{"model_id": "model_1"}, {"model_id": "model_2"}],
        frame_cache_mode="disk",
        frame_cache_dir=str(fc_dir),
        db_path=str(tmp_path / "hyvis.db"),
    )

    fc = FrameDiskCache(fc_dir)
    list(
        fc.save_and_yield_frames(
            h_anim,
            cfg.video.prep_hash(),
            iter(
                [ExtractedFrame(index=0, source_index=0, timestamp_s=0.0, image=Image.new("RGB", (16, 16), (1, 2, 3)))]
            ),
        )
    )
    (fc_dir / h_anim / "0000.jpg").write_bytes(b"corrupt_not_a_jpeg")

    cat_1 = LabelCatalog(labels=(LabelInfo(index=0, name="1girl", category="general"),), categories=("general",))
    cat_2 = LabelCatalog(labels=(LabelInfo(index=0, name="smile", category="general"),), categories=("general",))

    monkeypatch.setattr(
        vibe,
        "load",
        lambda model_id, **kwargs: _FakeSession(
            model_id,
            cat_1,
            lambda _v, _r: TagResult.from_arrays(
                tag_names=cat_1.names,
                scores=np.array([0.90], dtype=np.float32),
                category_indices={"general": (0,)},
            ),
        ),
    )

    with Database(tmp_path / "hyvis.db") as db, CacheDatabase(tmp_path / "cache.db") as cdb:
        row_2 = cdb.register_model("model_2", "", cat_2)
        cdb.save_prediction(
            row_2,
            h_anim,
            np.array([0], dtype=np.uint32),
            np.array([0.85], dtype=np.float32),
            min_cache_score=0.005,
            prep_hash=cfg.video.prep_hash(),
        )
        cdb.commit()

        db.start_active_run(
            "[hydrus]\napi_url='http://127.0.0.1:45869'",
            mode=RunMode.DEFAULT,
            hyvis_version="test",
            candidate_files=[(fi.file_hash, fi.mime)],
        )

        last_active = {h_anim: "model_2"}
        meta_by_id: dict[str, tuple[str, str, str]] = {}

        async def _run() -> None:
            s1 = await infer_files(
                cfg.inference.models[0],
                [fi],
                config=cfg,
                db=db,
                cache_db=cdb,
                progress=Progress(total=1),
                force=False,
                last_active_model_for_file=last_active,
                model_meta_by_id=meta_by_id,
            )
            assert s1.ok == 1 and s1.errors == 0
            assert fc.validate_frames(h_anim, cfg.video.prep_hash()) is True

            s2 = await infer_files(
                cfg.inference.models[1],
                [fi],
                config=cfg,
                db=db,
                cache_db=cdb,
                progress=Progress(total=1),
                force=False,
                last_active_model_for_file=last_active,
                model_meta_by_id=meta_by_id,
            )
            assert s2.refiltered == 1 and s2.ok == 0
            assert not (fc_dir / h_anim).exists()

        asyncio.run(_run())


def test_decode_error_classification_media_vs_missing_dep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    1. >10 consecutive corrupt video files must NOT trip MAX_CONSECUTIVE_ERRORS; valid file at end still runs.
    2. ImportError (missing PyAV) must abort immediately without marking files as FileStatus.ERROR.
    """
    import hyvis.video.extractor as extractor_mod

    valid_anim = tmp_path / "valid.webp"
    _write_animated_webp(valid_anim, count=4)

    bad_files = [
        FileInfo(f"{i:064x}", "video/mp4", 100, str(tmp_path / f"bad_{i}.mp4"), num_frames=10) for i in range(12)
    ]
    for bf in bad_files:
        assert bf.local_path is not None
        Path(bf.local_path).write_bytes(b"not a valid mp4")

    good_hash = "f" * 64
    good_fi = FileInfo(good_hash, "image/webp", 200, str(valid_anim), num_frames=4)
    all_files = [*bad_files, good_fi]

    cfg = _make_video_config(video_enabled=True, db_path=str(tmp_path / "hyvis.db"))
    cat = LabelCatalog(labels=(LabelInfo(index=0, name="1girl", category="general"),), categories=("general",))

    monkeypatch.setattr(
        vibe,
        "load",
        lambda model_id, **kwargs: _FakeSession(
            model_id,
            cat,
            lambda _v, _r: TagResult.from_arrays(
                tag_names=cat.names,
                scores=np.array([0.85], dtype=np.float32),
                category_indices={"general": (0,)},
            ),
        ),
    )

    with Database(tmp_path / "hyvis1.db") as db, CacheDatabase(tmp_path / "cache1.db") as cdb:
        stats1 = asyncio.run(
            infer_files(
                cfg.inference.models[0],
                all_files,
                config=cfg,
                db=db,
                cache_db=cdb,
                progress=Progress(total=len(all_files)),
                force=False,
            )
        )
        assert stats1.aborted_early is False
        assert stats1.errors == 12
        assert stats1.ok == 1

    monkeypatch.setattr(extractor_mod, "_import_av", MagicMock(side_effect=ImportError("No module named 'av'")))
    with Database(tmp_path / "hyvis2.db") as db, CacheDatabase(tmp_path / "cache2.db") as cdb:
        stats2 = asyncio.run(
            infer_files(
                cfg.inference.models[0],
                [bad_files[0]],
                config=cfg,
                db=db,
                cache_db=cdb,
                progress=Progress(total=1),
                force=False,
            )
        )
        assert stats2.aborted_early is True
        assert "PyAV" in stats2.abort_reason or "av" in stats2.abort_reason
        assert stats2.errors == 0
        row = db.conn.execute(
            "SELECT status FROM known_files WHERE file_hash = ?", (bad_files[0].file_hash,)
        ).fetchone()
        assert row is None


# endregion


def test_adjust_threshold_modes_and_clamping() -> None:
    from hyvis.config import adjust_threshold

    # Zero adjustment is an exact no-op across all modes
    for mode in ("headroom", "additive", "multiplier"):
        assert adjust_threshold(0.40, mode, 0.0) == pytest.approx(0.40)

    # Headroom: lifts low/mid cleanly without prematurely clamping 0.85 to 1.0
    assert adjust_threshold(0.10, "headroom", 0.20) == pytest.approx(0.28)
    assert adjust_threshold(0.40, "headroom", 0.20) == pytest.approx(0.52)
    assert adjust_threshold(0.85, "headroom", 0.20) == pytest.approx(0.88)
    assert adjust_threshold(0.40, "headroom", -0.25) == pytest.approx(0.30)

    # Additive: flat shift clamped to [0.0, 1.0]
    assert adjust_threshold(0.40, "additive", 0.10) == pytest.approx(0.50)
    assert adjust_threshold(0.95, "additive", 0.10) == pytest.approx(1.0)
    assert adjust_threshold(0.05, "additive", -0.10) == pytest.approx(0.0)

    # Multiplier: proportional shift clamped to [0.0, 1.0]
    assert adjust_threshold(0.40, "multiplier", 0.25) == pytest.approx(0.50)
    assert adjust_threshold(0.85, "multiplier", 0.25) == pytest.approx(1.0)
    assert adjust_threshold(0.40, "multiplier", -0.50) == pytest.approx(0.20)


def test_video_threshold_adjust_triggers_cpu_refilter_without_invalidating_static(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Raising video.threshold_adjust_value must:
      1. Leave static images untouched in skip_hashes.
      2. Keep video_prep_hash unchanged so cached videos route to Pass A (CPU re-filter).
      3. Filter out borderline video tags on CPU without modifying raw cache.db predictions.
    """
    static_path = tmp_path / "static.png"
    Image.new("RGB", (32, 32), (10, 20, 30)).save(static_path)

    anim_path = tmp_path / "anim.webp"
    _write_animated_webp(anim_path, count=4)

    h_static = "1" * 64
    h_anim = "2" * 64
    files = [
        FileInfo(h_static, "image/png", 100, str(static_path), num_frames=1),
        FileInfo(h_anim, "image/webp", 200, str(anim_path), num_frames=4),
    ]

    cat = LabelCatalog(
        labels=(LabelInfo(index=0, name="borderline_tag", category="general"),),
        categories=("general",),
    )

    monkeypatch.setattr(
        vibe,
        "load",
        lambda model_id, **_kw: _FakeSession(
            model_id,
            cat,
            lambda _v, _r: TagResult.from_arrays(
                tag_names=cat.names,
                scores=np.array([0.45], dtype=np.float32),
                category_indices={"general": (0,)},
            ),
        ),
    )

    base_cfg = AppConfig.model_validate(
        {
            "hydrus": {
                "api_url": "http://127.0.0.1:45869",
                "api_key": "k",
                "tag_queries": [{"tags": ["a"]}],
                "output_tag_services": {"keys": ["svc_a"]},
            },
            "inference": {"models": [{"model_id": "m1"}]},
            "output_filter": {"default_threshold": 0.40, "prefer_tag_level_thresholds": False},
            "video": {"enabled": True, "threshold_adjust_mode": "headroom", "threshold_adjust_value": 0.0},
        }
    )

    db_path = tmp_path / "hyvis.db"
    cache_path = tmp_path / "cache.db"

    with Database(db_path) as db, CacheDatabase(cache_path) as cdb:
        # 1. Baseline run (k=0.0): both static and anim pass 0.45 >= 0.40
        s1 = asyncio.run(
            infer_files(
                base_cfg.inference.models[0],
                files,
                config=base_cfg,
                db=db,
                cache_db=cdb,
                progress=Progress(total=2),
                force=False,
            )
        )
        assert s1.ok == 2 and s1.zero_tags == 0

        # 2. Stricter video threshold (k=0.20 -> 0.40 becomes 0.52):
        #    static skips; anim routes to CPU refilter (Pass A) and emits 0 tags (0.45 < 0.52)
        strict_cfg = base_cfg.model_copy(
            update={
                "video": base_cfg.video.model_copy(
                    update={"threshold_adjust_mode": "headroom", "threshold_adjust_value": 0.20}
                )
            }
        )
        plan_strict = plan_model_pass(
            strict_cfg.inference.models[0],
            files,
            config=strict_cfg,
            db=db,
            cache_db=cdb,
            force=False,
        )
        assert plan_strict.skip_hashes == {h_static}
        assert [f.file_hash for f in plan_strict.refilter_files] == [h_anim]
        assert plan_strict.files_to_infer == []

        s2 = asyncio.run(
            infer_files(
                strict_cfg.inference.models[0],
                files,
                config=strict_cfg,
                db=db,
                cache_db=cdb,
                progress=Progress(total=2),
                force=False,
                plan=plan_strict,
            )
        )
        assert s2.skipped == 1 and s2.refiltered == 1 and s2.zero_tags == 1

        # 3. Revert back to k=0.0: raw 0.45 is still intact in cache.db -> anim re-emits via CPU!
        s3 = asyncio.run(
            infer_files(
                base_cfg.inference.models[0],
                files,
                config=base_cfg,
                db=db,
                cache_db=cdb,
                progress=Progress(total=2),
                force=False,
            )
        )
        assert s3.skipped == 1 and s3.refiltered == 1 and s3.zero_tags == 0


def test_video_config_effort_validation_per_format() -> None:
    from pydantic import ValidationError

    from hyvis.config import VideoConfig

    # WebP accepts 0..6, rejects 7
    assert VideoConfig(frame_cache_format="webp", frame_cache_effort=6).frame_cache_effort == 6
    with pytest.raises(ValidationError, match="frame_cache_effort"):
        VideoConfig(frame_cache_format="webp", frame_cache_effort=7)

    # PNG accepts 0..9
    assert VideoConfig(frame_cache_format="png", frame_cache_effort=0).frame_cache_effort == 0
    assert VideoConfig(frame_cache_format="png", frame_cache_effort=9).frame_cache_effort == 9

    # JXL accepts 1..9, rejects 0
    assert VideoConfig(frame_cache_format="jxl", frame_cache_effort=1).frame_cache_effort == 1
    with pytest.raises(ValidationError, match="frame_cache_effort"):
        VideoConfig(frame_cache_format="jxl_lossless", frame_cache_effort=0)

    # JPEG ignores effort
    assert VideoConfig(frame_cache_format="jpeg", frame_cache_effort=9).frame_cache_effort == 9


@pytest.mark.parametrize(
    ("fmt", "quality", "effort", "expected_ext"),
    [
        ("jpeg", 95, 3, ".jpg"),
        ("webp", 85, 2, ".webp"),
        ("webp_lossless", 50, 1, ".webp"),
        ("png", 50, 2, ".png"),
        ("jxl", 90, 2, ".jxl"),
        ("jxl_lossless", 50, 2, ".jxl"),
    ],
)
def test_frame_disk_cache_formats_and_invalidation(
    tmp_path: Path,
    fmt: str,
    quality: int,
    effort: int,
    expected_ext: str,
) -> None:
    if fmt.startswith("jxl"):
        pytest.importorskip("pillow_jxl")

    fh = "a" * 64
    frames = [
        ExtractedFrame(index=0, source_index=0, timestamp_s=0.0, image=Image.new("RGB", (16, 16), (240, 20, 20))),
        ExtractedFrame(index=1, source_index=4, timestamp_s=0.4, image=Image.new("RGB", (16, 16), (20, 240, 20))),
    ]

    fc = FrameDiskCache(tmp_path / "fc", image_format=fmt, quality=quality, effort=effort)
    list(fc.save_and_yield_frames(fh, "prep_1", iter(frames)))

    assert (tmp_path / "fc" / fh / f"0000{expected_ext}").is_file()
    assert fc.validate_frames(fh, "prep_1") is True

    loaded = list(fc.load_frames(fh, "prep_1"))
    assert len(loaded) == 2
    assert loaded[0].image.getpixel((8, 8))[0] > 200  # ty:ignore[not-subscriptable]

    # Changing quality on lossless formats must NOT invalidate; changing quality on lossy formats MUST invalidate
    other_q_fc = FrameDiskCache(tmp_path / "fc", image_format=fmt, quality=70, effort=effort)
    is_lossless = fmt in ("webp_lossless", "png", "jxl_lossless")
    assert other_q_fc.has_complete_frames(fh, "prep_1") is is_lossless

    # Changing effort on formats that use effort MUST invalidate (JPEG ignores effort)
    other_e_fc = FrameDiskCache(tmp_path / "fc", image_format=fmt, quality=quality, effort=5)
    assert other_e_fc.has_complete_frames(fh, "prep_1") is (fmt == "jpeg")


def test_video_config_prep_hash_keyframe_compatibility() -> None:
    """extract_keyframes=False must keep the exact legacy prep_hash; enabling it must invalidate."""
    from hyvis.config import VideoConfig

    base = VideoConfig(enabled=True, density_k=2.0, min_frames=4, max_frames=128, sparse_below_frames=32)
    off_diff_max_kf = VideoConfig(
        enabled=True,
        density_k=2.0,
        min_frames=4,
        max_frames=128,
        sparse_below_frames=32,
        extract_keyframes=False,
        max_keyframes=64,
    )
    on_32 = VideoConfig(enabled=True, extract_keyframes=True, max_keyframes=32)
    on_64 = VideoConfig(enabled=True, extract_keyframes=True, max_keyframes=64)

    assert base.prep_hash() == off_diff_max_kf.prep_hash()
    assert on_32.prep_hash() != base.prep_hash()
    assert on_64.prep_hash() != on_32.prep_hash()
