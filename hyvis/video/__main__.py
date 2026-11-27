"""
__main__.py — Standalone CLI inspection and frame-encoding benchmark tool for HyVis video/animation extraction.

Usage:
    python -m hyvis.video <media_path> [options]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import get_args

from hyvis.config import FRAME_CACHE_EFFORT_RANGES, LOSSLESS_CACHE_FORMATS, FrameCacheFormat
from hyvis.video.extractor import (
    ExtractionStrategy,
    _ensure_pillow_plugins,
    build_video_plan,
    extract_frames,
    probe_media,
    select_strategy,
)
from hyvis.video.frame_cache import get_format_extension_and_save_kwargs
from hyvis.video.planner import FramePlanConfig

_FORMAT_CHOICES = get_args(FrameCacheFormat)


def _format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.2f} MB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hyvis.video",
        description="Inspect metadata, sampling plan, extracted frames, and cache compression on a video or animated image.",
    )
    parser.add_argument("path", type=Path, help="Path to video or animated image file.")
    parser.add_argument("--k", type=float, default=2.0, help="Density multiplier k (default: 2.0).")
    parser.add_argument("--min-frames", type=int, default=4, help="Minimum sampled frames (default: 4).")
    parser.add_argument("--max-frames", type=int, default=128, help="Maximum sampled frames (default: 128).")
    parser.add_argument("--sparse-below", type=int, default=32, help="Sparse threshold in frames (default: 32).")
    parser.add_argument(
        "--keyframes",
        action="store_true",
        help="Also extract video keyframes (up to --max-keyframes) alongside uniform sampling.",
    )
    parser.add_argument(
        "--max-keyframes",
        type=int,
        default=32,
        help="Maximum keyframes extracted per video when --keyframes is set (default: 32).",
    )
    parser.add_argument(
        "--strategy",
        choices=("auto", "sequential", "seek"),
        default="auto",
        help="Override PyAV video decode strategy (default: auto).",
    )
    parser.add_argument("--out-dir", type=Path, default=None, help="Optional directory to save extracted frames.")
    parser.add_argument(
        "--format",
        choices=_FORMAT_CHOICES,
        default="jpeg",
        help="Output image format when --out-dir is set (default: jpeg).",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=95,
        help="Lossy encoding quality 1-100 when saving frames (default: 95).",
    )
    parser.add_argument(
        "--effort",
        type=int,
        default=3,
        help="Compression effort for PNG (0-9), JXL (1-9), WebP (0-6); ignored for JPEG (default: 3).",
    )
    parser.add_argument("--dry-plan", action="store_true", help="Only probe and print plan without decoding pixels.")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Print full timestamps/indices and per-frame details."
    )
    args = parser.parse_args(argv)

    if not args.path.is_file():
        print(f"Error: File not found: {args.path}", file=sys.stderr)
        return 1

    if args.min_frames > args.max_frames:
        parser.error(f"--min-frames ({args.min_frames}) cannot exceed --max-frames ({args.max_frames}).")
    if args.max_keyframes < 1:
        parser.error(f"--max-keyframes must be >= 1, got {args.max_keyframes}.")
    if not (1 <= args.quality <= 100):
        parser.error(f"--quality must be between 1 and 100, got {args.quality}.")

    effort_bounds = FRAME_CACHE_EFFORT_RANGES.get(args.format)
    if effort_bounds is not None:
        lo, hi = effort_bounds
        if not (lo <= args.effort <= hi):
            parser.error(f"--effort for '{args.format}' must be between {lo} and {hi}, got {args.effort}.")

    try:
        cfg = FramePlanConfig(
            density_k=args.k,
            min_frames=args.min_frames,
            max_frames=args.max_frames,
            sparse_below_frames=args.sparse_below,
        )
    except ValueError as exc:
        parser.error(str(exc))

    probe = probe_media(args.path)
    plan = build_video_plan(
        probe,
        config=cfg,
        extract_keyframes=args.keyframes,
        max_keyframes=args.max_keyframes,
    )

    strategy_override: ExtractionStrategy | None = None
    if args.strategy != "auto":
        if probe.kind == "video":
            if args.strategy == "seek" and not plan.times_s:
                print(
                    "Warning: video has no duration metadata; cannot use seek strategy, using sequential.",
                    file=sys.stderr,
                )
            else:
                strategy_override = "pyav_seek" if args.strategy == "seek" else "pyav_sequential"
        else:
            print(
                f"Note: --strategy {args.strategy} only applies to video containers (ignored for {probe.kind}).",
                file=sys.stderr,
            )

    effective_strategy = strategy_override or select_strategy(probe, plan)

    dur_str = f"{probe.duration_s:.3f}s" if probe.duration_s is not None else "unknown"
    fps_str = f"{probe.fps:.2f}" if probe.fps is not None else "unknown"
    frames_str = str(probe.total_frames) if probe.total_frames is not None else "unknown"

    print(f"File         : {probe.path}")
    print(f"Kind         : {probe.kind} ({probe.format_name}{f' / {probe.codec}' if probe.codec else ''})")
    print(f"Dimensions   : {probe.width}x{probe.height} (rotation={probe.rotation}°)")
    print(f"Stream Info  : duration={dur_str} | frames={frames_str} | fps={fps_str}")
    kf_info = f" (+{plan.keyframe_count} keyframes)" if args.keyframes else ""
    print(f"Plan         : mode={plan.mode} | target_count={plan.target_count}{kf_info} | strategy={effective_strategy}")

    if plan.times_s:
        shown_t = plan.times_s if args.verbose else plan.times_s[:10]
        preview_t = ", ".join(f"{t:.2f}s" for t in shown_t)
        suffix = f" ... (+{len(plan.times_s) - 10} more)" if not args.verbose and len(plan.times_s) > 10 else ""
        print(f"Timestamps   : [{preview_t}{suffix}]")
    if plan.indices:
        shown_i = plan.indices if args.verbose else plan.indices[:10]
        preview_i = ", ".join(str(i) for i in shown_i)
        suffix = f" ... (+{len(plan.indices) - 10} more)" if not args.verbose and len(plan.indices) > 10 else ""
        print(f"Indices      : [{preview_i}{suffix}]")

    if args.dry_plan:
        return 0

    result = extract_frames(
        args.path,
        config=cfg,
        strategy=strategy_override,
        extract_keyframes=args.keyframes,
        max_keyframes=args.max_keyframes,
    )
    print(f"\nExtracted    : {len(result.frames)} frame(s) in {result.elapsed_s * 1000:.1f} ms")

    if args.verbose:
        for f in result.frames:
            t_str = f"{f.timestamp_s:.3f}s" if f.timestamp_s is not None else "-"
            idx_str = str(f.source_index) if f.source_index is not None else "-"
            w, h = f.image.size
            print(f"  [{f.index:03d}] src_frame={idx_str:<6} t={t_str:<8} {w}x{h}")

    if args.out_dir is not None:
        _ensure_pillow_plugins()
        ext, save_kwargs = get_format_extension_and_save_kwargs(
            args.format,
            quality=args.quality,
            effort=args.effort,
        )
        args.out_dir.mkdir(parents=True, exist_ok=True)

        t_save0 = time.perf_counter()
        total_bytes = 0
        for f in result.frames:
            t_part = f"_t{f.timestamp_s:.2f}s" if f.timestamp_s is not None else ""
            idx_part = f"_f{f.source_index}" if f.source_index is not None else ""
            out_path = args.out_dir / f"{args.path.stem}_{f.index:03d}{idx_part}{t_part}{ext}"
            rgb = f.image.convert("RGB") if f.image.mode != "RGB" else f.image
            rgb.save(out_path, **save_kwargs)
            total_bytes += out_path.stat().st_size
        save_ms = (time.perf_counter() - t_save0) * 1000.0

        fmt_parts = [args.format]
        if args.format not in LOSSLESS_CACHE_FORMATS:
            fmt_parts.append(f"q={args.quality}")
        if effort_bounds is not None:
            fmt_parts.append(f"effort={args.effort}")
        avg_bytes = total_bytes // max(len(result.frames), 1)
        print(
            f"Saved frames : {args.out_dir.resolve()} "
            f"({len(result.frames)} files, {_format_size(total_bytes)} total, "
            f"{_format_size(avg_bytes)}/frame in {save_ms:.1f} ms | {', '.join(fmt_parts)})"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
