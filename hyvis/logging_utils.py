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
    def format(self, record):
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


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter("%(asctime)s  %(levelname)s %(name)s  %(message)s", datefmt="%H:%M:%S"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    target_level = getattr(logging, level.upper(), logging.WARNING)
    root.setLevel(target_level)

    # Keep vibe aligned with hyvis
    logging.getLogger("vibe").setLevel(target_level)
    logging.getLogger("vibe_result_transforms").setLevel(target_level)

    # Silence noisy third-party libraries even in DEBUG mode
    logging.getLogger("PIL").setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.INFO)
    # logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
