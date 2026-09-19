"""
launcher.py — Detached terminal process runner and clipboard command helper.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def build_cli_command(config_path: Path | str, extra_args: list[str] | None = None) -> list[str]:
    """Return the canonical command arguments list to execute HyVis."""
    cmd = [sys.executable, "-m", "hyvis", str(config_path)]
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def format_cli_command_str(config_path: Path | str, extra_args: list[str] | None = None) -> str:
    """Format the execution command as a shell-friendly copyable string."""
    # Use 'hyvis' alias if running from installed package, otherwise sys.executable
    executable = "hyvis" if shutil.which("hyvis") else f'"{sys.executable}" -m hyvis'
    args_str = f" {' '.join(extra_args)}" if extra_args else ""
    return f'{executable} "{config_path}"{args_str}'


def launch_in_external_terminal(
    config_path: Path | str,
    extra_args: list[str] | None = None,
) -> bool:
    """
    Launch HyVis in a detached, independent terminal window.

    Returns True if successfully launched, False if terminal detection failed.
    """
    cmd_list = build_cli_command(config_path, extra_args)

    # 1. Windows: cmd.exe with 'start' so it spawns in a new window and keeps it open (/k)
    if sys.platform == "win32":
        try:
            formatted_args = " ".join(f'"{arg}"' if " " in arg else arg for arg in cmd_list)
            # /k keeps window open after completion so user can review the summary
            shell_cmd = f'start "HyVis Execution" cmd.exe /k {formatted_args}'
            subprocess.Popen(shell_cmd, shell=True)
            return True
        except Exception as exc:
            logger.error("Failed to spawn Windows terminal: %s", exc)
            return False

    # 2. Linux / BSD: Detect common terminal emulators
    terminals = [
        "x-terminal-emulator",
        "gnome-terminal",
        "konsole",
        "xfce4-terminal",
        "alacritty",
        "kitty",
        "foot",
        "xterm",
    ]

    detected_term = None
    for term in terminals:
        if shutil.which(term):
            detected_term = term
            break

    if detected_term is not None:
        try:
            # Construct standard terminal arguments
            if detected_term in ("gnome-terminal", "xfce4-terminal"):
                term_cmd = [detected_term, "--", *cmd_list]
            elif detected_term == "konsole":
                term_cmd = [detected_term, "-e", *cmd_list]
            else:
                # Standard POSIX -e flag for alacritty, kitty, xterm, x-terminal-emulator
                term_cmd = [detected_term, "-e", *cmd_list]

            # start_new_session=True fully detaches child from the GUI process
            subprocess.Popen(term_cmd, start_new_session=True)
            return True
        except Exception as exc:
            logger.error("Failed to spawn Linux terminal '%s': %s", detected_term, exc)
            return False

    # 3. macOS: Launch Terminal.app via osascript
    if sys.platform == "darwin":
        try:
            cmd_str = " ".join(f'\\"{arg}\\"' if " " in arg else arg for arg in cmd_list)
            script = f'tell application "Terminal" to do script "{cmd_str}"'
            subprocess.Popen(["osascript", "-e", script], start_new_session=True)
            return True
        except Exception as exc:
            logger.error("Failed to spawn macOS Terminal: %s", exc)
            return False

    return False
