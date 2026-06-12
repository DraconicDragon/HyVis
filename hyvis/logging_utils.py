from __future__ import annotations

import logging

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


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: DIM,
        logging.INFO: None,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: RED + BOLD,
    }

    def format(self, record):
        color: str | None = self.COLORS.get(record.levelno)

        # Save original values
        orig_msg = record.msg
        orig_lvl = record.levelname
        orig_name = record.name

        # Apply color to all parts if not INFO
        if color:
            record.levelname = f"{color}{orig_lvl:<8}{RESET}"
            record.name = f"{color}{orig_name}{RESET}"
            record.msg = f"{color}{orig_msg}{RESET}"
        else:
            record.levelname = f"{orig_lvl:<8}"

        formatted = super().format(record)

        # Restore values
        record.msg = orig_msg
        record.levelname = orig_lvl
        record.name = orig_name
        return formatted


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter("%(asctime)s  %(levelname)s %(name)s  %(message)s", datefmt="%H:%M:%S"))

    logging.basicConfig(level=logging.WARNING, handlers=[handler])
    logging.getLogger().setLevel(getattr(logging, level.upper()))
    logging.getLogger("vibe").setLevel(getattr(logging, level.upper()))
