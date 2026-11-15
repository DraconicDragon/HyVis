# HyVis — Hydrus Tagger

Vibecoded CLI utility that tags files in a [Hydrus](https://hydrusnetwork.github.io/hydrus/) client using image/video-tagging models (PyTorch/ONNX) via the `vibe` library. Videos are tagged by sampling frames and merging per-frame results. Reads files directly from disk, so it must run on the same machine/storage as the Hydrus client. Config is TOML; user docs live in `README.md`, `CONFIGURATION.md`, `SUPPORTED_MODELS.md`.

## Project

- Python ≥3.11 (dev venv uses 3.12), async (`asyncio`), Pydantic v2 config, SQLite cache, no web framework.
- Package `hyvis/`, versioned in `pyproject.toml` (currently 0.43.0). Hatchling build.
- Dependencies include a git dep: `vibe @ git+https://github.com/DraconicDragon/vibe@<pin>` — installs need network. Also `opencv-python-headless` (video frame extraction).
- Entry points (`pyproject.toml [project.scripts]`): `hyvis` → `hyvis.main:cli`, `hyvis-gui` → `hyvis.gui.app:cli` (PySide6 Qt GUI, install with `pip install ".[gui]"`).
- Repo root also has `reference.py` and `myconfig.toml` (untracked user scratch). `myconfig.toml` is the user's manual-testing config: mirror config changes (e.g. new/renamed/removed fields, comments) into `config_examples/config.example.toml` AND `myconfig.toml`; don't touch its unrelated content. Leave `reference.py` alone.

## Commands

- Install: `pip install .` (GUI: `pip install ".[gui]"`; editable dev: `pip install -e .`) — optional inference backends per README (`torch`/`safetensors`/`timm`/`transformers`/`einops` or `onnxruntime`). The dev venv is installed NON-editable: after editing repo code, run `pip install .` (as `update_linux.sh` does) or set `PYTHONPATH` to the repo for the changes to take effect.
- Run: `hyvis path/to/config.toml` (dev: `.venv/bin/hyvis path/to/config.toml`).
- Secondary tool: `hyvis config.toml --push-only` — push results cached by a previous run (e.g. after `--infer-only`).
- Update scripts: `update_linux.sh` / `update_windows.bat` (git pull + pip install).
- Tests live in `tests/` (`test_db.py`, `test_filters.py`, `test_gui_parity.py`) but `pytest` and `PySide6` are NOT installed in the dev venv, so GUI tests cannot run here; there is no linter/formatter config (don't claim `ruff`). Validate by importing: `.venv/bin/python -c "import hyvis"` and running `hyvis --version`.
- Example configs: `config_examples/*.toml` (mirror config changes in `config.example.toml`).

## Architecture

- `main.py` — async orchestrator: parse args → load/validate config → collect candidates + MIME filter (`preflight.py`) → resolve paths → preview pages → per-model inference → trailing push & cleanup → summary.
- `config.py` — Pydantic v2 models + TOML loading (`AppConfig.from_file`), `hyvis_validate()` for cross-field checks, friendly error formatting.
- `hydrus.py` — Hydrus Client API wrapper: queries, metadata, path resolution, preview pages, tag push; raises `HydrusError` / `HydrusConnectionError`.
- `inference.py` — per-model pipeline via `vibe` (`infer_files`), results written to DB; returns `PhaseStats`. Inputs are a `dict[file_hash, path|bytes]` stream (`vibe.load` session → `infer_async` → vibe-result-transforms pipeline → `extract_tags` → `db.enqueue_push`). Files are split into images and videos; both paths share the inner `_persist_result` helper. Video phase: `extract_frames` per file → `session.infer_async(list(frames))` → per-frame `TagResult.as_category_score_dict()` → `merge_frame_results` (union per category, max score per tag) → rebuilt `TagResult` → same pipeline/extraction. Batch size halves on OOM (image stream restarts only if nothing was processed yet; video frames retry per video) via `_is_memory_error`.
- `db.py` — SQLite cache (runs, file info, per-model inference results, pending pushes) with `with Database(...)` context manager.
- `push_service.py` — engine for pushing cached results to Hydrus + tag cleanup (`run_push_and_cleanup`).
- `video.py` — frame sampling for videos and animated GIFs. `extract_frames` returns a tuple of PIL frames. Scan-only: a probe grid of `probe_multiplier × max_frames` candidates (capped at `max_probes`) is drawn across the content window (`_content_window`: fade padding 0.2–3.0 s scaled by duration, frame-time aligned), decoded in ascending order through `_VideoFrameReader` (forward `cap.read()`; seek only when the gap exceeds 120 frames, because a random seek on WebM re-decodes from the start), then filtered by `_select_frames`: fade drop (`_fade_stats_test`), flat-dark drop, near-duplicate drop (`_frames_match`/`_is_near_duplicate`: both a low mean diff AND fewer than `max_changed_fraction` of pixels changed by more than `change_level` gray levels), keeping up to `_keep_budget(...)` frames, one per visually distinct phase, with a `brightest` fallback so a fully filtered clip still yields one frame. `[video.loop]` holds the loop rules: `single_frame_below_seconds` returns one centered frame without probing, `edges_max_seconds` drops edges by duration alone, and `detect` compares the first and last probe (`_detect_loop`, loads the last one first, 10 % seam tolerance) and skips both edges. `dump_frames()` writes the exact sampled frames to `frames/<hash>/` when `write_frames = true`. `extract_gif_frames` replays the same grid on the GIF/APNG timeline using each frame's display duration (`_gif_durations` + `bisect` index lookup), Pillow only. If OpenCV opens a video but decodes no frames (AV1 builds with no software path), `_sample_video_ffmpeg` retries the same grid through the `ffmpeg` CLI one frame per probe (`_ffmpeg_frame`), so the file is sampled instead of skipped. `[video.fades]`, `[video.low_interest]` and `[video.dedupe]` are the filter tables; a dropped probe is not replaced, the grid supplies alternatives. `ALLOWED_MIMES` includes `image/gif`, `image/apng` and `image/vnd.adobe.photoshop` (PSD is tagged from its saved flattened composite). An adaptive sampler previously existed and lives on the `video-adaptive` branch.
- `preflight.py` — candidate collection (tag/page queries, `--extra-hash-file`) + MIME filter (`filter_by_mime(..., allow_videos=cfg.video.enabled)`), preview pages.
- `gui/` — PySide6 desktop UI: `main_window.py` (sidebar pages + `_gather_config_dict`), `pages/*` (one `BaseConfigPage` per config section), `parity.py` (`audit_gui_parity`: the GUI must emit every `AppConfig` field; `test_gui_parity.py` asserts it). New config sections are emitted automatically by `state.config.model_dump()` but are only editable if a page exposes widgets for them.
- Supporting: `cli.py` (argparse), `cli_display.py` (confirmation screen / run summary), `bg_imports.py` (background preload of heavy libs: PIL, vibe, numpy, torch/onnx), `progress.py` (inline progress), `logging_utils.py` (ANSI colors, `setup_logging`), `extra_hashes.py` (hash-file ingestion), `db_migrations.py`, `dry_run.py`, `system_info.py`.

## Conventions

- Module docstring at top of each file: `"name.py — one-line purpose."`
- `from __future__ import annotations`; modern typing: `X | None`, `Path | str`.
- `# region` / `# endregion` section markers; lowercase `# todo:` comments.
- `logger = logging.getLogger(__name__)` per module; use `_c(text, *colors)` from `logging_utils` for user-facing colored output, `RED` for errors.
- Heavy imports (torch, onnxruntime, PIL, vibe, numpy, cv2) are lazy/background-loaded — don't top-level import them in hot paths.
- `session.infer_async()` / `session.infer()` input batches must be a `list`. A bare `tuple` is parsed by vibe as a single `(image, ref)` pair → "Tuple input at index 0 must be exactly (image_or_path, ref)." (e.g. `extract_frames` returns a tuple — convert with `list(frames)` at the call site).
- Config models are frozen (strict) Pydantic `BaseModel`s; errors surface as validation messages, not tracebacks.
- Commit style: conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`).

## Notes

- `tests/` exists upstream but `pytest`/`PySide6` are not installed in the dev venv. Verification pattern that works offline: monkeypatch `vibe.load` with a fake session (`infer_async` async generator yielding `FakeItem(input_ref, TagResult(...))`, `tagger.catalog.labels`, `capabilities = frozenset()`), patch `hyvis.video.extract_frames`, and run `infer_files` against a real `Database` to exercise the full pipeline without downloading models.
- `reference.py` at the repo root is the standalone reference script; only its `extract_frames` sampling strategy was ported into `hyvis/video.py` — do not port its CLI/tagging/push patterns.
