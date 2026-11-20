from __future__ import annotations

import logging
import sys
import threading
from typing import Any

logger = logging.getLogger(__name__)


def start_cancel_listener(
    session: Any,
    model_id: str,
) -> tuple[threading.Thread | None, threading.Event]:
    stop_event = threading.Event()

    if not sys.stdin.isatty():
        return None, stop_event

    # Use platform-appropriate method
    if sys.platform == "win32":
        return _start_cancel_listener_windows(session, model_id, stop_event)
    else:
        return _start_cancel_listener_unix(session, model_id, stop_event)


def _start_cancel_listener_unix(
    session: Any,
    model_id: str,
    stop_event: threading.Event,
) -> tuple[threading.Thread | None, threading.Event]:
    import termios
    import tty

    def _listen() -> None:
        fd = sys.stdin.fileno()
        try:
            old_attrs = termios.tcgetattr(fd)
        except Exception:
            return

        try:
            tty.setcbreak(fd)
            while not stop_event.is_set():
                char = sys.stdin.read(1)
                if stop_event.is_set():
                    break
                if char in ("q", "Q"):
                    logger.warning("Cancel requested for model %s ('q' key)", model_id)
                    session.cancel_current_inference()
                    break
        except Exception:
            logger.debug("Cancel listener error for model %s", model_id, exc_info=True)
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
            except Exception:
                pass

    thread = threading.Thread(target=_listen, name="cancel-key-listener", daemon=True)
    thread.start()
    return thread, stop_event


def _start_cancel_listener_windows(
    session: Any,
    model_id: str,
    stop_event: threading.Event,
) -> tuple[threading.Thread | None, threading.Event]:
    import msvcrt

    def _listen() -> None:
        try:
            while not stop_event.is_set():
                if msvcrt.kbhit():  # ty:ignore[unresolved-attribute]
                    char = msvcrt.getch().decode("utf-8", errors="ignore")  # ty:ignore[unresolved-attribute]
                    if char in ("q", "Q"):
                        logger.warning("Cancel requested for model %s ('q' key)", model_id)
                        session.cancel_current_inference()
                        break
                # Small sleep to avoid busy-waiting
                stop_event.wait(0.1)
        except Exception:
            logger.debug("Cancel listener error for model %s", model_id, exc_info=True)

    thread = threading.Thread(target=_listen, name="cancel-key-listener", daemon=True)
    thread.start()
    return thread, stop_event
