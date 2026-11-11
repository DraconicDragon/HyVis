"""
logging_utils.py — Unified console and timestamped file logging engine with run rotation.
"""

from __future__ import annotations

import logging
import shlex
import sys
from datetime import datetime
from pathlib import Path

from hyvis.cli import get_version

logger = logging.getLogger("hyvis")

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
DIM = "\033[2m"
MAGENTA = "\033[95m"


def _c(text: str, *codes: str) -> str:
    return "".join(codes) + text + RESET


LEVEL_COLORS = {
    logging.DEBUG: (DIM,),
    logging.INFO: (),
    logging.WARNING: (YELLOW,),
    logging.ERROR: (RED,),
    logging.CRITICAL: (RED, BOLD),
}


def colorize_level(text: str, level: int) -> str:
    return _c(text, *LEVEL_COLORS.get(level, ()))


class ColorFormatter(logging.Formatter):
    """Terminal formatter applying ANSI colors based on log severity."""

    def format(self, record: logging.LogRecord) -> str:
        orig_msg = record.msg
        orig_lvl = record.levelname
        orig_name = record.name

        record.levelname = colorize_level(f"{orig_lvl:<8}", record.levelno)
        record.name = colorize_level(orig_name, record.levelno)
        record.msg = colorize_level(str(orig_msg), record.levelno)

        formatted = super().format(record)

        record.msg = orig_msg
        record.levelname = orig_lvl
        record.name = orig_name
        return formatted


def prune_old_logs(log_dir: Path, max_files: int) -> None:
    """Retain only the newest (max_files - 1) run logs so the new run doesn't exceed the cap."""
    try:
        if not log_dir.is_dir():
            return

        # Scan for existing hyvis_*.log files, sorted newest first
        existing_logs = sorted(
            log_dir.glob("hyvis_*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        # Leave room for the 1 new log about to be created
        cutoff = max(0, max_files - 1)
        for old_log in existing_logs[cutoff:]:
            try:
                old_log.unlink()
            except OSError:
                pass
    except Exception as exc:
        logger.debug("Failed during log pruning: %s", exc)


def setup_logging(
    log_level: str = "WARNING",
    *,
    log_to_file: bool = True,
    log_dir: str | Path = "logs",
    max_log_files: int = 20,
    config_path: Path | str | None = None,
) -> Path | None:
    """
    Returns the Path to the active run log file if file logging is enabled, else None.
    """
    target_level = getattr(logging, log_level.upper(), logging.WARNING)
    root = logging.getLogger()

    # Clear existing handlers to prevent duplicate output on re-configuration
    for handler in list(root.handlers):
        root.removeHandler(handler)

    # 1. Console Handler: strictly WARNING & ERROR (never DEBUG or INFO to protect terminal progress)
    # If target_level is ERROR or higher, console also rises to ERROR (excluding warnings)
    console_level = max(logging.WARNING, target_level)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(ColorFormatter("%(asctime)s  %(levelname)s %(name)s  %(message)s", datefmt="%H:%M:%S"))
    root.addHandler(console_handler)

    active_log_file: Path | None = None

    # 2. File Handler
    if target_level <= logging.DEBUG:
        file_level = logging.DEBUG
    elif target_level <= logging.WARNING:
        file_level = logging.INFO
    else:
        file_level = target_level  # ERROR or CRITICAL

    if log_to_file:
        try:
            target_dir = Path(log_dir).resolve()
            target_dir.mkdir(parents=True, exist_ok=True)

            prune_old_logs(target_dir, max_log_files)

            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            active_log_file = target_dir / f"hyvis_{timestamp}.log"

            version_str = get_version()

            cmd_invocation = shlex.join(sys.argv)

            # Write run metadata header
            with active_log_file.open("w", encoding="utf-8") as fh:
                fh.write(f"=== HyVis Run Log - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                fh.write(f"HyVis Version: {version_str}\n")
                fh.write(f"Command:       {cmd_invocation}\n")
                if config_path:
                    fh.write(f"Config:        {Path(config_path).resolve()}\n")
                fh.write(f"Platform:      {sys.platform} | Python: {sys.version.split()[0]}\n")
                fh.write("=" * 55 + "\n\n")

            file_handler = logging.FileHandler(active_log_file, mode="a", encoding="utf-8")
            file_handler.setLevel(file_level)
            file_handler.setFormatter(
                logging.Formatter(
                    fmt="%(asctime)s [%(levelname)-7s] (%(name)s:%(lineno)d): %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            root.addHandler(file_handler)

            # Root logger must allow the most verbose handler through
            root.setLevel(min(console_level, file_level))
        except Exception as exc:
            logger.warning("Could not initialize file logging to '%s': %s", log_dir, exc)
            root.setLevel(console_level)
    else:
        root.setLevel(console_level)

    # Keep vibe aligned with hyvis
    logging.getLogger("vibe").setLevel(root.level)
    logging.getLogger("vibe_result_transforms").setLevel(root.level)

    # Silence noisy third-party libraries even in DEBUG mode
    logging.getLogger("PIL").setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.INFO)
    # logging.getLogger("huggingface_hub").setLevel(logging.WARNING)

    return active_log_file
