"""
test_video_pipeline.py — Integration tests for video/animation routing, cache.db v1->v2 migration,
prep_hash invalidation isolation, FrameDiskCache lifecycle, and multi-model streaming inference.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
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

        frames_map = db.bulk_get_known_frames([h_anim, h_unknown])
        assert frames_map == {h_anim: 18}


# endregion


# region MIME Filtering & Prep-Hash Isolation


def test_filter_by_mime_rejects_animations_when_video_disabled() -> None:
    """When allow_video=False, videos AND multi-frame animatable images must be rejected."""
    client = HydrusClient("http://127.0.0.1:45869", "key")
    mock_api = MagicMock()
    mock_api.get_file_metadata.return_value = {
        "metadata": [
            {"hash": "1" * 64, "mime": "image/jpeg", "size": 100},
            {"hash": "2" * 64, "mime": "image/webp", "size": 200, "num_frames": 12},
            {"hash": "3" * 64, "mime": "video/mp4", "size": 300, "num_frames": 90},
            {"hash": "4" * 64, "mime": "image/webp", "size": 150, "num_frames": 1},
            {"hash": "5" * 64, "mime": "image/gif", "size": 180, "num_frames": 8},
        ]
    }
    client._client = mock_api

    acc_off, rej_mimes_off, rej_hashes_off = client.filter_by_mime(
        ["1" * 64, "2" * 64, "3" * 64, "4" * 64, "5" * 64],
        allow_video=False,
    )
    assert [fi.file_hash for fi in acc_off] == ["1" * 64, "4" * 64]
    assert set(rej_hashes_off) == {"2" * 64, "3" * 64, "5" * 64}
    assert "image/webp (animated)" in rej_mimes_off
    assert "video/mp4" in rej_mimes_off
    assert "image/gif" in rej_mimes_off

    acc_on, rej_mimes_on, rej_hashes_on = client.filter_by_mime(
        ["1" * 64, "2" * 64, "3" * 64, "4" * 64, "5" * 64],
        allow_video=True,
    )
    assert len(acc_on) == 5
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
        assert [fi.file_hash for fi in p_dens.gpu_files] == [h_video]

        # 2. Threshold changed (density same): BOTH static and video route to CPU re-filter
        p_thresh = plan_model_pass(m_cfg, files, config=cfg_v2_thresh, db=db, cache_db=cdb, force=False)
        assert p_thresh.skip_hashes == set()
        assert {fi.file_hash for fi in p_thresh.refilter_files} == {h_static, h_video}
        assert p_thresh.gpu_files == []


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
    assert loaded[0].image.getpixel((8, 8))[0] > 200
    assert loaded[1].image.getpixel((8, 8))[1] > 200

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

    class _FakeSession:
        def __init__(self, model_id: str) -> None:
            self.model_id = model_id
            self.backend = vibe.Backend.PYTORCH
            self.variant = ""
            cat = cat_1 if model_id == "model_1" else cat_2
            self.tagger = vibe.TaggerView(catalog=cat, thresholds=None, recommendation=None)

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
                items: list[InferenceResultItem] = []
                for offset, (val, ref) in enumerate(zip(batch_vals, batch_refs, strict=True)):
                    if self.model_id == "model_1":
                        if isinstance(val, Image.Image):
                            # Check center red channel: frame index >= 3 has bright red -> cameo_tag spikes!
                            r = val.getpixel((16, 16))[0]
                            cameo_score = 0.88 if r > 100 else 0.05
                            scores = np.array([0.82, cameo_score], dtype=np.float32)
                        else:
                            scores = np.array([0.75, 0.10], dtype=np.float32)
                        res = TagResult.from_arrays(
                            tag_names=cat_1.names,
                            scores=scores,
                            category_indices={"general": (0, 1)},
                        )
                    else:
                        res = TagResult.from_arrays(
                            tag_names=cat_2.names,
                            scores=np.array([0.91], dtype=np.float32),
                            category_indices={"general": (0,)},
                        )
                    items.append(InferenceResultItem(index=start_idx + offset, result=res, input_ref=ref))
                yield InferenceResult(items=items)

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            pass

    monkeypatch.setattr(vibe, "load", lambda model_id, **kwargs: _FakeSession(model_id))

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


# endregion
