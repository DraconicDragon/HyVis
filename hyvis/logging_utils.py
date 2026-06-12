from __future__ import annotations

import logging

_RESET = "\033[0m"
_BOLD = "\033[1m"
_GREEN = "\033[92m"
_CYAN = "\033[96m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_DIM = "\033[2m"
_MAGENTA = "\033[95m"


def _c(text: str, *codes: str) -> str:
    return "".join(codes) + text + _RESET


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: _DIM,
        logging.INFO: None,
        logging.WARNING: _YELLOW,
        logging.ERROR: _RED,
        logging.CRITICAL: _RED + _BOLD,
    }

    def format(self, record):
        color: str | None = self.COLORS.get(record.levelno)

        # Save original values
        orig_msg = record.msg
        orig_lvl = record.levelname
        orig_name = record.name

        # Apply color to all parts if not INFO
        if color:
            record.levelname = f"{color}{orig_lvl:<8}{_RESET}"
            record.name = f"{color}{orig_name}{_RESET}"
            record.msg = f"{color}{orig_msg}{_RESET}"
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