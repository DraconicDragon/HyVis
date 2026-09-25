"""
launcher.py — Detached terminal process runner and clipboard command helper.
"""

from __future__ import annotations

import logging
import shlex
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
    Keeps the terminal window open on completion, failure, or crash so the user can review output.

    Returns True if successfully launched, False if terminal detection failed.
    """
    cmd_list = build_cli_command(config_path, extra_args)

    # 1. Windows: cmd.exe with 'start' and /k keeps the shell open after completion
    if sys.platform == "win32":
        try:
            formatted_args = " ".join(f'"{arg}"' if " " in arg else arg for arg in cmd_list)
            shell_cmd = f'start "HyVis Execution" cmd.exe /k {formatted_args}'
            subprocess.Popen(shell_cmd, shell=True)
            return True
        except Exception as exc:
            logger.error("Failed to spawn Windows terminal: %s", exc)
            return False

    # 2. Linux / BSD: Wrap command in a shell script that pauses before closing
    quoted_cmd = " ".join(shlex.quote(arg) for arg in cmd_list)
    shell_script = (
        f'{quoted_cmd}; code=$?; echo ""; '
        f'printf "\\033[1;33m[HyVis] Process finished (exit code $code). Press Enter to close...\\033[0m "; '
        f"read -r _"
    )

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
            if detected_term in ("gnome-terminal", "xfce4-terminal"):
                term_cmd = [detected_term, "--", "bash", "-c", shell_script]
            elif detected_term == "konsole":
                term_cmd = [detected_term, "-e", "bash", "-c", shell_script]
            else:
                term_cmd = [detected_term, "-e", "bash", "-c", shell_script]

            # start_new_session=True fully detaches child from the GUI process
            subprocess.Popen(term_cmd, start_new_session=True)
            return True
        except Exception as exc:
            logger.error("Failed to spawn Linux terminal '%s': %s", detected_term, exc)
            return False

    # 3. macOS: Launch Terminal.app via osascript with pause
    if sys.platform == "darwin":
        try:
            mac_script = (
                f'{quoted_cmd}; echo ""; '
                f'printf "\\033[1;33m[HyVis] Process finished. Press Enter to close...\\033[0m "; '
                f"read -r _"
            )
            escaped_script = mac_script.replace("\\", "\\\\").replace('"', '\\"')
            script = f'tell application "Terminal" to do script "{escaped_script}"'
            subprocess.Popen(["osascript", "-e", script], start_new_session=True)
            return True
        except Exception as exc:
            logger.error("Failed to spawn macOS Terminal: %s", exc)
            return False

    return False
