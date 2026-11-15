"""
video.py Frame extraction for video and animated GIF files.

Frames are sampled evenly across the content duration (the strategy ported
from the standalone reference script reference.py), with padding around
fade-ins/fade-outs and optional first/last frames. Sampled frames can then
be filtered (fades replaced, low-interest frames dropped) and near-
duplicates removed before inference.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hyvis.config import (
    FadeFilterConfig,
    LowInterestFilterConfig,
    is_video_mime,  # noqa: F401
)

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


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


def extract_frames(
    video_path: str | Path,
    *,
    max_frames: int = 11,
    max_frames_at_seconds: float = 60.0,
    sampler: str = "scan",
    include_edges: bool = True,
    include_edges_max_seconds: float = 0.0,
    single_frame_below_seconds: float = 1.0,
    detect_loops: bool = True,
    fades: FadeFilterConfig | None = None,
    low_interest: LowInterestFilterConfig | None = None,
    dedupe: bool = True,
    dedupe_threshold: float = 0.03,
    dedupe_change_level: int = 25,
    dedupe_max_changed_fraction: float = 0.01,
) -> tuple["Image.Image", ...]:
    """Extract frames from a video at certain intervals.

    Frames are distributed evenly across the content duration, with padding
    around fade-ins/fade-outs and optional inclusion of the first/last
    frame. When ``include_edges_max_seconds`` is non-zero and the clip is
    longer than that, it is assumed to be a loop: edges are skipped and
    sampling starts at the center of the content window and works outward.

    With ``sampler="scan"`` (the default) a dense probe grid over the content
    window is decoded once; fade frames, flat dark frames and near-duplicates
    are dropped, and one frame is kept per visually distinct phase. This
    avoids repeated seeks (very slow on WebM) and usually yields more
    distinct frames per inference. ``sampler="uniform"`` keeps the original
    even-sampling + fade replacement path.

    Clips shorter than ``single_frame_below_seconds`` are assumed to be
    loops too short to say anything beyond a single frame; one centered
    frame is returned and the fade/dedupe/low-interest filters are skipped.

    If `fades` is given, sampled frames that land inside fade-to-black /
    fade-to-white transitions are replaced by the nearest non-fade frame
    (see FadeFilterConfig).

    If `low_interest` is given (and enabled), sampled frames with little
    interesting content (flat AND dark - e.g. black frames) are dropped
    before inference (see LowInterestFilterConfig).

    Returns the sampled frames as PIL Images in RGB mode (in memory; no temp
    files are written). Raises ValueError if the video cannot be opened or
    has no usable duration.
    """
    cv2 = _load_cv2()
    from PIL import Image

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {video_path}")

    fps: float = cap.get(cv2.CAP_PROP_FPS)
    total_frame_count: int = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps <= 0 or total_frame_count <= 0:
        cap.release()
        raise ValueError(
            f"Cannot determine video duration for {video_path} (fps={fps}, frames={total_frame_count})"
        )

    total_duration: float = total_frame_count / fps
    if 0 < single_frame_below_seconds and total_duration < single_frame_below_seconds:
        center_frame = _read_center_frame(cv2, cap, total_frame_count)
        if center_frame is not None:
            cap.release()
            logger.debug(
                "extract_frames: %.2fs is below single_frame_below_seconds, using one frame",
                total_duration,
            )
            return (_to_pil(cv2, center_frame),)
    use_edges, center_out = _resolve_edges(
        total_duration, include_edges, include_edges_max_seconds
    )
    if sampler == "scan":
        try:
            return _extract_scan(
                cap,
                fps=fps,
                total_frame_count=total_frame_count,
                max_frames=max_frames,
                max_frames_at_seconds=max_frames_at_seconds,
                include_edges=use_edges,
                detect_loops=detect_loops,
                fades=fades,
                low_interest=low_interest,
                dedupe=dedupe,
                dedupe_threshold=dedupe_threshold,
                dedupe_change_level=dedupe_change_level,
                dedupe_max_changed_fraction=dedupe_max_changed_fraction,
            )
        finally:
            cap.release()
    if use_edges and detect_loops:
        try:
            if _detect_loop_video(
                cv2,
                cap,
                total_frame_count,
                threshold=dedupe_threshold,
                change_level=dedupe_change_level,
                max_fraction=max(_LOOP_MIN_CHANGED_FRACTION, dedupe_max_changed_fraction),
            ):
                logger.debug("extract_frames: %s looks like a loop, skipping edges", video_path)
                use_edges, center_out = False, True
        except Exception as exc:  # detection is best-effort
            logger.debug("extract_frames: loop detection failed for %s: %s", video_path, exc)

    try:
        frames = _extract_uniform(
            cap,
            fps=fps,
            total_frame_count=total_frame_count,
            max_frames=max_frames,
            max_frames_at_seconds=max_frames_at_seconds,
            include_edges=use_edges,
            center_out=center_out,
            fades=fades,
            dedupe=dedupe,
            dedupe_threshold=dedupe_threshold,
            dedupe_change_level=dedupe_change_level,
            dedupe_max_changed_fraction=dedupe_max_changed_fraction,
        )
    finally:
        cap.release()

    if low_interest is not None and low_interest.enabled:
        frames = _filter_low_interest_frames(frames, low_interest)
    return frames


def extract_gif_frames(
    gif_path: str | Path,
    *,
    max_frames: int = 11,
    max_frames_at_seconds: float = 60.0,
    sampler: str = "scan",
    include_edges: bool = True,
    include_edges_max_seconds: float = 0.0,
    single_frame_below_seconds: float = 1.0,
    detect_loops: bool = True,
    fades: FadeFilterConfig | None = None,
    low_interest: LowInterestFilterConfig | None = None,
    dedupe: bool = True,
    dedupe_threshold: float = 0.03,
    dedupe_change_level: int = 25,
    dedupe_max_changed_fraction: float = 0.01,
) -> tuple["Image.Image", ...]:
    """Extract frames from an animated GIF using the video sampling strategy.

    A GIF has no frame rate clock, so each frame's display duration from the
    GIF metadata defines the total animation duration. The same uniform
    timestamp distribution as videos is then applied: content padding,
    optional first/last frames, fade replacement, low-interest filtering and
    near-duplicate removal. When ``include_edges_max_seconds`` is non-zero and
    the animation is longer than that, it is assumed to be a loop: edges are
    skipped and sampling starts at the center of the content window and works
    outward. Animations shorter than ``single_frame_below_seconds`` are assumed
    to be loops too short to say anything beyond a single frame; one centered
    frame is returned and the filters are skipped. Returns the sampled frames
    as PIL Images in RGB mode. Raises ValueError if the file cannot be opened
    or has no frames.
    """
    from PIL import Image, ImageSequence

    try:
        gif = Image.open(gif_path)
    except Exception as exc:
        raise ValueError(f"Cannot open GIF: {gif_path}") from exc

    with gif:
        durations: list[float] = []
        for frame in ImageSequence.Iterator(gif):
            duration_ms = float(frame.info.get("duration", 0) or 0)
            durations.append(duration_ms / 1000.0 if duration_ms > 0 else 0.1)

        total_frame_count = len(durations)
        if total_frame_count == 0:
            raise ValueError(f"GIF has no frames: {gif_path}")

        total_duration = sum(durations)
        fps = total_frame_count / total_duration
        if 0 < single_frame_below_seconds and total_duration < single_frame_below_seconds:
            gif.seek(max(0, (total_frame_count - 1) // 2))
            logger.debug(
                "extract_gif_frames: %.2fs is below single_frame_below_seconds, using one frame",
                total_duration,
            )
            return (gif.convert("RGB").copy(),)
        use_edges, center_out = _resolve_edges(
            total_duration, include_edges, include_edges_max_seconds
        )
        if sampler == "scan":
            return _extract_gif_scan(
                gif,
                durations=durations,
                max_frames=max_frames,
                max_frames_at_seconds=max_frames_at_seconds,
                include_edges=use_edges,
                detect_loops=detect_loops,
                fades=fades,
                low_interest=low_interest,
                dedupe=dedupe,
                dedupe_threshold=dedupe_threshold,
                dedupe_change_level=dedupe_change_level,
                dedupe_max_changed_fraction=dedupe_max_changed_fraction,
            )
        if use_edges and detect_loops:
            try:
                if _detect_loop_gif(
                    gif,
                    total_frame_count,
                    threshold=dedupe_threshold,
                    change_level=dedupe_change_level,
                    max_fraction=max(_LOOP_MIN_CHANGED_FRACTION, dedupe_max_changed_fraction),
                ):
                    logger.debug("extract_gif_frames: %s looks like a loop, skipping edges", gif_path)
                    use_edges, center_out = False, True
            except Exception as exc:  # detection is best-effort
                logger.debug("extract_gif_frames: loop detection failed for %s: %s", gif_path, exc)

        padding_duration, content_start, content_end = _content_window(
            total_duration, fps, max_frames_at_seconds
        )
        content_duration = content_end - content_start
        num_frames_to_extract = _sample_count(content_duration, max_frames, max_frames_at_seconds)

        timestamps = _uniform_timestamps(
            total_duration=total_duration,
            fps=fps,
            num_frames_to_extract=num_frames_to_extract,
            padding_duration=padding_duration,
            content_duration=content_duration,
            include_edges=use_edges,
            center_out=center_out,
        )

        logger.debug(
            "extract_gif_frames frames=%s duration=%.2f samples=%s",
            total_frame_count,
            total_duration,
            len(timestamps),
        )

        selected = [max(0, min(total_frame_count - 1, int(timestamp * fps))) for timestamp in timestamps]

        if fades is not None:
            stats_cache: dict[int, tuple[float, float] | None] = {}

            def get_stats(idx: int) -> tuple[float, float] | None:
                if idx not in stats_cache:
                    stats_cache[idx] = _gif_luminance_stats(gif, idx)
                return stats_cache[idx]

            selected = _filter_fade_frames(
                selected,
                get_stats=get_stats,
                fps=fps,
                total_frame_count=total_frame_count,
                fades=fades,
            )

        frames: list[Image.Image] = []
        for frame_idx in sorted(set(selected)):
            gif.seek(frame_idx)
            frames.append(gif.convert("RGB").copy())

    if dedupe:
        frames = list(
            _dedupe_loaded_frames(
                frames,
                threshold=dedupe_threshold,
                change_level=dedupe_change_level,
                max_fraction=dedupe_max_changed_fraction,
            )
        )

    if low_interest is not None and low_interest.enabled:
        frames = list(_filter_low_interest_frames(frames, low_interest))

    return tuple(frames)


def _gif_luminance_stats(
    gif: "Image.Image",
    frame_idx: int,
) -> tuple[float, float] | None:
    """Return (mean, std) of luminance (0-1) for a GIF frame, or None."""
    import numpy as np

    try:
        gif.seek(frame_idx)
    except (EOFError, OSError, ValueError):
        return None

    arr = _pil_gray(gif, _FADE_ANALYSIS_SCALE).astype(np.float32)
    return float(arr.mean() / 255.0), float(arr.std() / 255.0)


def _dedupe_loaded_frames(
    frames: Sequence["Image.Image"],
    *,
    threshold: float,
    change_level: int,
    max_fraction: float,
) -> tuple["Image.Image", ...]:
    """Greedy near-duplicate removal for already-decoded frames.

    Same policy as `_dedupe_near_duplicates`: the first occurrence of a
    near-identical group is kept and later ones dropped.
    """
    accepted: list["Image.Image"] = []
    accepted_grays: list[Any] = []
    for frame in frames:
        gray = _pil_gray(frame, _FADE_ANALYSIS_SCALE)
        if _is_near_duplicate(gray, accepted_grays, threshold, change_level, max_fraction):
            continue
        accepted.append(frame)
        accepted_grays.append(gray)
    return tuple(accepted)


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


def _resolve_edges(
    total_duration: float,
    include_edges: bool,
    include_edges_max_seconds: float,
) -> tuple[bool, bool]:
    """Return (use_edges, center_out) for a clip's duration.

    Clips longer than ``include_edges_max_seconds`` are assumed to be loops:
    their edge frames add no information, so edges are skipped and samples
    are taken from the center of the content window outward. A threshold of
    0 disables the duration rule.
    """
    if include_edges_max_seconds <= 0.0 or total_duration <= include_edges_max_seconds:
        return include_edges, False
    return False, True


def _frames_match(
    gray_a: Any,
    gray_b: Any,
    *,
    threshold: float,
    change_level: int,
    max_fraction: float,
) -> bool:
    """True when two downscaled gray frames are near-identical.

    Same two-part test as near-duplicate removal: a low mean difference AND
    almost no pixels changed by more than ``change_level`` gray levels. A
    near-static scene with one moving element is therefore not mistaken for
    a loop seam.
    """
    import numpy as np

    if max_fraction <= 0 or gray_a.shape != gray_b.shape:
        return False
    diff = np.abs(gray_a.astype(np.int16) - gray_b.astype(np.int16))
    if float(diff.mean()) / 255.0 >= threshold:
        return False
    return float((diff > change_level).mean()) < max_fraction


def _detect_loop_video(
    cv2: Any,
    cap: Any,
    total_frame_count: int,
    *,
    threshold: float,
    change_level: int,
    max_fraction: float,
) -> bool:
    """True if a video's first and last frame are near-identical (a loop)."""
    first = _read_frame(cv2, cap, 0)
    last = _read_frame(cv2, cap, max(0, total_frame_count - 1))
    if first is None or last is None:
        return False
    return _frames_match(
        _downscale_gray(cv2, first, _FADE_ANALYSIS_SCALE),
        _downscale_gray(cv2, last, _FADE_ANALYSIS_SCALE),
        threshold=threshold,
        change_level=change_level,
        max_fraction=max_fraction,
    )


def _detect_loop_gif(
    gif: "Image.Image",
    total_frame_count: int,
    *,
    threshold: float,
    change_level: int,
    max_fraction: float,
) -> bool:
    """True if a GIF's first and last frame are near-identical (a loop)."""
    if total_frame_count < 2:
        return False
    gif.seek(0)
    first = _pil_gray(gif.convert("RGB"), _FADE_ANALYSIS_SCALE)
    gif.seek(total_frame_count - 1)
    last = _pil_gray(gif.convert("RGB"), _FADE_ANALYSIS_SCALE)
    return _frames_match(
        first, last, threshold=threshold, change_level=change_level, max_fraction=max_fraction
    )


def _read_frame(cv2: Any, cap: Any, frame_idx: int) -> Any | None:
    """Seek to frame_idx and return it as a BGR ndarray, or None on failure."""
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ret, frame = cap.read()
    return frame if ret else None


def _to_pil(cv2: Any, frame_bgr: Any) -> "Image.Image":
    from PIL import Image

    return Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))


def _read_center_frame(cv2: Any, cap: Any, total_frame_count: int) -> Any | None:
    """Read the frame at the middle of the clip, or None if it can't be read."""
    return _read_frame(cv2, cap, max(0, (total_frame_count - 1) // 2))


def _downscale_gray(cv2: Any, frame_bgr: Any, max_dim: int) -> Any:
    """Return a grayscale copy of the frame scaled so its max dimension is <= max_dim px.

    INTER_LINEAR: adequate for a mean-difference signal and far cheaper than
    INTER_AREA at large downscale ratios (this runs on every frame).
    """
    h, w = frame_bgr.shape[:2]
    if max(h, w) > max_dim:
        scale = max_dim / max(h, w)
        frame_bgr = cv2.resize(frame_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_LINEAR)
    return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)


def _mean_diff(cv2: Any, a: Any, b: Any) -> float:
    """Normalized (0-1) mean absolute difference between two equal-sized arrays."""
    return float(cv2.absdiff(a, b).mean() / 255.0)


#: Resolution used for fade-frame detection (mean/std of luminance only).
_FADE_ANALYSIS_SCALE = 160

#: Loop-seam detection tolerates more changed pixels than dedupe: consecutive
#: frames around the seam are one frame apart, so a high-contrast element
#: moving one step already flips a few percent of pixels by >25 gray levels.
_LOOP_MIN_CHANGED_FRACTION = 0.10


def _luminance_stats(cv2: Any, cap: Any, frame_idx: int) -> tuple[float, float] | None:
    """Return (mean, std) of luminance (0-1) for a frame, or None if it can't be read."""
    frame = _read_frame(cv2, cap, frame_idx)
    if frame is None:
        return None
    gray = _downscale_gray(cv2, frame, _FADE_ANALYSIS_SCALE)
    mean, std = cv2.meanStdDev(gray)
    return float(mean[0][0]) / 255.0, float(std[0][0]) / 255.0


class _VideoFrameStats:
    """Luminance stats for video frames, decoded forward in one pass.

    The fade filter asks for stats of a frame and its neighbourhood in
    ascending order. Seeking to every index separately is ruinous for
    containers without a frame index (WebM/Matroska): OpenCV falls back to
    decoding from the start of the file on each seek, so a 12-frame sample
    could take minutes. This reader keeps a decode cursor: requests at or
    after it advance with plain reads, and only a backward or very distant
    request seeks. Repeated requests are cached.
    """

    def __init__(self, cv2: Any, cap: Any, *, total_frame_count: int) -> None:
        self._cv2 = cv2
        self._cap = cap
        self._total = total_frame_count
        self._cache: dict[int, tuple[float, float] | None] = {}
        # Index of the frame that the next `read()` will return.
        self._next = 0
        # Decoding forward through more than this many frames is slower than
        # one seek on indexed containers.
        self._max_forward_gap = 96

    def get(self, frame_idx: int) -> tuple[float, float] | None:
        """Return (mean, std) of luminance (0-1) for a frame, or None."""
        if frame_idx in self._cache:
            return self._cache[frame_idx]
        if frame_idx < 0 or frame_idx >= self._total:
            return None
        if frame_idx < self._next or frame_idx - self._next > self._max_forward_gap:
            self._cap.set(self._cv2.CAP_PROP_POS_FRAMES, frame_idx)
            self._next = frame_idx
        while self._next <= frame_idx:
            ok, frame = self._cap.read()
            current = self._next
            self._next += 1
            if not ok:
                self._cache[current] = None
                return self._cache.get(frame_idx)
            if current != frame_idx:
                continue
            gray = _downscale_gray(self._cv2, frame, _FADE_ANALYSIS_SCALE)
            mean, std = self._cv2.meanStdDev(gray)
            self._cache[frame_idx] = (float(mean[0][0]) / 255.0, float(std[0][0]) / 255.0)
        return self._cache.get(frame_idx)


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


def _is_fade_frame(
    cv2: Any,
    cap: Any,
    frame_idx: int,
    *,
    dark: float,
    light: float,
    uniformity: float,
) -> bool:
    """True if the frame looks like a fade-to-black/white frame.

    A fade frame is near-uniform (low luminance std) AND near-black or
    near-white (extreme mean luminance). A dark scene with detail has a high
    std and is therefore NOT treated as a fade.
    """
    stats = _luminance_stats(cv2, cap, frame_idx)
    if stats is None:
        return False
    mean, std = stats
    return _fade_stats_test(mean, std, dark=dark, light=light, uniformity=uniformity)


def _pil_gray(frame: "Image.Image", max_dim: int) -> Any:
    """Return a downscaled grayscale array of a PIL frame.

    Cheap, cv2-free downscale used for luminance and near-duplicate
    comparisons on already-decoded frames (GIF sampling).
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


def _filter_low_interest_frames(
    frames: Sequence["Image.Image"],
    cfg: LowInterestFilterConfig,
) -> tuple["Image.Image", ...]:
    """Drop sampled frames with little interesting content.

    A frame is dropped when it is near-uniform AND dark (luminance std
    below `cfg.min_std` AND mean luminance below `cfg.min_luminance`) -
    i.e. a black / mostly-black empty frame, e.g. a fade-in/out that the
    fade padding or the fade filter missed. Dark scenes with real detail
    (high std) are preserved; bright frames are never dropped. Dropped
    frames are not replaced - the output simply has fewer frames.
    """
    kept: list["Image.Image"] = []

    for frame in frames:
        arr = _pil_gray(frame, 96).astype("float32")
        mean = float(arr.mean() / 255.0)
        std = float(arr.std() / 255.0)
        if mean < cfg.min_luminance and std < cfg.min_std:
            logger.debug(
                "filtered low-interest frame: mean_luminance=%.3f std=%.3f (min_luminance=%.3f min_std=%.3f)",
                mean,
                std,
                cfg.min_luminance,
                cfg.min_std,
            )
            continue
        kept.append(frame)
    return tuple(kept)


def _filter_fade_frames(
    indices: list[int],
    *,
    get_stats: Any,
    fps: float,
    total_frame_count: int,
    fades: FadeFilterConfig,
) -> list[int]:
    """Replace sampled frames that land inside or adjacent to fade transitions.

    Two cases:
    - Flat fade frames (extreme mean luminance AND low luminance std) are
      replaced unconditionally.
    - Dim-but-detailed frames (mean below replacement_min_brightness but with
      real detail - e.g. a sample placed inside a longer fade-in by the
      padding estimate) are nudged to brighter content only when a fade-like
      frame actually exists within search_seconds. A genuinely dark scene
      (no fade nearby) is preserved.

    Replacements are the nearest frame (within search_seconds, either
    direction) that is both not fade-like and at least
    replacement_min_brightness bright; if none meets the target, the
    brightest non-fade frame in the window is used. Flat fade frames whose
    whole window is fade-like (e.g. a long fade-out plus trailing black) get
    an extended search past the window - bounded by
    min(total_frame_count, search_seconds*10) - but only when at least one
    sampled frame is non-fade, so an entirely black video still keeps its
    originals. When nothing qualifies the original is kept so the caller
    never ends up with an empty selection.

    All stats lookups happen in ascending frame order and windows are scanned
    front to back, so a forward-decoding `get_stats` (see `_VideoFrameStats`)
    never has to seek backwards. Scanning order does not affect the result:
    "nearest" is chosen by distance, not by visit order.

    `get_stats(frame_idx)` returns (mean, std) of luminance for a frame, or
    None if it cannot be decoded.
    """
    search_window = max(1, int(fades.search_seconds * fps))
    probe_stride = max(1, search_window // 8)
    # Safety cap for the extended search: fade-like stretches (fade-out tail +
    # trailing black) longer than ~10x the window are treated as content.
    extended_limit = min(total_frame_count, search_window * 10)

    def fade_stats(idx: int) -> tuple[float, float] | None:
        return get_stats(idx)

    def is_fade_stats(stats: tuple[float, float] | None) -> bool:
        if stats is None:
            return False
        mean, std = stats
        return _fade_stats_test(
            mean, std, dark=fades.dark_threshold, light=fades.light_threshold, uniformity=fades.uniformity
        )

    def window_has_fade(idx: int) -> bool:
        """Cheap strided check for any fade-like frame in the window.

        Probes offsets {1, 1+stride, ...} (stride = search_window // 8, i.e.
        ~0.25s of frames), both directions. Fade-like frames come in runs
        (the flat middle of a fade spans many frames), so a fade is detected
        unless it is shorter than `stride` frames AND sits entirely between
        probe offsets. A false negative only ever preserves a dim frame that
        would have been nudged - never keeps a flat fade (those bypass this
        probe) and never invents a replacement.
        """
        probes = {
            cand
            for step in range(1, search_window + 1, probe_stride)
            for cand in (idx - step, idx + step)
            if 0 <= cand < total_frame_count
        }
        return any(is_fade_stats(fade_stats(cand)) for cand in sorted(probes))

    def find_replacement(
        idx: int,
        orig_mean: float,
        orig_fade: bool,
        *,
        unbounded: bool = False,
    ) -> tuple[int | None, bool]:
        """Nearest qualifying frame (or brightest non-fade fallback, seeded
        with the original so dark scenes are kept), plus whether any fade-like
        frame exists anywhere in the searched range. The searched range is the
        full window so `saw_fade` is not affected by scan order; with
        unbounded=True the search extends past the window (bounded by
        extended_limit) to escape long fade-like stretches."""
        limit = extended_limit if unbounded else search_window
        lo = max(0, idx - limit)
        hi = min(total_frame_count - 1, idx + limit)
        best: tuple[float, int] | None = None if orig_fade else (orig_mean, idx)
        nearest: tuple[int, int] | None = None  # (distance, frame)
        saw_fade = False
        for cand in range(lo, hi + 1):
            if cand == idx:
                continue
            stats = fade_stats(cand)
            if stats is None:
                continue
            mean, std = stats
            if _fade_stats_test(
                mean, std, dark=fades.dark_threshold, light=fades.light_threshold, uniformity=fades.uniformity
            ):
                saw_fade = True
                continue  # candidate is itself fade-like
            if best is None or mean > best[0]:
                best = (mean, cand)
            if mean >= fades.replacement_min_brightness:
                distance = abs(cand - idx)
                if nearest is None or distance < nearest[0]:
                    nearest = (distance, cand)
        replacement = nearest[1] if nearest is not None else (best[1] if best is not None else None)
        return replacement, saw_fade

    # Classify samples up front so we know whether any sampled frame is
    # non-fade: flat fade frames whose window is entirely fade-like (long
    # fade-out / trailing black) are then allowed to search further. Note
    # this is computed from the sampled frames only - a video whose samples
    # all land in black won't trigger the extended search even if content
    # exists elsewhere (conservative by design).
    sample_stats: list[tuple[float, float] | None] = []
    sample_is_fade: list[bool] = []
    sample_too_dim: list[bool] = []
    has_content = False
    for idx in sorted(set(indices)):
        stats = fade_stats(idx)
        sample_stats.append(stats)
        sample_is_fade.append(is_fade_stats(stats))
        sample_too_dim.append(stats is not None and stats[0] < fades.replacement_min_brightness)
        if stats is not None and not sample_is_fade[-1]:
            has_content = True

    by_index = {idx: pos for pos, idx in enumerate(sorted(set(indices)))}
    result: list[int] = []
    for idx in indices:
        pos = by_index[idx]
        stats = sample_stats[pos]
        if stats is None:
            result.append(idx)
            continue
        mean, _ = stats
        is_fade = sample_is_fade[pos]
        too_dim = sample_too_dim[pos]

        if is_fade:
            replacement, _ = find_replacement(idx, mean, orig_fade=True)
            if replacement is None and has_content:
                # The whole search window is fade-like (long fade-out and/or
                # trailing black): keep looking past it so a pure black frame
                # is never kept when real content exists elsewhere in the video.
                replacement, _ = find_replacement(idx, mean, orig_fade=True, unbounded=True)
            if replacement is not None:
                result.append(replacement)
                continue
        elif too_dim:
            # Only nudge dim-but-detailed frames when a fade is actually
            # nearby; a dark scene with no fade neighbours is preserved.
            # The cheap strided probe avoids the full window scan for the
            # common dark-scene case.
            if not window_has_fade(idx):
                result.append(idx)
                continue
            replacement, saw_fade = find_replacement(idx, mean, orig_fade=False)
            if saw_fade and replacement is not None:
                result.append(replacement)
                continue
        result.append(idx)
    return result


# region Scan sampling (dense probe, dead-frame and duplicate aware)


class _VideoFrameReader:
    """Decodes video frames on demand, walking forward when it can.

    Random seeking is expensive on containers without a frame index (WebM:
    OpenCV re-decodes from the start of the file), so this reader decodes
    forward over small gaps and only seeks for backward or distant requests.
    Callers request frames in ascending order.
    """

    def __init__(
        self,
        cv2: Any,
        cap: Any,
        *,
        total_frame_count: int,
        max_forward_gap: int,
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


def _scan_keep_budget(
    *,
    content_duration: float,
    padding_duration: float,
    max_frames: int,
    max_frames_at_seconds: float,
    include_edges: bool,
) -> int:
    """How many probed frames the scan sampler may keep for this clip.

    Mirrors the uniform sampler's duration scaling so the scan sampler is a
    drop-in replacement with the same inference cost: short clips get a
    single center frame plus edges, longer clips scale up to ``max_frames``.
    """
    budget = _sample_count(content_duration, max_frames, max_frames_at_seconds)
    keep = max(1, budget)
    if include_edges:
        keep += 2
    if padding_duration * 2 < content_duration and budget > 1:
        keep += 2
    return min(keep, max_frames + 4)


def _scan_candidate_indices(
    *,
    fps: float,
    total_frame_count: int,
    padding_duration: float,
    content_duration: float,
    max_frames: int,
    include_edges: bool,
) -> list[int]:
    """Frame indices for the dense probe grid, ascending and deduplicated.

    The grid is three times the output budget (bounded to 48 probes, and to
    20 on long files where every probe costs a seek), spread over the content
    window so every part of the clip has a chance to be represented. With
    ``include_edges`` the true first and last frames are added as probes too.
    """
    if content_duration <= 0 or total_frame_count <= 0:
        center = max(0, (total_frame_count - 1) // 2)
        if include_edges and total_frame_count > 1:
            return sorted({0, center, total_frame_count - 1})
        return [center]

    long_file = total_frame_count > 900
    ceiling = 20 if long_file else 48
    count = max(8, min(ceiling, 3 * max_frames))
    step = content_duration / (count + 1)
    indices = [int((padding_duration + step * (i + 1)) * fps) for i in range(count)]
    if include_edges:
        indices = [0, *indices, total_frame_count - 1]
    return sorted({max(0, min(total_frame_count - 1, idx)) for idx in indices})


def _select_scan_frames(
    cv2: Any,
    reader: _VideoFrameReader,
    candidates: list[int],
    *,
    total_frame_count: int,
    max_frames: int,
    loop_detected: bool,
    fades: FadeFilterConfig | None,
    low_interest: LowInterestFilterConfig | None,
    dedupe: bool,
    dedupe_threshold: float,
    dedupe_change_level: int,
    dedupe_max_changed_fraction: float,
) -> list["Image.Image"]:
    """Pick visually distinct, non-dead frames from the probe grid.

    A candidate is dropped when it is a fade frame, a flat dark frame
    (low_interest), or a near-duplicate of an already-chosen frame. Because
    the grid already covers the clip evenly, the first candidate of each
    visually distinct phase wins and the rest of that phase is skipped, which
    is exactly "one frame per distinct moment". Edge candidates are skipped
    when the clip was detected as a loop (the last frame repeats the first).
    """
    kept: list[Image.Image] = []
    accepted_grays: list[Any] = []
    for idx in candidates:
        if loop_detected and idx in (0, total_frame_count - 1):
            continue
        frame = reader.frame(idx)
        if frame is None:
            continue
        gray = _downscale_gray(cv2, frame, _FADE_ANALYSIS_SCALE)
        mean, std = cv2.meanStdDev(gray)
        mean = float(mean[0][0]) / 255.0
        std = float(std[0][0]) / 255.0
        if fades is not None and _fade_stats_test(
            mean,
            std,
            dark=fades.dark_threshold,
            light=fades.light_threshold,
            uniformity=fades.uniformity,
        ):
            logger.debug("scan: dropping fade frame at index %s", idx)
            continue
        if (
            low_interest is not None
            and low_interest.enabled
            and mean < low_interest.min_luminance
            and std < low_interest.min_std
        ):
            logger.debug("scan: dropping low-interest frame at index %s", idx)
            continue
        if dedupe and _is_near_duplicate(
            gray,
            accepted_grays,
            dedupe_threshold,
            dedupe_change_level,
            dedupe_max_changed_fraction,
        ):
            continue
        kept.append(_to_pil(cv2, frame))
        accepted_grays.append(gray)
        if len(kept) >= max_frames:
            break
    return kept


def _extract_scan(
    cap: Any,
    *,
    fps: float,
    total_frame_count: int,
    max_frames: int,
    max_frames_at_seconds: float,
    include_edges: bool,
    detect_loops: bool,
    fades: FadeFilterConfig | None,
    low_interest: LowInterestFilterConfig | None,
    dedupe: bool,
    dedupe_threshold: float,
    dedupe_change_level: int,
    dedupe_max_changed_fraction: float,
) -> tuple["Image.Image", ...]:
    """Content-aware sampler: probe densely once, keep distinct live frames."""
    cv2 = _load_cv2()
    total_duration = total_frame_count / fps
    padding_duration, content_start, content_end = _content_window(
        total_duration, fps, max_frames_at_seconds
    )
    content_duration = max(0.0, content_end - content_start)
    candidates = _scan_candidate_indices(
        fps=fps,
        total_frame_count=total_frame_count,
        padding_duration=padding_duration,
        content_duration=content_duration,
        max_frames=max_frames,
        include_edges=include_edges,
    )
    keep_budget = _scan_keep_budget(
        content_duration=content_duration,
        padding_duration=padding_duration,
        max_frames=max_frames,
        max_frames_at_seconds=max_frames_at_seconds,
        include_edges=include_edges,
    )
    reader = _VideoFrameReader(
        cv2,
        cap,
        total_frame_count=total_frame_count,
        # Long files: seeks are cheaper than decoding every frame in between.
        max_forward_gap=0 if total_frame_count > 900 else 120,
    )
    loop_detected = False
    if include_edges and detect_loops and len(candidates) >= 3:
        first = reader.frame(candidates[0])
        last = reader.frame(candidates[-1])
        if first is not None and last is not None:
            try:
                loop_detected = _frames_match(
                    _downscale_gray(cv2, first, _FADE_ANALYSIS_SCALE),
                    _downscale_gray(cv2, last, _FADE_ANALYSIS_SCALE),
                    threshold=dedupe_threshold,
                    change_level=dedupe_change_level,
                    max_fraction=max(_LOOP_MIN_CHANGED_FRACTION, dedupe_max_changed_fraction),
                )
            except Exception as exc:  # detection is best-effort
                logger.debug("scan: loop detection failed: %s", exc)

    frames = _select_scan_frames(
        cv2,
        reader,
        candidates,
        total_frame_count=total_frame_count,
        max_frames=keep_budget,
        loop_detected=loop_detected,
        fades=fades,
        low_interest=low_interest,
        dedupe=dedupe,
        dedupe_threshold=dedupe_threshold,
        dedupe_change_level=dedupe_change_level,
        dedupe_max_changed_fraction=dedupe_max_changed_fraction,
    )
    logger.debug(
        "extract_frames(scan) fps=%s frames=%s duration=%.2f probes=%s kept=%s loop=%s",
        fps,
        total_frame_count,
        total_duration,
        len(candidates),
        len(frames),
        loop_detected,
    )
    return tuple(frames)


def _extract_gif_scan(
    gif: "Image.Image",
    *,
    durations: list[float],
    max_frames: int,
    max_frames_at_seconds: float,
    include_edges: bool,
    detect_loops: bool,
    fades: FadeFilterConfig | None,
    low_interest: LowInterestFilterConfig | None,
    dedupe: bool,
    dedupe_threshold: float,
    dedupe_change_level: int,
    dedupe_max_changed_fraction: float,
) -> tuple["Image.Image", ...]:
    """Content-aware GIF sampler: one dense probe pass over the frames."""
    import bisect

    total_frame_count = len(durations)
    total_duration = sum(durations)
    if total_duration <= 0:
        total_duration = max(0.1, 0.1 * total_frame_count)
    fps = total_frame_count / total_duration

    starts: list[float] = []
    elapsed = 0.0
    for duration in durations:
        starts.append(elapsed)
        elapsed += duration

    def index_at(timestamp: float) -> int:
        """Frame index whose display interval contains ``timestamp``."""
        if total_frame_count == 0:
            return 0
        position = bisect.bisect_right(starts, timestamp) - 1
        return max(0, min(total_frame_count - 1, position))

    padding_duration, content_start, content_end = _content_window(
        total_duration, fps, max_frames_at_seconds
    )
    content_duration = max(0.0, content_end - content_start)
    keep_budget = _scan_keep_budget(
        content_duration=content_duration,
        padding_duration=padding_duration,
        max_frames=max_frames,
        max_frames_at_seconds=max_frames_at_seconds,
        include_edges=include_edges,
    )
    if content_duration <= 0:
        center = max(0, (total_frame_count - 1) // 2)
        candidates = sorted({0, center, total_frame_count - 1}) if include_edges and total_frame_count > 1 else [center]
    else:
        long_file = total_frame_count > 900
        ceiling = 20 if long_file else 48
        count = max(8, min(ceiling, 3 * max_frames))
        step = content_duration / (count + 1)
        candidates = [
            index_at(padding_duration + step * (i + 1)) for i in range(count)
        ]
        if include_edges:
            candidates = [0, *candidates, total_frame_count - 1]
        candidates = sorted(set(candidates))

    loop_detected = False
    if include_edges and detect_loops and len(candidates) >= 3:
        try:
            gif.seek(candidates[0])
            first = _pil_gray(gif.convert("RGB"), _FADE_ANALYSIS_SCALE)
            gif.seek(candidates[-1])
            last = _pil_gray(gif.convert("RGB"), _FADE_ANALYSIS_SCALE)
            loop_detected = _frames_match(
                first,
                last,
                threshold=dedupe_threshold,
                change_level=dedupe_change_level,
                max_fraction=max(_LOOP_MIN_CHANGED_FRACTION, dedupe_max_changed_fraction),
            )
        except Exception as exc:  # detection is best-effort
            logger.debug("scan: GIF loop detection failed: %s", exc)

    kept: list[Image.Image] = []
    accepted_grays: list[Any] = []
    for idx in candidates:
        if loop_detected and idx in (0, total_frame_count - 1):
            continue
        try:
            gif.seek(idx)
            frame = gif.convert("RGB").copy()
        except (EOFError, OSError, ValueError):
            continue
        gray = _pil_gray(frame, _FADE_ANALYSIS_SCALE)
        arr = gray.astype("float32")
        mean = float(arr.mean() / 255.0)
        std = float(arr.std() / 255.0)
        if fades is not None and _fade_stats_test(
            mean,
            std,
            dark=fades.dark_threshold,
            light=fades.light_threshold,
            uniformity=fades.uniformity,
        ):
            continue
        if (
            low_interest is not None
            and low_interest.enabled
            and mean < low_interest.min_luminance
            and std < low_interest.min_std
        ):
            continue
        if dedupe and _is_near_duplicate(
            gray,
            accepted_grays,
            dedupe_threshold,
            dedupe_change_level,
            dedupe_max_changed_fraction,
        ):
            continue
        kept.append(frame)
        accepted_grays.append(gray)
        if len(kept) >= keep_budget:
            break

    logger.debug(
        "extract_gif_frames(scan) frames=%s duration=%.2f probes=%s kept=%s loop=%s",
        total_frame_count,
        total_duration,
        len(candidates),
        len(kept),
        loop_detected,
    )
    return tuple(kept)


# endregion


# region Uniform sampling (reference strategy)


def _sample_count(content_duration: float, max_frames: int, max_frames_at_seconds: float) -> int:
    """Number of content samples for a duration, based on the content window."""
    if content_duration < max_frames_at_seconds:
        return int(content_duration / max_frames_at_seconds * max_frames)  # can be 0
    return max_frames  # assumes sane padding values


def _uniform_timestamps(
    *,
    total_duration: float,
    fps: float,
    num_frames_to_extract: int,
    padding_duration: float,
    content_duration: float,
    include_edges: bool,
    center_out: bool = False,
) -> tuple[float, ...]:
    """Generate timestamps to extract frames at.

    Rough format: (first frame, after padding, content, before padding, last frame).

    When content duration < minimum interval, paddings are skipped and
    content is a single frame at duration / 2.

    With ``center_out`` the timestamps are ordered from the middle of the
    content window outward, so near-duplicate removal keeps central frames
    first (used for clips assumed to loop).
    """
    start: list[float] = []
    end: list[float] = []
    content: list[float] = []

    first_frame = 0.0
    middle_frame = total_duration / 2 - 1 / fps
    last_frame: float = total_duration - 1 / fps

    num_of_slices = num_frames_to_extract + 1
    slice_duration = content_duration / num_of_slices

    if num_frames_to_extract <= 1:  # content_duration < min interval * 2
        content = [middle_frame]
    else:
        content = [padding_duration + float((slice_duration) * c) for c in range(1, num_of_slices)]

    if include_edges:
        start = [first_frame] + start
        end = end + [last_frame]

    if padding_duration * 2 < content_duration and num_frames_to_extract > 1:
        start = start + [padding_duration]
        end = [total_duration - padding_duration] + end

    timestamps = start + content + end
    if center_out:
        center = padding_duration + content_duration / 2
        timestamps = sorted(timestamps, key=lambda timestamp: abs(timestamp - center))

    return tuple(timestamps)


def _extract_uniform(
    cap: Any,
    *,
    fps: float,
    total_frame_count: int,
    max_frames: int,
    max_frames_at_seconds: float,
    include_edges: bool,
    center_out: bool = False,
    fades: FadeFilterConfig | None = None,
    dedupe: bool = True,
    dedupe_threshold: float = 0.03,
    dedupe_change_level: int = 25,
    dedupe_max_changed_fraction: float = 0.01,
) -> tuple["Image.Image", ...]:
    cv2 = _load_cv2()
    total_duration: float = total_frame_count / fps

    padding_duration, content_start, content_end = _content_window(
        total_duration, fps, max_frames_at_seconds
    )
    content_duration = content_end - content_start

    num_frames_to_extract = _sample_count(content_duration, max_frames, max_frames_at_seconds)

    timestamps = _uniform_timestamps(
        total_duration=total_duration,
        fps=fps,
        num_frames_to_extract=num_frames_to_extract,
        padding_duration=padding_duration,
        content_duration=content_duration,
        include_edges=include_edges,
        center_out=center_out,
    )

    logger.debug(
        "extract_frames(uniform) fps=%s frames=%s duration=%.2f samples=%s",
        fps,
        total_frame_count,
        total_duration,
        len(timestamps),
    )

    selected = [int(timestamp * fps) for timestamp in timestamps]
    if fades is not None:
        # Forward-decoding lookup: window scans walk frames front to back, so
        # the whole check costs a couple of sequential passes instead of a
        # seek per probed frame (which is catastrophic on WebM).
        stats_reader = _VideoFrameStats(cv2, cap, total_frame_count=total_frame_count)
        selected = _filter_fade_frames(
            selected,
            get_stats=stats_reader.get,
            fps=fps,
            total_frame_count=total_frame_count,
            fades=fades,
        )
    if dedupe:
        # Drop near-duplicates - the main defence against looping animations
        # (the same loop phase sampled at different timestamps) and static
        # segments wasting the budget. First occurrence wins, so a loop's
        # end frame duplicating its start frame collapses to one.
        selected = _dedupe_near_duplicates(
            cv2, cap, selected,
            threshold=dedupe_threshold,
            change_level=dedupe_change_level,
            max_fraction=dedupe_max_changed_fraction,
            scale=_FADE_ANALYSIS_SCALE,
        )
    selected = sorted(set(selected))

    frames: list[Image.Image] = []
    for frame_idx in selected:
        frame = _read_frame(cv2, cap, frame_idx)
        # Some codecs don't like rendering certain frames. If it's not
        # renderable it's probably similar to a previous frame anyway, so
        # we just skip it rather than stepping back.
        if frame is not None:
            frames.append(_to_pil(cv2, frame))

    return tuple(frames)


# endregion


def _is_near_duplicate(
    gray: Any,
    accepted_grays: list[Any],
    threshold: float,
    change_level: int,
    max_fraction: float,
) -> bool:
    """Return True if gray is near-identical to any already-accepted frame.

    Two tests must both pass:
    - the mean abs diff is below `threshold` (normalized), and
    - the fraction of pixels differing by more than `change_level` (8-bit
      gray levels) is below `max_fraction`. The fraction test is
      area-independent: a small but important change (a character, a face,
      water movement) in a mostly static frame changes a measurable fraction
      of pixels by a lot and is therefore kept, while a true loop repeat /
      static segment changes ~0% of pixels by any amount and is deduped.

    Callers precompute the grays once so the comparison is O(n) decodes,
    not O(n^2). A max_fraction <= 0 disables dedupe (no frame ever counts
    as a duplicate).
    """
    import numpy as np

    if max_fraction <= 0:
        return False
    for other_gray in accepted_grays:
        diff = np.abs(gray.astype(np.int16) - other_gray.astype(np.int16))
        if float(diff.mean()) / 255.0 >= threshold:
            continue
        changed = float((diff > change_level).mean())
        if changed < max_fraction:
            return True
    return False


def _dedupe_near_duplicates(
    cv2: Any,
    cap: Any,
    indices: list[int],
    *,
    threshold: float,
    change_level: int,
    max_fraction: float,
    scale: int,
) -> list[int]:
    """Greedy near-duplicate removal preserving order.

    Each entry is kept only if it is NOT a near-duplicate of any already-kept
    entry (see _is_near_duplicate). Processing order matters: the first
    occurrence of a near-identical group is kept and later ones dropped - so
    a loop's end frame duplicating its start frame (and the include_edges
    first/last frames) collapses to one. Frames that fail to decode are
    dropped.

    This is the main defence against looping animations (the same loop phase
    sampled at different timestamps) and long static segments: near-identical
    frames only burn inference budget once.
    """
    accepted: list[int] = []
    accepted_grays: list[Any] = []
    for idx in indices:
        frame = _read_frame(cv2, cap, idx)
        if frame is None:
            continue
        gray = _downscale_gray(cv2, frame, scale)
        if _is_near_duplicate(gray, accepted_grays, threshold, change_level, max_fraction):
            continue
        accepted.append(idx)
        accepted_grays.append(gray)
    return accepted
