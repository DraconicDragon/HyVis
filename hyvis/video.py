"""
video.py Frame extraction for video and animated image files (GIF, APNG).

The sampler probes a dense grid of candidate frames across the content window
(duration minus fade-in/fade-out padding) in one forward pass, drops fade
frames, flat dark frames and near-duplicates, and keeps one frame per visually
distinct phase. The grid is ``probe_multiplier`` times the number of frames
that go to inference, capped at ``max_probes``, so the filters have
alternatives to reject. Frames are decoded forward whenever possible: seeking
is ruinous on containers without a frame index (WebM re-decodes from the start
of the file on every seek).
"""

from __future__ import annotations

import bisect
import io
import logging
import shutil
import subprocess
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hyvis.config import (
    DedupeConfig,
    FadeFilterConfig,
    LoopConfig,
    LowInterestFilterConfig,
)

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)

#: Longest edge (px) of the grayscale copy used for every luminance,
#: duplicate and loop-seam comparison. Kept small on purpose: it averages
#: compression noise away and runs ~400x cheaper than comparing full frames.
_ANALYSIS_SCALE = 160

#: Floor for the probe grid, so a tiny frame budget still gets a spread of
#: candidates instead of a single unlucky one.
_MIN_PROBES = 8

#: Frames the reader may walk forward before a seek is cheaper.
_MAX_FORWARD_GAP = 120

#: Loop seams tolerate more changed pixels than dedupe: the frames either
#: side of a seam are one frame apart, so a high-contrast element moving one
#: step already flips a few percent of pixels by more than the change level.
_LOOP_MIN_CHANGED_FRACTION = 0.10

#: Upper bound on one ffmpeg frame grab, so a broken file cannot hang a run.
_FFMPEG_TIMEOUT = 60.0

#: One probe and its analysis copy, as handed to ``_select_frames``.
Probe = tuple[int, "Image.Image", Any]


def _load_cv2() -> Any:
    """Import OpenCV lazily; video processing is the only user of it."""
    try:
        import cv2
    except ImportError as exc:
        raise ImportError(
            "Video support requires 'opencv-python-headless'. "
            "Install it with: pip install opencv-python-headless"
        ) from exc
    return cv2


def dump_frames(
    frames: Sequence["Image.Image"],
    *,
    file_hash: str,
    base_dir: str | Path = "frames",
) -> Path:
    """Write sampled frames as PNGs for debugging.

    Writes frame_000.png, frame_001.png, ... into base_dir/<file_hash>/ and
    returns that directory. The order matches the order the frames will be
    inferred in. Called only when [video].write_frames is enabled.
    """
    out_dir = Path(base_dir) / file_hash
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, frame in enumerate(frames):
        frame.save(out_dir / f"frame_{i:03d}.png")
    logger.debug("Dumped %d debug frame(s) to %s", len(frames), out_dir)
    return out_dir


# region Public API


def extract_frames(
    video_path: str | Path,
    *,
    max_frames: int = 11,
    max_frames_at_seconds: float = 60.0,
    probe_multiplier: int = 3,
    max_probes: int = 48,
    include_edges: bool = True,
    loop: LoopConfig | None = None,
    dedupe: DedupeConfig | None = None,
    fades: FadeFilterConfig | None = None,
    low_interest: LowInterestFilterConfig | None = None,
) -> tuple["Image.Image", ...]:
    """Sample frames from a video to send to inference.

    A probe grid is drawn across the content window (the clip minus padding
    around fade-ins and fade-outs), plus the true first and last frame when
    ``include_edges`` is on. Each probe is decoded once, in ascending order,
    and dropped when it is a fade frame (``fades.enabled``), a flat dark
    frame (``low_interest.enabled``) or a near-duplicate of an already-kept
    frame (``dedupe.enabled``). The survivors, one per visually distinct
    phase, are returned as RGB PIL images in ascending frame order.

    ``loop`` controls the loop heuristics: ``single_frame_below_seconds``
    returns one centered frame for trivially short clips, ``edges_max_seconds``
    skips the edges of long clips by duration alone, and ``detect`` compares
    the first and last frame and skips the edges when they match.

    Raises ValueError if the video cannot be opened or its duration cannot be
    determined.
    """
    cv2 = _load_cv2()
    loop = loop or LoopConfig()
    dedupe = dedupe or DedupeConfig()

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {video_path}")
    try:
        fps: float = cap.get(cv2.CAP_PROP_FPS)
        total_frame_count: int = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0 or total_frame_count <= 0:
            raise ValueError(
                f"Cannot determine video duration for {video_path} "
                f"(fps={fps}, frames={total_frame_count})"
            )

        total_duration = total_frame_count / fps
        if 0 < loop.single_frame_below_seconds and total_duration < loop.single_frame_below_seconds:
            center_frame = _read_center_frame(cv2, cap, total_frame_count)
            if center_frame is not None:
                logger.debug(
                    "extract_frames: %.2fs is below single_frame_below_seconds, using one frame",
                    total_duration,
                )
                return (_to_pil(cv2, center_frame),)

        frames = _sample_video(
            cap,
            fps=fps,
            total_frame_count=total_frame_count,
            max_frames=max_frames,
            max_frames_at_seconds=max_frames_at_seconds,
            probe_multiplier=probe_multiplier,
            max_probes=max_probes,
            include_edges=include_edges,
            loop=loop,
            dedupe=dedupe,
            fades=fades,
            low_interest=low_interest,
        )
        if not frames:
            # OpenCV can open the container but not decode it: AV1 builds that
            # only expose a hardware path fail every read. Try the ffmpeg CLI
            # before giving up, so the file is still sampled instead of skipped.
            frames = _sample_video_ffmpeg(
                video_path,
                fps=fps,
                total_frame_count=total_frame_count,
                max_frames=max_frames,
                max_frames_at_seconds=max_frames_at_seconds,
                probe_multiplier=probe_multiplier,
                max_probes=max_probes,
                include_edges=include_edges,
                loop=loop,
                dedupe=dedupe,
                fades=fades,
                low_interest=low_interest,
            )
    finally:
        cap.release()

    return frames


def extract_gif_frames(
    gif_path: str | Path,
    *,
    max_frames: int = 11,
    max_frames_at_seconds: float = 60.0,
    probe_multiplier: int = 3,
    max_probes: int = 48,
    include_edges: bool = True,
    loop: LoopConfig | None = None,
    dedupe: DedupeConfig | None = None,
    fades: FadeFilterConfig | None = None,
    low_interest: LowInterestFilterConfig | None = None,
) -> tuple["Image.Image", ...]:
    """Sample frames from an animated GIF or APNG to send to inference.

    Same probe-and-filter strategy as ``extract_frames``, with one difference:
    a GIF or APNG has no frame rate clock, so each frame's display duration
    from the file metadata defines the animation timeline and probe timestamps
    are mapped onto frame indices with a cumulative-duration lookup. Returns RGB
    PIL frames in ascending frame order. Raises ValueError if the file cannot
    be opened or has no frames.
    """
    from PIL import Image

    loop = loop or LoopConfig()
    dedupe = dedupe or DedupeConfig()

    try:
        gif = Image.open(gif_path)
    except Exception as exc:
        raise ValueError(f"Cannot open GIF: {gif_path}") from exc

    with gif:
        durations = _gif_durations(gif)
        total_frame_count = len(durations)
        if total_frame_count == 0:
            raise ValueError(f"GIF has no frames: {gif_path}")

        total_duration = sum(durations)
        if 0 < loop.single_frame_below_seconds and total_duration < loop.single_frame_below_seconds:
            gif.seek(max(0, (total_frame_count - 1) // 2))
            logger.debug(
                "extract_gif_frames: %.2fs is below single_frame_below_seconds, using one frame",
                total_duration,
            )
            return (gif.convert("RGB").copy(),)

        frames = _sample_gif(
            gif,
            durations=durations,
            max_frames=max_frames,
            max_frames_at_seconds=max_frames_at_seconds,
            probe_multiplier=probe_multiplier,
            max_probes=max_probes,
            include_edges=include_edges,
            loop=loop,
            dedupe=dedupe,
            fades=fades,
            low_interest=low_interest,
        )

    return frames


# endregion


# region Video sampling


class _VideoFrameReader:
    """Decodes video frames on demand, walking forward when it can.

    Callers request frames in ascending order. Random access is expensive on
    containers without a frame index (WebM: OpenCV re-decodes from the start
    of the file), so small forward gaps are decoded rather than sought.
    """

    def __init__(
        self,
        cv2: Any,
        cap: Any,
        *,
        total_frame_count: int,
        max_forward_gap: int = _MAX_FORWARD_GAP,
    ) -> None:
        self._cv2 = cv2
        self._cap = cap
        self._total = total_frame_count
        self._max_forward_gap = max_forward_gap
        self._next = 0

    def frame(self, frame_idx: int) -> Any | None:
        """Return the frame at ``frame_idx`` as a BGR array, or None."""
        if frame_idx < 0 or frame_idx >= self._total:
            return None
        if frame_idx < self._next or frame_idx - self._next > self._max_forward_gap:
            self._cap.set(self._cv2.CAP_PROP_POS_FRAMES, frame_idx)
            self._next = frame_idx
        frame = None
        while self._next <= frame_idx:
            ok, current = self._cap.read()
            self._next += 1
            if not ok:
                return None
            frame = current
        return frame


def _sample_video(
    cap: Any,
    *,
    fps: float,
    total_frame_count: int,
    max_frames: int,
    max_frames_at_seconds: float,
    probe_multiplier: int,
    max_probes: int,
    include_edges: bool,
    loop: LoopConfig,
    dedupe: DedupeConfig,
    fades: FadeFilterConfig | None,
    low_interest: LowInterestFilterConfig | None,
) -> tuple["Image.Image", ...]:
    """Probe and filter one open video capture."""
    cv2 = _load_cv2()
    total_duration = total_frame_count / fps
    padding_duration, content_start, content_end = _content_window(
        total_duration, fps, max_frames_at_seconds
    )
    content_duration = max(0.0, content_end - content_start)
    use_edges = _use_edges(loop, total_duration, include_edges)
    candidates = _probe_indices(
        total_frame_count=total_frame_count,
        padding_duration=padding_duration,
        content_duration=content_duration,
        count=_probe_count(max_frames, probe_multiplier, max_probes),
        include_edges=use_edges,
        frame_at=lambda timestamp: int(timestamp * fps),
    )
    keep_budget = _keep_budget(
        content_duration=content_duration,
        padding_duration=padding_duration,
        max_frames=max_frames,
        max_frames_at_seconds=max_frames_at_seconds,
        include_edges=use_edges,
    )
    reader = _VideoFrameReader(cv2, cap, total_frame_count=total_frame_count)

    def load_gray(frame_idx: int) -> Any | None:
        frame = reader.frame(frame_idx)
        return None if frame is None else _downscale_gray(cv2, frame, _ANALYSIS_SCALE)

    loop_detected = _detect_loop(load_gray, candidates, use_edges=use_edges, loop=loop, dedupe=dedupe)
    frames = _select_frames(
        _video_probes(cv2, reader, candidates, loop_detected=loop_detected, total_frame_count=total_frame_count),
        keep_budget=keep_budget,
        fades=fades,
        low_interest=low_interest,
        dedupe=dedupe,
    )
    logger.debug(
        "extract_frames: fps=%s frames=%s duration=%.2f probes=%s kept=%s loop=%s",
        fps,
        total_frame_count,
        total_duration,
        len(candidates),
        len(frames),
        loop_detected,
    )
    return tuple(frames)


def _ffmpeg_binary() -> str | None:
    """Path to the ffmpeg CLI, or None when it is not installed."""
    return shutil.which("ffmpeg")


def _ffmpeg_frame(video_path: str | Path, timestamp: float) -> "Image.Image" | None:
    """Decode one frame at ``timestamp`` through the ffmpeg CLI.

    Only used when OpenCV cannot decode the file. One subprocess per probe
    keeps this bounded and stateless; probes are at most ``max_probes`` per
    clip, and clips that reach this path are ones that would otherwise be
    skipped entirely. Returns an RGB PIL frame, or None when the timestamp
    cannot be decoded.
    """
    from PIL import Image

    ffmpeg = _ffmpeg_binary()
    if ffmpeg is None:
        return None
    command = [
        ffmpeg,
        "-v",
        "error",
        "-nostdin",
        "-ss",
        f"{max(0.0, timestamp):.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-an",
        "-sn",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=_FFMPEG_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not completed.stdout:
        return None
    try:
        frame = Image.open(io.BytesIO(completed.stdout))
        return frame.convert("RGB")
    except (OSError, ValueError):
        return None


def _sample_video_ffmpeg(
    video_path: str | Path,
    *,
    fps: float,
    total_frame_count: int,
    max_frames: int,
    max_frames_at_seconds: float,
    probe_multiplier: int,
    max_probes: int,
    include_edges: bool,
    loop: LoopConfig,
    dedupe: DedupeConfig,
    fades: FadeFilterConfig | None,
    low_interest: LowInterestFilterConfig | None,
) -> tuple["Image.Image", ...]:
    """Probe and filter a video with the ffmpeg decoder instead of OpenCV."""
    if _ffmpeg_binary() is None:
        return ()
    total_duration = total_frame_count / fps
    padding_duration, content_start, content_end = _content_window(
        total_duration, fps, max_frames_at_seconds
    )
    content_duration = max(0.0, content_end - content_start)
    use_edges = _use_edges(loop, total_duration, include_edges)
    candidates = _probe_indices(
        total_frame_count=total_frame_count,
        padding_duration=padding_duration,
        content_duration=content_duration,
        count=_probe_count(max_frames, probe_multiplier, max_probes),
        include_edges=use_edges,
        frame_at=lambda timestamp: int(timestamp * fps),
    )
    keep_budget = _keep_budget(
        content_duration=content_duration,
        padding_duration=padding_duration,
        max_frames=max_frames,
        max_frames_at_seconds=max_frames_at_seconds,
        include_edges=use_edges,
    )
    cache: dict[int, "Image.Image | None"] = {}

    def grab(frame_idx: int) -> "Image.Image | None":
        if frame_idx not in cache:
            cache[frame_idx] = _ffmpeg_frame(video_path, frame_idx / fps)
        return cache[frame_idx]

    def load_gray(frame_idx: int) -> Any | None:
        frame = grab(frame_idx)
        return None if frame is None else _pil_gray(frame, _ANALYSIS_SCALE)

    loop_detected = _detect_loop(
        load_gray, candidates, use_edges=use_edges, loop=loop, dedupe=dedupe
    )
    probes: list[Probe] = []
    for idx in candidates:
        if loop_detected and idx in (0, total_frame_count - 1):
            continue
        frame = grab(idx)
        if frame is None:
            continue
        probes.append((idx, frame, _pil_gray(frame, _ANALYSIS_SCALE)))
    frames = _select_frames(
        probes,
        keep_budget=keep_budget,
        fades=fades,
        low_interest=low_interest,
        dedupe=dedupe,
    )
    logger.debug(
        "extract_frames: ffmpeg fallback probes=%s kept=%s loop=%s",
        len(candidates),
        len(frames),
        loop_detected,
    )
    return tuple(frames)


def _video_probes(
    cv2: Any,
    reader: _VideoFrameReader,
    candidates: Sequence[int],
    *,
    loop_detected: bool,
    total_frame_count: int,
) -> Iterator[Probe]:
    """Decode each candidate once, skipping a loop's repeated edge frames."""
    for idx in candidates:
        if loop_detected and idx in (0, total_frame_count - 1):
            continue
        frame = reader.frame(idx)
        if frame is None:
            continue
        yield idx, _to_pil(cv2, frame), _downscale_gray(cv2, frame, _ANALYSIS_SCALE)


# endregion


# region GIF sampling


def _gif_durations(gif: "Image.Image") -> list[float]:
    """Per-frame display durations (seconds), defaulting to 100 ms when unset."""
    from PIL import ImageSequence

    durations: list[float] = []
    for frame in ImageSequence.Iterator(gif):
        duration_ms = float(frame.info.get("duration", 0) or 0)
        durations.append(duration_ms / 1000.0 if duration_ms > 0 else 0.1)
    return durations


def _sample_gif(
    gif: "Image.Image",
    *,
    durations: Sequence[float],
    max_frames: int,
    max_frames_at_seconds: float,
    probe_multiplier: int,
    max_probes: int,
    include_edges: bool,
    loop: LoopConfig,
    dedupe: DedupeConfig,
    fades: FadeFilterConfig | None,
    low_interest: LowInterestFilterConfig | None,
) -> tuple["Image.Image", ...]:
    """Probe and filter one open GIF."""
    total_frame_count = len(durations)
    total_duration = sum(durations) or max(0.1, 0.1 * total_frame_count)
    fps = total_frame_count / total_duration
    starts = _frame_starts(durations)

    def index_at(timestamp: float) -> int:
        """Frame index whose display interval contains ``timestamp``."""
        position = bisect.bisect_right(starts, timestamp) - 1
        return max(0, min(total_frame_count - 1, position))

    padding_duration, content_start, content_end = _content_window(
        total_duration, fps, max_frames_at_seconds
    )
    content_duration = max(0.0, content_end - content_start)
    use_edges = _use_edges(loop, total_duration, include_edges)
    candidates = _probe_indices(
        total_frame_count=total_frame_count,
        padding_duration=padding_duration,
        content_duration=content_duration,
        count=_probe_count(max_frames, probe_multiplier, max_probes),
        include_edges=use_edges,
        frame_at=index_at,
    )
    keep_budget = _keep_budget(
        content_duration=content_duration,
        padding_duration=padding_duration,
        max_frames=max_frames,
        max_frames_at_seconds=max_frames_at_seconds,
        include_edges=use_edges,
    )

    def load_gray(frame_idx: int) -> Any | None:
        try:
            gif.seek(frame_idx)
            return _pil_gray(gif.convert("RGB"), _ANALYSIS_SCALE)
        except (EOFError, OSError, ValueError):
            return None

    loop_detected = _detect_loop(load_gray, candidates, use_edges=use_edges, loop=loop, dedupe=dedupe)
    frames = _select_frames(
        _gif_probes(gif, candidates, loop_detected=loop_detected, total_frame_count=total_frame_count),
        keep_budget=keep_budget,
        fades=fades,
        low_interest=low_interest,
        dedupe=dedupe,
    )
    logger.debug(
        "extract_gif_frames: frames=%s duration=%.2f probes=%s kept=%s loop=%s",
        total_frame_count,
        total_duration,
        len(candidates),
        len(frames),
        loop_detected,
    )
    return tuple(frames)


def _frame_starts(durations: Sequence[float]) -> list[float]:
    """Cumulative start timestamp of each GIF frame, in seconds."""
    starts: list[float] = []
    elapsed = 0.0
    for duration in durations:
        starts.append(elapsed)
        elapsed += duration
    return starts


def _gif_probes(
    gif: "Image.Image",
    candidates: Sequence[int],
    *,
    loop_detected: bool,
    total_frame_count: int,
) -> Iterator[Probe]:
    """Decode each candidate GIF frame once, skipping a loop's edge frames."""
    for idx in candidates:
        if loop_detected and idx in (0, total_frame_count - 1):
            continue
        try:
            gif.seek(idx)
            frame = gif.convert("RGB").copy()
        except (EOFError, OSError, ValueError):
            continue
        yield idx, frame, _pil_gray(frame, _ANALYSIS_SCALE)


# endregion


# region Probe grid


def _probe_count(max_frames: int, probe_multiplier: int, max_probes: int) -> int:
    """Number of candidate frames to probe for a clip."""
    return max(_MIN_PROBES, min(max_probes, probe_multiplier * max_frames))


def _probe_indices(
    *,
    total_frame_count: int,
    padding_duration: float,
    content_duration: float,
    count: int,
    include_edges: bool,
    frame_at: Callable[[float], int],
) -> list[int]:
    """Frame indices for the probe grid, ascending and deduplicated.

    The content window is split into ``count + 1`` equal slices and one probe
    is taken at the end of each slice, so every part of the clip gets a
    chance to be represented. With ``include_edges`` the true first and last
    frame are added as probes. ``frame_at`` maps a timestamp to a frame index
    (video: timestamp x fps, GIF: display-duration lookup).
    """
    if content_duration <= 0 or total_frame_count <= 0:
        center = max(0, (total_frame_count - 1) // 2)
        if include_edges and total_frame_count > 1:
            return sorted({0, center, total_frame_count - 1})
        return [center]

    step = content_duration / (count + 1)
    indices = [frame_at(padding_duration + step * (i + 1)) for i in range(count)]
    if include_edges:
        indices = [0, *indices, total_frame_count - 1]
    return sorted({max(0, min(total_frame_count - 1, idx)) for idx in indices})


def _keep_budget(
    *,
    content_duration: float,
    padding_duration: float,
    max_frames: int,
    max_frames_at_seconds: float,
    include_edges: bool,
) -> int:
    """How many probed frames may be kept for this clip.

    Scaling with duration keeps inference cost proportional to how much
    material there is: short clips keep a single center frame plus edges,
    longer clips scale up to ``max_frames``. The extra two frames cover the
    padding slices when there is enough content to give them their own
    budget.
    """
    if content_duration < max_frames_at_seconds:
        budget = int(content_duration / max_frames_at_seconds * max_frames)
    else:
        budget = max_frames
    keep = max(1, budget)
    if include_edges:
        keep += 2
    if padding_duration * 2 < content_duration and budget > 1:
        keep += 2
    return min(keep, max_frames + 4)


def _use_edges(loop: LoopConfig, total_duration: float, include_edges: bool) -> bool:
    """Duration-only loop rule: long clips skip their edge probes."""
    if loop.edges_max_seconds > 0 and total_duration > loop.edges_max_seconds:
        return False
    return include_edges


def _detect_loop(
    load_gray: Callable[[int], Any | None],
    candidates: Sequence[int],
    *,
    use_edges: bool,
    loop: LoopConfig,
    dedupe: DedupeConfig,
) -> bool:
    """Whether the clip's first and last frame are near-identical.

    The last frame is loaded first: on video containers that means one seek
    deep into the file instead of decoding everything up to the end.
    """
    if not (use_edges and loop.detect) or len(candidates) < 3:
        return False
    try:
        last = load_gray(candidates[-1])
        first = load_gray(candidates[0])
        if first is None or last is None:
            return False
        return _frames_match(
            first,
            last,
            threshold=dedupe.threshold,
            change_level=dedupe.change_level,
            max_fraction=max(_LOOP_MIN_CHANGED_FRACTION, dedupe.max_changed_fraction),
        )
    except Exception as exc:  # detection is best-effort
        logger.debug("loop detection failed: %s", exc)
        return False


# endregion


# region Frame selection


def _select_frames(
    probes: Iterator[Probe],
    *,
    keep_budget: int,
    fades: FadeFilterConfig | None,
    low_interest: LowInterestFilterConfig | None,
    dedupe: DedupeConfig,
) -> list["Image.Image"]:
    """Keep the first probe of every visually distinct, non-dead phase.

    Probes arrive in ascending frame order, so keeping the first survivor of
    a phase and dropping its near-duplicates is exactly "one frame per
    distinct moment". A clip whose every probe is filtered still yields one
    frame (the brightest probe) rather than failing the file.
    """
    kept: list["Image.Image"] = []
    accepted_grays: list[Any] = []
    brightest: tuple[float, "Image.Image"] | None = None
    for idx, frame, gray in probes:
        mean, std = _luminance(gray)
        if brightest is None or mean > brightest[0]:
            brightest = (mean, frame)
        if fades is not None and fades.enabled and _fade_stats_test(
            mean, std, dark=fades.dark_threshold, light=fades.light_threshold, uniformity=fades.uniformity
        ):
            logger.debug("dropping fade frame at index %s", idx)
            continue
        if (
            low_interest is not None
            and low_interest.enabled
            and mean < low_interest.min_luminance
            and std < low_interest.min_std
        ):
            logger.debug("dropping low-interest frame at index %s", idx)
            continue
        if dedupe.enabled and _is_near_duplicate(
            gray, accepted_grays, dedupe.threshold, dedupe.change_level, dedupe.max_changed_fraction
        ):
            continue
        kept.append(frame)
        accepted_grays.append(gray)
        if len(kept) >= keep_budget:
            break
    if not kept and brightest is not None:
        logger.debug("every probed frame was filtered, keeping the brightest")
        kept.append(brightest[1])
    return kept


def _luminance(gray: Any) -> tuple[float, float]:
    """Mean and standard deviation of a grayscale array, normalized to 0-1."""
    arr = gray.astype("float32")
    return float(arr.mean() / 255.0), float(arr.std() / 255.0)


def _fade_stats_test(
    mean: float,
    std: float,
    *,
    dark: float,
    light: float,
    uniformity: float,
) -> bool:
    """Fade test on precomputed (mean, std): extreme luminance AND flat."""
    extreme = mean < dark or mean > light
    return extreme and std < uniformity


def _frames_match(
    gray_a: Any,
    gray_b: Any,
    *,
    threshold: float,
    change_level: int,
    max_fraction: float,
) -> bool:
    """True when two downscaled gray frames are near-identical."""
    import numpy as np

    if max_fraction <= 0 or gray_a.shape != gray_b.shape:
        return False
    diff = np.abs(gray_a.astype(np.int16) - gray_b.astype(np.int16))
    if float(diff.mean()) / 255.0 >= threshold:
        return False
    return float((diff > change_level).mean()) < max_fraction


def _is_near_duplicate(
    gray: Any,
    accepted_grays: list[Any],
    threshold: float,
    change_level: int,
    max_fraction: float,
) -> bool:
    """Return True if gray is near-identical to any already-accepted frame.

    Both tests must pass: the mean absolute difference is below ``threshold``
    (normalized), and the fraction of pixels differing by more than
    ``change_level`` 8-bit gray levels is below ``max_fraction``. The second
    test is area-independent, so a small but important change (a character, a
    face, water movement) in a mostly static frame changes a measurable
    fraction of pixels by a lot and is kept, while a true loop repeat or
    static segment changes ~0% of pixels and is dropped.

    A ``max_fraction`` of 0 disables dedupe entirely.
    """
    if max_fraction <= 0:
        return False
    return any(
        _frames_match(
            gray,
            other,
            threshold=threshold,
            change_level=change_level,
            max_fraction=max_fraction,
        )
        for other in accepted_grays
    )


# endregion


# region Shared helpers


def _content_window(
    total_duration: float,
    fps: float,
    max_frames_at_seconds: float,
) -> tuple[float, float, float]:
    """
    Compute fade padding and the content window.

    Returns (padding_duration, content_start, content_end) in seconds.
    Padding scales from 0.2s to 3.0s with duration (capped at 3.0s past
    max_frames_at_seconds) to skip fade-ins/fade-outs; clamped so very short
    videos never end up with a negative content duration.
    """
    min_padding = 0.2
    max_padding = 3.0

    scaling_ratio = total_duration / max_frames_at_seconds
    if total_duration < max_frames_at_seconds:
        padding = (scaling_ratio * (max_padding - min_padding)) + min_padding
    else:
        padding = max_padding

    padding_duration = padding - (padding % 1 / fps)  # modulo aligns to frametime
    padding_duration = min(padding_duration, total_duration / 2)

    content_start = padding_duration
    content_end = total_duration - padding_duration
    return padding_duration, content_start, content_end


def _read_frame(cv2: Any, cap: Any, frame_idx: int) -> Any | None:
    """Seek to frame_idx and return it as a BGR ndarray, or None on failure."""
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ret, frame = cap.read()
    return frame if ret else None


def _read_center_frame(cv2: Any, cap: Any, total_frame_count: int) -> Any | None:
    """Read the frame at the middle of the clip, or None if it can't be read."""
    return _read_frame(cv2, cap, max(0, (total_frame_count - 1) // 2))


def _to_pil(cv2: Any, frame_bgr: Any) -> "Image.Image":
    from PIL import Image

    return Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))


def _downscale_gray(cv2: Any, frame_bgr: Any, max_dim: int) -> Any:
    """Return a grayscale copy of the frame scaled so its max dimension is <= max_dim px.

    INTER_LINEAR: adequate for a mean-difference signal and far cheaper than
    INTER_AREA at large downscale ratios (this runs on every probe).
    """
    h, w = frame_bgr.shape[:2]
    if max(h, w) > max_dim:
        scale = max_dim / max(h, w)
        frame_bgr = cv2.resize(
            frame_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_LINEAR
        )
    return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)


def _pil_gray(frame: "Image.Image", max_dim: int) -> Any:
    """Return a downscaled grayscale array of a PIL frame.

    cv2-free equivalent of ``_downscale_gray`` for already-decoded frames
    (GIF sampling).
    """
    import numpy as np
    from PIL import Image

    gray = frame.convert("L")
    width, height = gray.size
    if max(width, height) > max_dim:
        scale = max_dim / max(width, height)
        gray = gray.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            resample=Image.Resampling.BOX,
        )
    return np.asarray(gray)


# endregion
