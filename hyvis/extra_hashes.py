"""
extra_hashes.py Optional ingestion of hashes supplied via file through CLI input.
"""

from __future__ import annotations

from pathlib import Path


def load_extra_hashes(path: Path) -> list[str]:
    """
    Read one sha256 hash per line from a text file.

    Blank lines and comment lines beginning with '#' are ignored.
    Duplicate hashes are removed while preserving first-seen order.
    """
    hashes: list[str] = []
    seen: set[str] = set()

    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw_line in enumerate(fh, start=1):
            value = raw_line.strip()
            if not value or value.startswith("#"):
                continue
            if len(value) != 64:
                raise ValueError(f"{path}:{line_no}: expected a 64-character sha256 hash, got {value!r}")
            try:
                int(value, 16)
            except ValueError as exc:
                raise ValueError(f"{path}:{line_no}: invalid sha256 hash {value!r}") from exc

            if value not in seen:
                seen.add(value)
                hashes.append(value)

    return hashes
