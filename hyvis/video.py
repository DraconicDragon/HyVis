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

    `get_stats(frame_idx)` returns (mean, std) of luminance for a frame, or
    None if it cannot be decoded. Callers supply a cheap implementation:
    the uniform sampler passes a caching seek-based lookup.
    """
    search_window = max(1, int(fades.search_seconds * fps))
    probe_stride = max(1, search_window // 8)
    # Safety cap for the extended search: fade-like stretches (fade-out tail +
    # trailing black) longer than ~10x the window are treated as content.
    extended_limit = min(total_frame_count, search_window * 10)

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
        for step in range(1, search_window + 1, probe_stride):
            for cand in (idx - step, idx + step):
                if not 0 <= cand < total_frame_count:
                    continue
                stats = get_stats(cand)
                if stats is None:
                    continue
                mean, std = stats
                if _fade_stats_test(
                    mean, std, dark=fades.dark_threshold, light=fades.light_threshold, uniformity=fades.uniformity
                ):
                    return True
        return False

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
        best: tuple[float, int] | None = None if orig_fade else (orig_mean, idx)
        nearest_qual: int | None = None  # first qualifying frame in outward order = nearest
        saw_fade = False
        for step in range(1, limit + 1):
            for cand in (idx - step, idx + step):
                if not 0 <= cand < total_frame_count:
                    continue
                stats = get_stats(cand)
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
                if nearest_qual is None and mean >= fades.replacement_min_brightness:
                    nearest_qual = cand
        replacement = nearest_qual if nearest_qual is not None else (best[1] if best is not None else None)
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
    for idx in indices:
        stats = get_stats(idx)
        sample_stats.append(stats)
        if stats is None:
            sample_is_fade.append(False)
            sample_too_dim.append(False)
            continue
        mean, std = stats
        is_fade = _fade_stats_test(
            mean, std, dark=fades.dark_threshold, light=fades.light_threshold, uniformity=fades.uniformity
        )
        sample_is_fade.append(is_fade)
        sample_too_dim.append(mean < fades.replacement_min_brightness)
        if not is_fade:
            has_content = True

    result: list[int] = []
    for pos, idx in enumerate(indices):
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
        # Caching seek-based lookup: repeated window lookups decode each frame
        # once, and only flagged samples trigger window scans, so the typical
        # cost is a handful of frame decodes (no full-video pass).
        _stats_cache: dict[int, tuple[float, float] | None] = {}

        def get_stats(idx: int) -> tuple[float, float] | None:
            if idx not in _stats_cache:
                _stats_cache[idx] = _luminance_stats(cv2, cap, idx)
            return _stats_cache[idx]

        selected = _filter_fade_frames(
            selected,
            get_stats=get_stats,
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
