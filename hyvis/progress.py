"""
progress.py Single-line in-place progress display.
"""

from __future__ import annotations

import sys
import time
from collections import deque


def _fmt_duration(seconds: float) -> str:
    """Format seconds as HH:MM:SS (or MM:SS for < 1 hour)."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class Progress:
    """
    Renders an updating single-line progress summary to stdout.

    Thread safety: not thread-safe. Call only from the async main loop.
    """

    def __init__(self, total: int) -> None:
        self._total = max(total, 1)
        self._processed = 0
        self._errors = 0
        self._skipped = 0
        self._start = time.monotonic()
        self._last_len = 0

        self._last_file_hash = ""
        self._last_model_id = ""
        self._last_tag_count = 0

        self._samples = deque()  # (timestamp, active_items)
        self._recent_active = 0
        self._window_seconds = 10.0

    def reset_start_time(self) -> None:
        """Reset the start timer to the current time (e.g. after model loading completes)."""
        self._start = time.monotonic()
        self._samples.clear()
        self._recent_active = 0

    def tick(self, *, processed: int = 0, errors: int = 0, skipped: int = 0) -> None:
        delta_active = processed + errors
        now = time.monotonic()

        if delta_active > 0:
            self._samples.append((now, delta_active))
            self._recent_active += delta_active

            cutoff = now - self._window_seconds
            while self._samples and self._samples[0][0] < cutoff:
                _, n = self._samples.popleft()
                self._recent_active -= n

        self._processed += processed
        self._errors += errors
        self._skipped += skipped
        self._redraw()

    def _rate(self) -> float:
        if not self._samples:
            return 0.0

        now = time.monotonic()
        window_start = self._samples[0][0]
        elapsed = now - window_start

        if elapsed < 2.0:
            total_elapsed = now - self._start
            if total_elapsed <= 0.1:
                return 0.0
            return (self._processed + self._errors) / total_elapsed

        return self._recent_active / elapsed

    def set_last_file_info(self, file_hash: str, model_id: str, tag_count: int) -> None:
        """Record and display the last processed file in the progress line."""
        self._last_file_hash = file_hash
        self._last_model_id = model_id
        self._last_tag_count = tag_count
        self._redraw()

    def print_message(self, msg: str) -> None:
        """
        Print `msg` on its own line, then redraw the progress line below.
        """
        line = self._render()
        clear = " " * self._last_len
        sys.stdout.write(f"\r{clear}\r{msg}\n\r{line}")
        self._last_len = len(line)
        sys.stdout.flush()

    def finish(self) -> None:
        """Finalise: print one last newline so the next output starts cleanly."""
        sys.stdout.write("\n")
        sys.stdout.flush()

    # region Rendering
    def _render(self) -> str:
        elapsed = time.monotonic() - self._start
        done = self._processed + self._errors + self._skipped
        pct = done / self._total * 100.0

        rate = self._rate()
        remaining = max(self._total - done, 0)
        eta_secs = remaining / rate if rate > 0.01 else 0.0

        parts = [
            f"{done}/{self._total} ({pct:.1f}%)",
            f"{rate:.1f} f/s",
            f"Elapsed: {_fmt_duration(elapsed)}",
            f"ETA: {_fmt_duration(eta_secs)}",
        ]

        if self._errors:
            parts.append(f"✗ {self._errors} error{'s' if self._errors != 1 else ''}")
        if self._skipped:
            parts.append(f"↷ {self._skipped} cached")
        if self._processed > 0 and self._last_file_hash:
            short_hash = self._last_file_hash[:8]
            parts.append(f"Last: {short_hash} - {self._last_model_id} - {self._last_tag_count} tags")

        return " │ ".join(parts)

    def _redraw(self) -> None:
        line = self._render()
        pad = max(self._last_len - len(line), 0)
        sys.stdout.write(f"\r{line}{' ' * pad}")
        self._last_len = len(line)
        sys.stdout.flush()

    def summary(self) -> str:
        elapsed = time.monotonic() - self._start
        active = self._processed + self._errors
        avg_rate = active / max(elapsed, 0.1)
        lines = [
            f"  Total files     : {self._total}",
            f"  Processed OK    : {self._processed}",
            f"  Errors          : {self._errors}",
            f"  Skipped (cache) : {self._skipped}",
            f"  Elapsed time    : {_fmt_duration(elapsed)}",
            f"  Avg throughput  : {avg_rate:.1f} files/s",
        ]
        return "\n".join(lines)
