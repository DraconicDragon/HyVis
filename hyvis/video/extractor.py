"""
extractor.py — Video and animated image probing and frame extraction.

Uses PyAV for video containers (with rotation handling, start_time offset, and
adaptive sequential vs. keyframe-seek decoding) and Pillow for animated images
(GIF, APNG, WebP, AVIF, JXL).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageOps, UnidentifiedImageError

from hyvis.video.planner import FramePlan, FramePlanConfig, plan_frames

logger = logging.getLogger(__name__)

MediaKind = Literal["image", "animated_image", "video"]
ExtractionStrategy = Literal["static", "pillow_animated", "pyav_sequential", "pyav_seek"]

#: Switch from sequential decode to keyframe seeks only when targets are spaced out on longer videos.
_SEEK_MIN_DURATION_S = 30.0
_SEEK_MIN_INTERVAL_S = 2.5


@dataclass(frozen=True, slots=True)
class MediaProbe:
    """Header metadata extracted from a media file."""

    path: Path
    kind: MediaKind
    format_name: str
    width: int
    height: int
    duration_s: float | None
    total_frames: int | None
    fps: float | None
    rotation: int = 0
    codec: str | None = None


@dataclass(slots=True)
class ExtractedFrame:
    """One decoded RGB frame paired with its source position."""

    index: int
    source_index: int | None
    timestamp_s: float | None
    image: Image.Image


@dataclass(slots=True)
class ExtractionResult:
    """Collected output of extracting all planned frames for a media file."""

    probe: MediaProbe
    plan: FramePlan
    frames: list[ExtractedFrame]
    elapsed_s: float
    strategy: ExtractionStrategy


# region Probing & Orientation Helpers


def is_animated_image(path: str | Path) -> bool:
    """
    Fast header-only check to distinguish static images from multi-frame animations
    (e.g. APNG, animated WebP/AVIF/JXL/GIF). Returns False for video containers.
    """
    p = Path(path)
    try:
        with Image.open(p) as img:
            return bool(getattr(img, "is_animated", False)) or int(getattr(img, "n_frames", 1)) > 1
    except UnidentifiedImageError:
        return False


def _probe_pillow_image(path: Path) -> MediaProbe | None:
    """Inspect a file with Pillow. Returns None if Pillow does not recognize it."""
    try:
        with Image.open(path) as img:
            n_frames = int(getattr(img, "n_frames", 1))
            is_anim = bool(getattr(img, "is_animated", False)) or n_frames > 1
            fmt = str(img.format or path.suffix.lstrip(".") or "image").lower()
            width, height = img.size

            if not is_anim:
                return MediaProbe(
                    path=path,
                    kind="image",
                    format_name=fmt,
                    width=width,
                    height=height,
                    duration_s=None,
                    total_frames=1,
                    fps=None,
                )

            # Estimate duration without decoding hundreds of frames upfront:
            # exact sum for small animations, frame-0 estimate when larger.
            duration_s: float | None = None
            if n_frames <= 32:
                total_ms = 0.0
                valid_delays = 0
                try:
                    for idx in range(n_frames):
                        img.seek(idx)
                        delay = img.info.get("duration")
                        if isinstance(delay, (int, float)) and delay > 0:
                            total_ms += float(delay)
                            valid_delays += 1
                    if valid_delays > 0:
                        duration_s = total_ms / 1000.0
                except (EOFError, OSError):
                    pass
            else:
                delay0 = img.info.get("duration")
                if isinstance(delay0, (int, float)) and delay0 > 0:
                    duration_s = (float(delay0) * n_frames) / 1000.0

            fps = (n_frames / duration_s) if (duration_s and duration_s > 0) else None

            return MediaProbe(
                path=path,
                kind="animated_image",
                format_name=fmt,
                width=width,
                height=height,
                duration_s=duration_s,
                total_frames=n_frames,
                fps=fps,
            )
    except UnidentifiedImageError:
        return None


def _probe_video_pyav(path: Path) -> MediaProbe:
    """Inspect a video container using PyAV without decoding frames."""
    import av

    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError(f"No video stream found in '{path}'.")

        stream = container.streams.video[0]
        width = int(stream.width or 0)
        height = int(stream.height or 0)

        duration_s: float | None = None
        if stream.duration is not None and stream.time_base is not None:
            dur = float(stream.duration * stream.time_base)
            if dur > 0:
                duration_s = dur
        if duration_s is None and container.duration is not None:
            dur = float(container.duration / av.time_base)
            if dur > 0:
                duration_s = dur

        total_frames = int(stream.frames) if stream.frames and stream.frames > 0 else None

        rate = stream.average_rate or stream.guessed_rate
        fps = float(rate) if rate and float(rate) > 0 else None

        rot = 0
        raw_rot = getattr(stream, "rotation", None)
        if raw_rot is None:
            raw_rot = stream.metadata.get("rotate") if hasattr(stream, "metadata") else None
        if raw_rot is not None:
            try:
                rot = round(float(raw_rot)) % 360
            except (TypeError, ValueError):
                rot = 0

        codec_name = str(stream.codec_context.name) if stream.codec_context else None
        fmt_name = str(container.format.name) if container.format else path.suffix.lstrip(".")

        return MediaProbe(
            path=path,
            kind="video",
            format_name=fmt_name,
            width=width,
            height=height,
            duration_s=duration_s,
            total_frames=total_frames,
            fps=fps,
            rotation=rot,
            codec=codec_name,
        )


def probe_media(path: str | Path) -> MediaProbe:
    """Inspect any image, animated image, or video file and return container metadata."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Media file not found: '{p}'")

    pil_probe = _probe_pillow_image(p)
    if pil_probe is not None:
        return pil_probe

    return _probe_video_pyav(p)


def _apply_rotation(img: Image.Image, degrees_ccw: float | None) -> Image.Image:
    """Apply PyAV counter-clockwise display rotation to a PIL Image."""
    if not degrees_ccw:
        return img

    angle = round(float(degrees_ccw)) % 360
    if angle == 0:
        return img
    if angle == 90:
        return img.transpose(Image.Transpose.ROTATE_90)
    if angle == 180:
        return img.transpose(Image.Transpose.ROTATE_180)
    if angle == 270:
        return img.transpose(Image.Transpose.ROTATE_270)
    return img.rotate(angle, expand=True)


def _av_frame_to_pil(frame: Any) -> Image.Image:
    """Convert a PyAV VideoFrame to an upright RGB PIL Image."""
    img = frame.to_image()
    if img.mode != "RGB":
        img = img.convert("RGB")
    rot = getattr(frame, "rotation", 0)
    return _apply_rotation(img, rot)


# endregion


# region Extraction Backends


def _extract_pillow_frames(probe: MediaProbe, plan: FramePlan) -> Iterator[ExtractedFrame]:
    """Extract target frames from a static or animated image using Pillow."""
    with Image.open(probe.path) as img:
        if probe.kind == "image" or plan.target_count <= 1:
            upright = ImageOps.exif_transpose(img)
            if upright.mode != "RGB":
                upright = upright.convert("RGB")
            yield ExtractedFrame(index=0, source_index=0, timestamp_s=0.0, image=upright)
            return

        indices = plan.indices or (0,)
        target_set = set(indices)
        max_idx = max(indices)

        out_idx = 0
        elapsed_ms = 0.0

        for frame_idx in range(max_idx + 1):
            try:
                img.seek(frame_idx)
            except EOFError:
                logger.warning(
                    "Animated image '%s' ended early at frame %d (expected up to %d).",
                    probe.path.name,
                    frame_idx,
                    max_idx,
                )
                break

            if frame_idx in target_set:
                rgb_copy = img.convert("RGB")
                yield ExtractedFrame(
                    index=out_idx,
                    source_index=frame_idx,
                    timestamp_s=elapsed_ms / 1000.0 if elapsed_ms > 0 else (0.0 if frame_idx == 0 else None),
                    image=rgb_copy,
                )
                out_idx += 1

            delay = img.info.get("duration")
            if isinstance(delay, (int, float)) and delay > 0:
                elapsed_ms += float(delay)


def _should_use_seek_strategy(probe: MediaProbe, plan: FramePlan) -> bool:
    """Choose keyframe seeking only when targets are sparse enough over a non-trivial duration."""
    if not plan.times_s or probe.duration_s is None:
        return False
    if probe.duration_s <= _SEEK_MIN_DURATION_S or len(plan.times_s) <= 1:
        return False
    avg_interval = probe.duration_s / len(plan.times_s)
    return avg_interval >= _SEEK_MIN_INTERVAL_S


def _extract_video_sequential(probe: MediaProbe, plan: FramePlan) -> Iterator[ExtractedFrame]:
    """Decode video in a single sequential pass, picking frames matching target timestamps or indices."""
    import av

    with av.open(str(probe.path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"

        start_s: float | None = (
            float(stream.start_time * stream.time_base)
            if (stream.start_time is not None and stream.time_base is not None)
            else None
        )

        use_times = bool(plan.times_s)
        targets = plan.times_s if use_times else plan.indices
        if not targets:
            targets = (0.0,) if use_times else (0,)

        target_pos = 0
        out_idx = 0
        logged_rotation = False

        for raw_idx, frame in enumerate(container.decode(stream)):
            if not logged_rotation:
                rot = getattr(frame, "rotation", 0)
                if rot:
                    logger.debug("Applying %s° CCW rotation to frames from '%s'", rot, probe.path.name)
                logged_rotation = True

            if use_times:
                f_time = frame.time
                if f_time is None:
                    # Fallback if PTS missing: estimate from FPS
                    f_time = raw_idx / (probe.fps or 24.0)
                    rel_t = f_time
                else:
                    if start_s is None:
                        start_s = f_time if f_time > 0.5 else 0.0
                    rel_t = f_time - start_s

                if rel_t + 1e-4 >= targets[target_pos]:
                    img = _av_frame_to_pil(frame)
                    yield ExtractedFrame(
                        index=out_idx,
                        source_index=raw_idx,
                        timestamp_s=round(rel_t, 4),
                        image=img,
                    )
                    out_idx += 1
                    # Advance past any targets covered by this frame (avoids duplicate images)
                    while target_pos < len(targets) and rel_t + 1e-4 >= targets[target_pos]:
                        target_pos += 1
                    if target_pos >= len(targets):
                        break
            else:
                if raw_idx == targets[target_pos]:
                    img = _av_frame_to_pil(frame)
                    yield ExtractedFrame(
                        index=out_idx,
                        source_index=raw_idx,
                        timestamp_s=round(frame.time - (start_s or 0.0), 4) if frame.time is not None else None,
                        image=img,
                    )
                    out_idx += 1
                    target_pos += 1
                    if target_pos >= len(targets):
                        break

        if out_idx < len(targets):
            logger.debug(
                "Video '%s' ended after yielding %d/%d planned frames (sequential).",
                probe.path.name,
                out_idx,
                len(targets),
            )


def _extract_video_seek(probe: MediaProbe, plan: FramePlan) -> Iterator[ExtractedFrame]:
    """Extract target timestamps via backward keyframe seeks + short forward decode."""
    import av

    with av.open(str(probe.path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"

        if stream.time_base is None:
            yield from _extract_video_sequential(probe, plan)
            return

        start_s = float(stream.start_time * stream.time_base) if stream.start_time is not None else 0.0
        time_base = stream.time_base
        out_idx = 0
        logged_rotation = False

        for target_t in plan.times_s:
            seek_pts = int((target_t + start_s) / time_base)
            try:
                container.seek(seek_pts, backward=True, stream=stream)
            except av.FFmpegError as exc:
                logger.warning(
                    "Seek failed at %.2fs in '%s' (%s); falling back to sequential.", target_t, probe.path.name, exc
                )
                if out_idx == 0:
                    yield from _extract_video_sequential(probe, plan)
                return

            found = False
            for step, frame in enumerate(container.decode(stream)):
                if not logged_rotation:
                    rot = getattr(frame, "rotation", 0)
                    if rot:
                        logger.debug("Applying %s° CCW rotation to frames from '%s'", rot, probe.path.name)
                    logged_rotation = True

                f_time = frame.time
                rel_t = (f_time - start_s) if f_time is not None else None

                # Accept once timestamp reaches target (or safety cap if PTS broken)
                if rel_t is None or rel_t + 1e-3 >= target_t or step >= 600:
                    img = _av_frame_to_pil(frame)
                    yield ExtractedFrame(
                        index=out_idx,
                        source_index=None,
                        timestamp_s=round(rel_t, 4) if rel_t is not None else round(target_t, 4),
                        image=img,
                    )
                    out_idx += 1
                    found = True
                    break

            if not found:
                logger.debug("Seek to %.2fs in '%s' reached EOF without a frame.", target_t, probe.path.name)


# endregion


# region Public Extraction API


def select_strategy(probe: MediaProbe, plan: FramePlan) -> ExtractionStrategy:
    """Determine which extraction strategy will be used for a probed media file."""
    if probe.kind == "image":
        return "static"
    if probe.kind == "animated_image":
        return "pillow_animated"
    if _should_use_seek_strategy(probe, plan):
        return "pyav_seek"
    return "pyav_sequential"


def iter_extract_frames(
    path: str | Path,
    config: FramePlanConfig | None = None,
    *,
    probe: MediaProbe | None = None,
    plan: FramePlan | None = None,
) -> Iterator[ExtractedFrame]:
    """
    Stream upright RGB PIL images from any static image, animated image, or video.
    Raises RuntimeError if no frames could be decoded from the file.
    """
    active_probe = probe or probe_media(path)
    active_plan = plan or plan_frames(active_probe.duration_s, active_probe.total_frames, config=config)
    strategy = select_strategy(active_probe, active_plan)

    logger.debug(
        "Extracting '%s' | kind=%s | mode=%s | planned=%d | strategy=%s",
        active_probe.path.name,
        active_probe.kind,
        active_plan.mode,
        active_plan.target_count,
        strategy,
    )

    if strategy in ("static", "pillow_animated"):
        gen = _extract_pillow_frames(active_probe, active_plan)
    elif strategy == "pyav_seek":
        gen = _extract_video_seek(active_probe, active_plan)
    else:
        gen = _extract_video_sequential(active_probe, active_plan)

    yielded_count = 0
    for item in gen:
        yielded_count += 1
        yield item

    if yielded_count == 0:
        raise RuntimeError(f"Decoded 0 frames from media file '{active_probe.path}'.")


def extract_frames(
    path: str | Path,
    config: FramePlanConfig | None = None,
) -> ExtractionResult:
    """
    Probe, plan, and extract all target frames into memory with timing telemetry.
    Useful for standalone inspection, debugging, and unit tests.
    """
    t0 = time.perf_counter()
    probe = probe_media(path)
    plan = plan_frames(probe.duration_s, probe.total_frames, config=config)
    strategy = select_strategy(probe, plan)
    frames = list(iter_extract_frames(path, config=config, probe=probe, plan=plan))
    elapsed = time.perf_counter() - t0

    return ExtractionResult(
        probe=probe,
        plan=plan,
        frames=frames,
        elapsed_s=elapsed,
        strategy=strategy,
    )


# endregion
