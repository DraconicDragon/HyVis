"""
test_video_extractor.py — Integration tests for Pillow animated image and PyAV video extraction.
Synthesizes media containers in tmp_path to verify animated frames, rotation, seeking, and start_time offsets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image

from hyvis.video.extractor import (
    _extract_video_seek,
    _extract_video_sequential,
    build_video_plan,
    extract_frames,
    is_animated_image,
    probe_media,
)
from hyvis.video.planner import FramePlanConfig, plan_frames


def test_static_png_vs_animated_gif_and_webp(tmp_path: Path) -> None:
    # 1. Static PNG
    static_path = tmp_path / "static.png"
    Image.new("RGB", (64, 64), (200, 10, 10)).save(static_path)

    assert is_animated_image(static_path) is False
    probe_static = probe_media(static_path)
    assert probe_static.kind == "image"
    assert probe_static.total_frames == 1

    # 2. Animated GIF (10 distinct colors)
    gif_path = tmp_path / "anim.gif"
    gif_frames = [Image.new("RGB", (64, 64), (i * 25, 255 - i * 25, 100)) for i in range(10)]
    gif_frames[0].save(gif_path, save_all=True, append_images=gif_frames[1:], duration=100, loop=0)

    assert is_animated_image(gif_path) is True
    res_gif = extract_frames(gif_path)
    assert res_gif.probe.kind == "animated_image"
    assert res_gif.probe.total_frames == 10
    assert res_gif.strategy == "pillow_animated"
    assert len(res_gif.frames) == res_gif.plan.target_count >= 4

    # Verify extracted RGB colors actually correspond to their planned source indices
    for frame in res_gif.frames:
        assert frame.source_index is not None
        r, _g, _b = frame.image.getpixel((32, 32))  # ty:ignore[not-iterable]
        assert abs(r - frame.source_index * 25) <= 4

    # 3. Animated WebP (8 frames)
    webp_path = tmp_path / "anim.webp"
    webp_frames = [Image.new("RGB", (64, 64), (10, i * 30, 200)) for i in range(8)]
    webp_frames[0].save(webp_path, save_all=True, append_images=webp_frames[1:], duration=80, loop=0)

    assert is_animated_image(webp_path) is True
    res_webp = extract_frames(webp_path)
    assert res_webp.probe.kind == "animated_image"
    assert res_webp.probe.total_frames == 8
    assert len(res_webp.frames) >= 4


def _write_test_video(
    path: Path,
    *,
    num_frames: int = 48,
    fps: int = 24,
    rotation_ccw: int = 0,
) -> None:
    """Generate a clean synthetic video using canonical PyAV mpeg4 encoding."""
    av = pytest.importorskip("av")

    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width = 64
        stream.height = 64
        stream.pix_fmt = "yuv420p"

        if rotation_ccw != 0:
            if hasattr(stream, "set_display_rotation"):
                stream.set_display_rotation(rotation_ccw)
            elif hasattr(stream, "metadata"):
                stream.metadata["rotate"] = str(rotation_ccw)

        for _ in range(num_frames):
            arr = np.zeros((64, 64, 3), dtype=np.uint8)
            # Top half red, bottom half blue (so rotation is unambiguously testable)
            arr[:32, :, 0] = 240
            arr[32:, :, 2] = 240

            vframe = av.VideoFrame.from_ndarray(arr, format="rgb24")
            for packet in stream.encode(vframe):
                container.mux(packet)

        for packet in stream.encode():
            container.mux(packet)


def test_pyav_sequential_and_seek(tmp_path: Path) -> None:
    pytest.importorskip("av")
    mp4_path = tmp_path / "clip.mp4"
    _write_test_video(mp4_path, num_frames=48, fps=24)

    assert is_animated_image(mp4_path) is False
    probe = probe_media(mp4_path)
    assert probe.kind == "video"
    assert probe.duration_s is not None and 1.8 <= probe.duration_s <= 2.1

    # 1. Default sequential extraction
    cfg = FramePlanConfig(min_frames=4, max_frames=8)
    res_seq = extract_frames(mp4_path, config=cfg)
    assert res_seq.strategy == "pyav_sequential"
    assert len(res_seq.frames) == 4
    assert all(0.0 <= (f.timestamp_s or 0.0) <= 2.1 for f in res_seq.frames)

    # 2. Direct seek extraction test
    plan = plan_frames(probe.duration_s, probe.total_frames, config=cfg)
    seek_frames = list(_extract_video_seek(probe, plan))
    assert len(seek_frames) == 4


def test_pyav_handles_nonzero_start_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that when a video container has a non-zero start_time, timestamps are relative to 0.0s."""
    av = pytest.importorskip("av")
    mp4_path = tmp_path / "clip_offset.mp4"
    _write_test_video(mp4_path, num_frames=24, fps=24)

    probe = probe_media(mp4_path)
    plan = plan_frames(probe.duration_s, probe.total_frames, config=FramePlanConfig(min_frames=2, max_frames=4))

    original_av_open = av.open

    class _FrameProxy:
        def __init__(self, frame: Any, offset_s: float) -> None:
            self._frame = frame
            self._offset_s = offset_s

        @property
        def time(self) -> float | None:
            t = self._frame.time
            return (t + self._offset_s) if t is not None else None

        def __getattr__(self, name: str) -> Any:
            return getattr(self._frame, name)

    class _StreamProxy:
        def __init__(self, real_stream: Any, start_ticks: int) -> None:
            self._real = real_stream
            self.start_time = start_ticks

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

    class _ContainerProxy:
        def __init__(self, real_container: Any, offset_s: float = 10.0) -> None:
            self._real = real_container
            self._offset_s = offset_s
            real_v = real_container.streams.video[0]
            start_ticks = int(offset_s / float(real_v.time_base))
            self._mock_stream = _StreamProxy(real_v, start_ticks)

            self.streams = MagicMock()
            self.streams.video = [self._mock_stream]

        def decode(self, *args: Any, **kwargs: Any) -> Any:
            for frame in self._real.decode(self._real.streams.video[0]):
                yield _FrameProxy(frame, self._offset_s)

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args) -> None:
            self._real.close()

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

    monkeypatch.setattr(av, "open", lambda *args, **kwargs: _ContainerProxy(original_av_open(*args, **kwargs)))

    frames = list(_extract_video_sequential(probe, plan))
    assert len(frames) >= 2
    # Frame timestamps must be normalized to start near 0.0s, not 10.0s
    assert all(0.0 <= (f.timestamp_s or 0.0) <= 2.5 for f in frames)


def test_pyav_applies_display_rotation(tmp_path: Path) -> None:
    pytest.importorskip("av")
    mp4_rot = tmp_path / "rotated_90.mp4"
    # Coded image has RED on top, BLUE on bottom; 90° CCW rotation puts RED on left, BLUE on right.
    _write_test_video(mp4_rot, num_frames=12, fps=12, rotation_ccw=90)

    res = extract_frames(mp4_rot, config=FramePlanConfig(min_frames=2, max_frames=4))
    img = res.frames[0].image

    left_pixel = img.getpixel((8, 32))
    right_pixel = img.getpixel((56, 32))

    # Left side must be red-dominant, right side must be blue-dominant
    assert left_pixel[0] > 180 and left_pixel[2] < 60  # ty:ignore[not-subscriptable]
    assert right_pixel[2] > 180 and right_pixel[0] < 60  # ty:ignore[not-subscriptable]


def test_pyav_mid_seek_error_falls_back_sequential(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a mid-video seek() FFmpegError resumes sequentially without dropping remaining frames."""
    av = pytest.importorskip("av")
    mp4_path = tmp_path / "mid_seek_err.mp4"
    _write_test_video(mp4_path, num_frames=48, fps=24)

    probe = probe_media(mp4_path)
    plan = plan_frames(probe.duration_s, probe.total_frames, config=FramePlanConfig(min_frames=4, max_frames=8))
    assert len(plan.times_s) == 4

    original_av_open = av.open
    seek_calls = 0

    class _SeekFailContainerProxy:
        def __init__(self, real_container: Any) -> None:
            self._real = real_container

        def seek(self, *args: Any, **kwargs: Any) -> Any:
            nonlocal seek_calls
            seek_calls += 1
            if seek_calls >= 2:
                raise av.FFmpegError(-1, "Simulated mid-video seek failure")
            return self._real.seek(*args, **kwargs)

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            self._real.close()

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

    monkeypatch.setattr(av, "open", lambda *args, **kwargs: _SeekFailContainerProxy(original_av_open(*args, **kwargs)))

    frames = list(_extract_video_seek(probe, plan))
    assert len(frames) == 4
    assert [f.index for f in frames] == [0, 1, 2, 3]


def _write_multi_gop_video(path: Path, *, num_frames: int = 40, fps: int = 20, gop_size: int = 10) -> None:
    """Create a synthetic H.264/MPEG4 video with forced periodic keyframes every `gop_size` frames."""
    av = pytest.importorskip("av")
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width = 64
        stream.height = 64
        stream.pix_fmt = "yuv420p"
        stream.codec_context.gop_size = gop_size

        for i in range(num_frames):
            arr = np.full((64, 64, 3), (i * 5) % 250, dtype=np.uint8)
            vframe = av.VideoFrame.from_ndarray(arr, format="rgb24")
            for pkt in stream.encode(vframe):
                container.mux(pkt)
        for pkt in stream.encode():
            container.mux(pkt)


def test_pyav_keyframe_extraction_and_thinning(tmp_path: Path) -> None:
    """Verify keyframes are merged alongside uniform samples and thinned to max_keyframes."""
    pytest.importorskip("av")
    mp4_path = tmp_path / "multi_gop.mp4"
    _write_multi_gop_video(mp4_path, num_frames=40, fps=20, gop_size=10)  # ~4 keyframes (0, 10, 20, 30)

    cfg = FramePlanConfig(min_frames=4, max_frames=4)
    res_unif = extract_frames(mp4_path, config=cfg, extract_keyframes=False)
    assert res_unif.plan.keyframe_count == 0
    assert len(res_unif.frames) == 4

    res_kf = extract_frames(mp4_path, config=cfg, extract_keyframes=True, max_keyframes=2)
    assert res_kf.plan.keyframe_count == 2
    assert res_kf.plan.target_count == 6
    assert len(res_kf.frames) == 6


def test_keyframe_extraction_skips_intra_only_and_animations(tmp_path: Path) -> None:
    """Intra-only codecs (MJPEG) and animated images must skip keyframe scanning cleanly."""
    av = pytest.importorskip("av")

    # 1. MJPEG (intra-only) video
    mjpeg_path = tmp_path / "intra.avi"
    with av.open(str(mjpeg_path), mode="w") as container:
        stream = container.add_stream("mjpeg", rate=10)
        stream.width = 64
        stream.height = 64
        stream.pix_fmt = "yuvj420p"
        for _ in range(12):
            arr = np.full((64, 64, 3), 120, dtype=np.uint8)
            vframe = av.VideoFrame.from_ndarray(arr, format="rgb24")
            for pkt in stream.encode(vframe):
                container.mux(pkt)
        for pkt in stream.encode():
            container.mux(pkt)

    probe_mjpeg = probe_media(mjpeg_path)
    plan_mjpeg = build_video_plan(
        probe_mjpeg,
        FramePlanConfig(min_frames=4, max_frames=4),
        extract_keyframes=True,
        max_keyframes=32,
    )
    assert plan_mjpeg.keyframe_count == 0
    assert plan_mjpeg.target_count == 4

    # 2. Animated GIF
    gif_path = tmp_path / "anim.gif"
    gif_frames = [Image.new("RGB", (32, 32), (i * 30, 10, 10)) for i in range(6)]
    gif_frames[0].save(gif_path, save_all=True, append_images=gif_frames[1:], duration=100, loop=0)

    probe_gif = probe_media(gif_path)
    plan_gif = build_video_plan(
        probe_gif,
        FramePlanConfig(min_frames=4, max_frames=8),
        extract_keyframes=True,
        max_keyframes=32,
    )
    assert plan_gif.keyframe_count == 0


def test_keyframe_missing_pts_falls_back_gracefully(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing packet PTS must skip keyframe extraction without failing uniform extraction."""
    av = pytest.importorskip("av")
    mp4_path = tmp_path / "no_pts_kf.mp4"
    _write_multi_gop_video(mp4_path, num_frames=24, fps=24, gop_size=8)

    probe = probe_media(mp4_path)
    orig_open = av.open

    class _NoPtsPktProxy:
        def __init__(self, pkt: Any) -> None:
            self._pkt = pkt
            self.pts = None

        def __getattr__(self, name: str) -> Any:
            return getattr(self._pkt, name)

    class _NoPtsContainerProxy:
        def __init__(self, real: Any) -> None:
            self._real = real

        def demux(self, *args: Any, **kwargs: Any) -> Any:
            for pkt in self._real.demux(*args, **kwargs):
                yield _NoPtsPktProxy(pkt)

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            self._real.close()

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

    monkeypatch.setattr(av, "open", lambda *args, **kwargs: _NoPtsContainerProxy(orig_open(*args, **kwargs)))

    plan = build_video_plan(probe, FramePlanConfig(min_frames=4, max_frames=4), extract_keyframes=True)
    assert plan.keyframe_count == 0
    assert plan.target_count == 4
