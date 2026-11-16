"""
frame_cache.py — Optional disk-backed temporary frame cache for multi-model video runs.

Stores extracted raw frames as high-quality (Q97) JPEGs so multi-model pipelines don't
have to re-decode long videos for Model 2+. Each video's directory is deleted immediately
once all configured models finish inferring that file.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image

from hyvis.video.extractor import ExtractedFrame

if TYPE_CHECKING:
    from hyvis.config import AppConfig

logger = logging.getLogger(__name__)

_MANIFEST_NAME = "manifest.json"
_JPEG_QUALITY = 97


def resolve_frame_cache_dir(config: AppConfig, *, db_path: str | Path | None = None) -> Path:
    """Resolve the temporary frame disk cache directory."""
    if config.video.frame_cache_dir:
        p = Path(config.video.frame_cache_dir)
        return p if p.is_absolute() else (Path.cwd() / p)

    if db_path and str(db_path) != ":memory:":
        base = Path(db_path)
        if not base.is_absolute():
            base = Path.cwd() / base
        return base.parent / "frame_cache"

    return Path.cwd() / "data" / "frame_cache"


class FrameDiskCache:
    """Manages temporary extracted Q97 JPEG frames per file_hash on disk."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root = Path(root_dir)

    def _file_dir(self, file_hash: str) -> Path:
        return self.root / file_hash

    def has_complete_frames(self, file_hash: str, prep_hash: str) -> bool:
        """Return True only if a complete frame set matching `prep_hash` exists on disk."""
        f_dir = self._file_dir(file_hash)
        manifest_path = f_dir / _MANIFEST_NAME
        if not manifest_path.is_file():
            return False

        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if data.get("prep_hash") != prep_hash:
                return False
            entries = data.get("frames", [])
            if not entries:
                return False
            return all((f_dir / str(entry["file"])).is_file() for entry in entries)
        except Exception:
            return False

    def validate_frames(self, file_hash: str, prep_hash: str) -> bool:
        """
        Verify manifest, prep_hash, file presence, and full image decodability for all cached frames.
        Opens and closes one frame at a time (O(1) memory). If any frame is corrupt, invalidates
        the file's cache directory and returns False so the caller re-extracts cleanly.
        """
        if not self.has_complete_frames(file_hash, prep_hash):
            return False

        f_dir = self._file_dir(file_hash)
        try:
            data = json.loads((f_dir / _MANIFEST_NAME).read_text(encoding="utf-8"))
            for entry in data["frames"]:
                frame_path = f_dir / str(entry["file"])
                with Image.open(frame_path) as img:
                    img.load()
            return True
        except Exception as exc:
            logger.warning(
                "Corrupt cached frame detected for %s (%s); invalidating frame cache and re-extracting.",
                file_hash[:8],
                exc,
            )
            self.cleanup_file(file_hash)
            return False

    def load_frames(self, file_hash: str, prep_hash: str) -> Iterator[ExtractedFrame]:
        """Yield previously cached ExtractedFrames from disk (closing file handles immediately)."""
        f_dir = self._file_dir(file_hash)
        data: dict[str, Any] = json.loads((f_dir / _MANIFEST_NAME).read_text(encoding="utf-8"))
        if data.get("prep_hash") != prep_hash:
            raise ValueError(f"Frame cache prep_hash mismatch for {file_hash[:8]}")

        for entry in data["frames"]:
            frame_path = f_dir / str(entry["file"])
            with Image.open(frame_path) as img:
                img.load()
                rgb = img.convert("RGB") if img.mode != "RGB" else img.copy()

            yield ExtractedFrame(
                index=int(entry["index"]),
                source_index=entry.get("source_index"),
                timestamp_s=entry.get("timestamp_s"),
                image=rgb,
            )

    def save_and_yield_frames(
        self,
        file_hash: str,
        prep_hash: str,
        frames: Iterator[ExtractedFrame],
    ) -> Iterator[ExtractedFrame]:
        """
        Stream frames from `frames`, saving each as a Q97 JPEG to disk while yielding it.
        Writes manifest.json only upon full completion; cleans up partial folders on abort/error.
        """
        f_dir = self._file_dir(file_hash)
        if f_dir.exists():
            shutil.rmtree(f_dir, ignore_errors=True)
        f_dir.mkdir(parents=True, exist_ok=True)

        manifest_entries: list[dict[str, Any]] = []
        completed = False

        try:
            for frame in frames:
                fname = f"{frame.index:04d}.jpg"
                out_path = f_dir / fname
                rgb = frame.image.convert("RGB") if frame.image.mode != "RGB" else frame.image
                rgb.save(out_path, format="JPEG", quality=_JPEG_QUALITY)

                manifest_entries.append(
                    {
                        "file": fname,
                        "index": frame.index,
                        "source_index": frame.source_index,
                        "timestamp_s": frame.timestamp_s,
                    }
                )
                yield frame

            if manifest_entries:
                payload = {"prep_hash": prep_hash, "frames": manifest_entries}
                (f_dir / _MANIFEST_NAME).write_text(
                    json.dumps(payload, separators=(",", ":")),
                    encoding="utf-8",
                )
                completed = True
        finally:
            if not completed:
                shutil.rmtree(f_dir, ignore_errors=True)

    def cleanup_file(self, file_hash: str) -> None:
        """Delete cached extracted frames for one completed file."""
        f_dir = self._file_dir(file_hash)
        if f_dir.exists():
            shutil.rmtree(f_dir, ignore_errors=True)

    def clear_all(self) -> int:
        """Remove all cached frame directories and return count of removed file folders."""
        if not self.root.is_dir():
            return 0
        removed = 0
        for child in self.root.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        return removed
