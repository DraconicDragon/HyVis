"""
__main__.py — Standalone CLI inspection tool for HyVis video/animation frame extraction.

Usage:
    python -m hyvis.video <media_path> [--k 2.0] [--min-frames 4] [--max-frames 128] [--out-dir ./debug_frames]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hyvis.video.extractor import extract_frames, probe_media, select_strategy
from hyvis.video.planner import FramePlanConfig, plan_frames


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hyvis.video",
        description="Inspect metadata, sampling plan, and extracted frames for a video or animated image.",
    )
    parser.add_argument("path", type=Path, help="Path to video or animated image file.")
    parser.add_argument("--k", type=float, default=2.0, help="Density multiplier k (default: 2.0).")
    parser.add_argument("--min-frames", type=int, default=4, help="Minimum sampled frames (default: 4).")
    parser.add_argument("--max-frames", type=int, default=128, help="Maximum sampled frames (default: 128).")
    parser.add_argument("--sparse-below", type=int, default=32, help="Sparse threshold in frames (default: 32).")
    parser.add_argument("--out-dir", type=Path, default=None, help="Optional directory to save extracted frames.")
    parser.add_argument("--dry-plan", action="store_true", help="Only probe and print plan without decoding pixels.")
    args = parser.parse_args(argv)

    if not args.path.is_file():
        print(f"Error: File not found: {args.path}", file=sys.stderr)
        return 1

    cfg = FramePlanConfig(
        density_k=args.k,
        min_frames=args.min_frames,
        max_frames=args.max_frames,
        sparse_below_frames=args.sparse_below,
    )

    probe = probe_media(args.path)
    plan = plan_frames(probe.duration_s, probe.total_frames, config=cfg)
    strategy = select_strategy(probe, plan)

    dur_str = f"{probe.duration_s:.3f}s" if probe.duration_s is not None else "unknown"
    fps_str = f"{probe.fps:.2f}" if probe.fps is not None else "unknown"
    frames_str = str(probe.total_frames) if probe.total_frames is not None else "unknown"

    print(f"File         : {probe.path}")
    print(f"Kind         : {probe.kind} ({probe.format_name}{f' / {probe.codec}' if probe.codec else ''})")
    print(f"Dimensions   : {probe.width}x{probe.height} (rotation={probe.rotation}°)")
    print(f"Stream Info  : duration={dur_str} | frames={frames_str} | fps={fps_str}")
    print(f"Plan         : mode={plan.mode} | target_count={plan.target_count} | strategy={strategy}")

    if plan.times_s:
        preview_t = ", ".join(f"{t:.2f}s" for t in plan.times_s[:10])
        suffix = f" ... (+{len(plan.times_s) - 10} more)" if len(plan.times_s) > 10 else ""
        print(f"Timestamps   : [{preview_t}{suffix}]")
    if plan.indices:
        preview_i = ", ".join(str(i) for i in plan.indices[:10])
        suffix = f" ... (+{len(plan.indices) - 10} more)" if len(plan.indices) > 10 else ""
        print(f"Indices      : [{preview_i}{suffix}]")

    if args.dry_plan:
        return 0

    result = extract_frames(args.path, config=cfg)
    print(f"\nExtracted    : {len(result.frames)} frame(s) in {result.elapsed_s * 1000:.1f} ms")

    if args.out_dir is not None:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for f in result.frames:
            t_part = f"_t{f.timestamp_s:.2f}s" if f.timestamp_s is not None else ""
            idx_part = f"_f{f.source_index}" if f.source_index is not None else ""
            out_path = args.out_dir / f"{args.path.stem}_{f.index:03d}{idx_part}{t_part}.jpg"
            f.image.save(out_path, quality=97)
        print(f"Saved frames : {args.out_dir.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
