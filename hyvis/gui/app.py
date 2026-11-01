"""
app.py — Desktop GUI entry point for HyVis.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from hyvis.gui.hot_restart import is_restart_child, run_with_restart
from hyvis.gui.theme import MODERN_SCROLLBAR_STYLE, CheckBoxCursorFilter, HyVisAppStyle

# just append stylesheet strings here with +
_GLOBAL_APP_STYLE = MODERN_SCROLLBAR_STYLE


def parse_gui_args(argv: list[str] | None = None) -> argparse.Namespace:
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
    parser.add_argument(
        "--hot-restart",
        action="store_true",
        help="For development: restart the GUI after its window is closed.",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Show development controls in the UI.",
    )
    return parser.parse_args(argv)


def run_gui(initial_config: Path | str | None = None, *, dev_mode: bool = False) -> int:
    """Initialize and run the desktop PySide6 application."""

    # On Linux/BSD, tell Qt to use the modern native system file chooser via XDG Desktop Portal
    if sys.platform.startswith("linux") or "bsd" in sys.platform:
        os.environ["QT_QPA_PLATFORMTHEME"] = "xdgdesktopportal"
        os.environ["QT_LOGGING_RULES"] = "qt.qpa.services=false"  # suppress QPA service warnings on Wayland

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        from hyvis.gui.main_window import MainWindow
        from hyvis.gui.settings import StartupBehavior, get_gui_settings, has_session_tmp
        from hyvis.gui.state import ConfigState
    except ImportError as exc:
        print(
            f"Error importing required modules: {exc}",
            file=sys.stderr,
        )
        return 1

    # Enable high-DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setApplicationName("HyVis")
    app.setOrganizationName("Drac")
    app.setDesktopFileName("hyvis-gui")

    app.setStyle(HyVisAppStyle(app.style()))
    app.setStyleSheet(_GLOBAL_APP_STYLE)
    _cursor_filter = CheckBoxCursorFilter(app)
    app.installEventFilter(_cursor_filter)

    state = ConfigState()
    gui_settings = get_gui_settings()

    # Resolve startup configuration hierarchy:
    # 1. CLI argument overrides everything
    # 2. Custom preset path from settings
    # 3. Last opened session config from settings
    # 4. Fallback: clean default template (already initialized in ConfigState)
    target_config = initial_config

    if not target_config and not has_session_tmp():
        if gui_settings.startup_behavior == StartupBehavior.CUSTOM_PRESET and gui_settings.custom_preset_path:
            preset_path = Path(gui_settings.custom_preset_path)
            if preset_path.is_file():
                target_config = preset_path
        elif gui_settings.startup_behavior == StartupBehavior.LAST_SESSION and gui_settings.last_opened_config:
            last_file = Path(gui_settings.last_opened_config)
            if last_file.is_file():
                target_config = last_file

    if target_config:
        ok, err = state.load_from_file(target_config)
        if not ok and err:
            print(f"Warning: Could not open '{target_config}': {err}", file=sys.stderr)

    window = MainWindow(state, dev_mode=dev_mode)
    window.show()

    return app.exec()


def cli() -> None:
    args = parse_gui_args()
    if is_restart_child():
        sys.exit(run_gui(args.config, dev_mode=args.dev))
    if args.hot_restart:
        sys.exit(run_with_restart(sys.argv[1:]))
    sys.exit(run_gui(args.config, dev_mode=args.dev))


if __name__ == "__main__":
    cli()
