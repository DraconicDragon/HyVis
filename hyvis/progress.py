"""
progress.py Single-line in-place progress display.

Usage:
    progress = Progress(total=5000)
    progress.tick(processed=1)          # success
    progress.tick(errors=1)             # failure
    progress.tick(skipped=1)            # cache skip
    progress.print_message("→ abc123: 42 tags")   # print above progress bar
    progress.finish()                   # print final newline
"""

from __future__ import annotations

import sys
import time


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

    _CLEAR_WIDTH = 120  # maximum expected terminal width for clearing

    def __init__(self, total: int) -> None:
        self._total = max(total, 1)
        self._processed = 0  # successful
        self._errors = 0
        self._skipped = 0
        self._start = time.monotonic()
        self._last_len = 0  # length of the last rendered line (for clearing)
        self._last_file_hash = ""
        self._last_model_id = ""
        self._last_tag_count = 0

    # region Public interface
    def tick(
        self,
        *,
        processed: int = 0,
        errors: int = 0,
        skipped: int = 0,
    ) -> None:
        """Increment counters and redraw the progress line."""
        self._processed += processed
        self._errors += errors
        self._skipped += skipped
        self._redraw()

    def set_last_file_info(self, file_hash: str, model_id: str, tag_count: int) -> None:
        """Record and display the last processed file in the progress line."""
        self._last_file_hash = file_hash
        self._last_model_id = model_id
        self._last_tag_count = tag_count
        self._redraw()

    def print_message(self, msg: str) -> None:
        """
        Print `msg` on its own line, then redraw the progress line below.

        Call this instead of print() so the progress line stays at the bottom.
        """
        line = self._render()
        # Erase current progress line, print the message, reprint progress.
        clear = " " * self._last_len
        sys.stdout.write(f"\r{clear}\r{msg}\n\r{line}")
        self._last_len = len(line)
        sys.stdout.flush()

    def finish(self) -> None:
        """Finalise: print one last newline so the next output starts cleanly."""
        sys.stdout.write("\n")
        sys.stdout.flush()

    # region Rendering
    def _done(self) -> int:
        return self._processed + self._errors + self._skipped

    def _render(self) -> str:
        elapsed = time.monotonic() - self._start
        done = self._done()
        pct = done / self._total * 100.0

        # Throughput: files processed OK per second (not counting skips/errors
        # against throughput since they take near-zero time).
        active_done = self._processed + self._errors
        rate = active_done / max(elapsed, 0.1)

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
        # Pad or trim to overwrite any longer previous line.
        pad = max(self._last_len - len(line), 0)
        sys.stdout.write(f"\r{line}{' ' * pad}")
        self._last_len = len(line)
        sys.stdout.flush()

    # region Summary

    def summary(self) -> str:
        elapsed = time.monotonic() - self._start
        _done = self._done()
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
