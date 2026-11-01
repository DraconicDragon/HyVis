"""Hot-restart the GUI in a fresh process after its window is closed."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Sequence

_RESTART_CHILD_ENV = "HYVIS_GUI_RESTART_CHILD"


class _StopRequested(Exception):
    """Raised when the supervisor receives a signal requesting shutdown."""


def is_restart_child() -> bool:
    """Return whether this process was started by the GUI restart supervisor."""
    return os.environ.get(_RESTART_CHILD_ENV) == "1"


def _stop_child(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_with_restart(argv: Sequence[str]) -> int:
    """Run the GUI in a child process and restart it after a normal close.

    The supervisor has no Qt event loop, so Ctrl+C can always interrupt it.
    """
    command = [sys.executable, "-m", "hyvis.gui.app", *argv]
    child_env = os.environ.copy()
    child_env[_RESTART_CHILD_ENV] = "1"
    previous_handlers = {
        signal.SIGINT: signal.getsignal(signal.SIGINT),
        signal.SIGTERM: signal.getsignal(signal.SIGTERM),
    }

    def request_stop(_signum: int, _frame: object) -> None:
        raise _StopRequested

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    child: subprocess.Popen[bytes] | None = None
    try:
        while True:
            child = subprocess.Popen(
                command,
                env=child_env,
                start_new_session=os.name != "nt",
            )
            try:
                return_code = child.wait()
            except (_StopRequested, KeyboardInterrupt):
                _stop_child(child)
                return 130

            if return_code != 0:
                return return_code

            print("[HyVis] GUI closed; restarting. Press Ctrl+C to stop.", file=sys.stderr)
    except _StopRequested:
        if child is not None:
            _stop_child(child)
        return 130
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
