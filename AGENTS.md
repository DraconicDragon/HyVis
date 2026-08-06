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
- `video.py` — video frame extraction for video tagging. Uniform sampling (strategy ported from `reference.py`: even sampling across duration with fade padding, optional first/last frames; scaling via `max_frames`/`max_frames_at_seconds`). `extract_frames` returns PIL frames (tuple); per-frame score mappings are merged by `merge_frame_results` in `inference.py` (union, max score). Near-duplicate removal (`_dedupe_near_duplicates`, greedy first-occurrence-wins; `[video] dedupe`/`dedupe_threshold`/`dedupe_change_level`/`dedupe_max_changed_fraction`) collapses true loop repeats and static segments while keeping small-but-important changes (the dup test requires both a low mean diff AND <1% of pixels changed by >25 gray levels). `[video] write_frames = true` dumps the exact sampled frames to `frames/<hash>/` via `dump_frames()` (debug aid; default off). `[video] exclude_fades = true` replaces frames landing in fade-to-black/white transitions and dim frames adjacent to a fade (`_filter_fade_frames`; flat fade = extreme mean luminance AND low luminance std; `replacement_min_brightness` (default 0.30) is the min brightness for frames near fades — dark scenes with no fade nearby are preserved; default off). `[video.low_interest]` (enabled by default) drops sampled frames that are flat AND dark (black frames/fade-outs) before inference (`_filter_low_interest_frames`; `min_luminance` 0.15, `min_std` 0.05). An adaptive/loop-aware sampler previously existed but was removed — see the `video-adaptive` branch.
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
