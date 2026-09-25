"""
app.py — Desktop GUI entry point for HyVis.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def parse_gui_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="hyvis-gui",
        description="Desktop graphical configuration tool for HyVis.",
    )
    parser.add_argument(
        "config",
        nargs="?",
        default=None,
        type=Path,
        help="Optional path to an existing TOML configuration file to open.",
    )
    return parser.parse_args()


def run_gui(initial_config: Path | str | None = None) -> int:
    """Initialize and run the desktop PySide6 application."""

    # On Linux/BSD, tell Qt to use the modern native system file chooser via XDG Desktop Portal
    if sys.platform.startswith("linux") or "bsd" in sys.platform:
        os.environ["QT_QPA_PLATFORMTHEME"] = "xdgdesktopportal"

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        from hyvis.gui.main_window import MainWindow
        from hyvis.gui.state import ConfigState
    except ImportError:
        print(
            "ERROR: PySide6 is required to run the HyVis desktop interface.\n"
            "Install it with: pip install 'hyvis[gui]' or pip install PySide6",
            file=sys.stderr,
        )
        return 1

    # Enable high-DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setApplicationName("HyVis")
    app.setOrganizationName("Drac")

    state = ConfigState()
    if initial_config:
        ok, err = state.load_from_file(initial_config)
        if not ok and err:
            print(f"Warning: Could not open '{initial_config}': {err}", file=sys.stderr)

    window = MainWindow(state)
    window.show()

    return app.exec()


def cli() -> None:
    args = parse_gui_args()
    sys.exit(run_gui(args.config))


if __name__ == "__main__":
    cli()
