"""
test_pipeline_routing.py — Tests for filter_hash, 3-way routing, CPU cache re-filtering,
multi-model staging with only_if_tags_emitted, and CLI --resume argument validation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest
from vibe.metadata import LabelCatalog, LabelInfo

from hyvis.cli import parse_args
from hyvis.config import AppConfig
from hyvis.db import CacheDatabase, Database, FileStatus, RunMode
from hyvis.hydrus import FileInfo
from hyvis.inference import compute_filter_hash, infer_files, plan_model_pass
from hyvis.progress import Progress


def _make_config(
    *,
    default_threshold: float = 0.40,
    prefer_tlt: bool = True,
    models: list[dict] | None = None,
    add_tags: list[dict] | None = None,
    remove_tags: list[dict] | None = None,
    min_cache_score: float = 0.005,
) -> AppConfig:
    return AppConfig.model_validate(
        {
            "hydrus": {
                "api_url": "http://127.0.0.1:45869",
                "api_key": "test_key",
                "tag_queries": [{"tags": ["system:limit is 10"], "tag_service_keys": []}],
                "output_tag_services": {"keys": ["svc_main"]},
                "add_tags": add_tags or [],
                "remove_tags": remove_tags or [],
            },
            "inference": {
                "models": models or [{"model_id": "wd-swinv2-v3"}],
            },
            "output_filter": {
                "default_threshold": default_threshold,
                "prefer_tag_level_thresholds": prefer_tlt,
            },
            "database": {
                "min_cache_score": min_cache_score,
            },
        }
    )


def test_filter_hash_sensitivity() -> None:
    """filter_hash changes on tag-affecting settings, but ignores query/device/batch changes."""
    cfg_a = _make_config(default_threshold=0.40)
    cfg_b = _make_config(default_threshold=0.45)

    # Same filter/services, different batch_size/device/query
    cfg_same = AppConfig.model_validate(
        {
            **cfg_a.model_dump(mode="json"),
            "hydrus": {
                **cfg_a.hydrus.model_dump(mode="json"),
                "tag_queries": [{"tags": ["different_query"], "tag_service_keys": []}],
            },
            "inference": {
                "models": [{"model_id": "wd-swinv2-v3", "device": "cpu", "batch_size": 8}],
            },
        }
    )

    h_a = compute_filter_hash(cfg_a, cfg_a.inference.models[0])
    h_b = compute_filter_hash(cfg_b, cfg_b.inference.models[0])
    h_same = compute_filter_hash(cfg_same, cfg_same.inference.models[0])

    assert h_a == h_same
    assert h_a != h_b


def test_plan_model_pass_3way_routing(tmp_path: Path) -> None:
    """Verify Skip vs CPU Re-filter vs GPU fallback routing based on filter_hash and floor_q."""
    db_path = tmp_path / "hyvis.db"
    cache_path = tmp_path / "cache.db"

    h_skip = "1" * 64
    h_cpu = "2" * 64
    h_shallow = "3" * 64
    h_missing = "4" * 64

    files = [
        FileInfo(h_skip, "image/png", 100, "/1.png"),
        FileInfo(h_cpu, "image/png", 100, "/2.png"),
        FileInfo(h_shallow, "image/png", 100, "/3.png"),
        FileInfo(h_missing, "image/png", 100, "/4.png"),
    ]

    cfg_old = _make_config(default_threshold=0.40, min_cache_score=0.005)
    cfg_new = _make_config(default_threshold=0.35, min_cache_score=0.005)
    m_cfg = cfg_new.inference.models[0]

    old_fh = compute_filter_hash(cfg_old, m_cfg)
    new_fh = compute_filter_hash(cfg_new, m_cfg)

    cat = LabelCatalog(
        labels=(LabelInfo(index=0, name="1girl", category="general"),),
        categories=("general",),
    )

    with Database(db_path) as db, CacheDatabase(cache_path) as cdb:
        for fi in files:
            db.upsert_file(fi.file_hash, file_path=fi.local_path, mime=fi.mime, status=FileStatus.ACTIVE)

        # h_skip already has new_fh in inference_log
        db.record_inference(h_skip, m_cfg.model_id, filter_hash=new_fh)
        # h_cpu and h_shallow have old_fh in inference_log
        db.record_inference(h_cpu, m_cfg.model_id, filter_hash=old_fh)
        db.record_inference(h_shallow, m_cfg.model_id, filter_hash=old_fh)
        db.commit()

        # Register cache entries: h_cpu cached deeply (0.005), h_shallow cached too high (0.05)
        m_row = cdb.register_model(m_cfg.model_id, "", cat)
        cdb.save_prediction(
            m_row, h_cpu, np.array([0], dtype=np.uint32), np.array([0.8], dtype=np.float32), min_cache_score=0.005
        )
        cdb.save_prediction(
            m_row, h_shallow, np.array([0], dtype=np.uint32), np.array([0.8], dtype=np.float32), min_cache_score=0.05
        )
        cdb.commit()

        plan = plan_model_pass(m_cfg, files, config=cfg_new, db=db, cache_db=cdb, force=False)

        assert plan.skip_hashes == {h_skip}
        assert [f.file_hash for f in plan.refilter_files] == [h_cpu]
        assert [f.file_hash for f in plan.files_to_infer] == [h_shallow, h_missing]


def test_cpu_refilter_multimodel_and_only_if_tags_emitted(tmp_path: Path) -> None:
    """
    Execute a 2-model pass strictly from cache.db on CPU (no GPU/vibe.load),
    verifying calibrated TLT thresholds and conditional add_tags / remove_tags.
    """
    db_path = tmp_path / "hyvis.db"
    cache_path = tmp_path / "cache.db"

    h_with_tags = "a" * 64
    h_zero_tags = "b" * 64

    files = [
        FileInfo(h_with_tags, "image/png", 100, "/a.png"),
        FileInfo(h_zero_tags, "image/png", 100, "/b.png"),
    ]

    cfg = _make_config(
        default_threshold=0.50,
        prefer_tlt=True,
        models=[{"model_id": "model_a"}, {"model_id": "model_b"}],
        add_tags=[
            {"tags": ["ai:tagged"], "tag_service_keys": ["svc_main"], "only_if_tags_emitted": True},
            {"tags": ["ai:checked"], "tag_service_keys": ["svc_main"], "only_if_tags_emitted": False},
        ],
        remove_tags=[
            {"tags": ["queue:tagme"], "tag_service_keys": ["svc_main"], "only_if_tags_emitted": True},
        ],
    )

    cat_a = LabelCatalog(
        labels=(LabelInfo(index=0, name="1girl", category="general"),),
        categories=("general",),
    )
    cat_b = LabelCatalog(
        labels=(LabelInfo(index=0, name="special_tag", category="general"),),
        categories=("general",),
    )

    with Database(db_path) as db, CacheDatabase(cache_path) as cdb:
        # Register model_a (no TLT) and model_b (calibrated TLT = 0.20 for 'special_tag')
        row_a = cdb.register_model("model_a", "", cat_a)
        row_b = cdb.register_model("model_b", "", cat_b, calibrated_thresholds={"special_tag": 0.20})

        # h_with_tags: model_a below default (0.10), model_b passes TLT 0.20 with score 0.30!
        cdb.save_prediction(
            row_a,
            h_with_tags,
            np.array([0], dtype=np.uint32),
            np.array([0.10], dtype=np.float32),
            min_cache_score=0.005,
        )
        cdb.save_prediction(
            row_b,
            h_with_tags,
            np.array([0], dtype=np.uint32),
            np.array([0.30], dtype=np.float32),
            min_cache_score=0.005,
        )

        # h_zero_tags: both models score 0.10 (below 0.50 and below TLT 0.20)
        cdb.save_prediction(
            row_a,
            h_zero_tags,
            np.array([0], dtype=np.uint32),
            np.array([0.10], dtype=np.float32),
            min_cache_score=0.005,
        )
        cdb.save_prediction(
            row_b,
            h_zero_tags,
            np.array([0], dtype=np.uint32),
            np.array([0.10], dtype=np.float32),
            min_cache_score=0.005,
        )
        cdb.commit()

        plans = [plan_model_pass(m, files, config=cfg, db=db, cache_db=cdb, force=False) for m in cfg.inference.models]
        assert all(len(p.refilter_files) == 2 and not p.files_to_infer for p in plans)

        last_active = {fi.file_hash: "model_b" for fi in files}
        meta_by_id: dict[str, tuple[str, str, str]] = {}

        db.start_active_run(
            "[hydrus]\napi_url='http://127.0.0.1:45869'",
            mode=RunMode.DEFAULT,
            hyvis_version="test",
            candidate_files=[(fi.file_hash, fi.mime) for fi in files],
        )

        async def _run() -> None:
            for plan in plans:
                stats = await infer_files(
                    plan.model_cfg,
                    files,
                    config=cfg,
                    db=db,
                    cache_db=cdb,
                    progress=Progress(total=len(files)),
                    force=False,
                    plan=plan,
                    last_active_model_for_file=last_active,
                    model_meta_by_id=meta_by_id,
                )
                assert stats.refiltered == 2
                assert stats.ok == 0

        asyncio.run(_run())
        db.clear_active_run()

        batch = dict(db.fetch_push_batch(limit=10))
        # h_with_tags passed TLT -> gets 'special tag', conditional 'ai:tagged', unconditional 'ai:checked', and removal 'queue:tagme'
        assert set(batch[h_with_tags]["svc_main"]["add"]) == {"special tag", "ai:tagged", "ai:checked"}
        assert batch[h_with_tags]["svc_main"]["delete"] == ["queue:tagme"]

        # h_zero_tags emitted 0 model tags -> skips conditional 'ai:tagged' and 'queue:tagme', keeps unconditional 'ai:checked'
        assert batch[h_zero_tags]["svc_main"]["add"] == ["ai:checked"]
        assert "delete" not in batch[h_zero_tags]["svc_main"]


def test_cli_resume_argument_validation() -> None:
    """Verify optional CONFIG_PATH rules and incompatible flag rejections."""
    args_ok = parse_args(["--resume"])
    assert args_ok.resume is True and args_ok.config is None

    for bad_argv in (
        [],
        ["--resume", "--force"],
        ["--resume", "--discard-run"],
        ["--resume", "--dry-run"],
        ["--resume", "--push-only"],
        ["--resume", "--extra-hash-file", "hashes.txt"],
    ):
        with pytest.raises(SystemExit):
            parse_args(bad_argv)
