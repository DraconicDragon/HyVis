"""Small development-only controls embedded in the main window."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QWidget


class DevToolsStrip(QWidget):
    """Quick access to dialogs and recovery testing while developing the GUI."""

    def __init__(
        self,
        *,
        open_preferences: Callable[[], None],
        open_about: Callable[[], None],
        open_launch: Callable[[], None],
        open_unsaved_changes: Callable[[], None],
        simulate_crash: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._open_preferences = open_preferences
        self._open_about = open_about
        self._open_launch = open_launch
        self._open_unsaved_changes = open_unsaved_changes
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(6)

        layout.addWidget(QLabel("DEV", self))
        self.preferences_button = self._add_button(layout, "Preferences", self._on_preferences_clicked)
        self.about_button = self._add_button(layout, "About", self._on_about_clicked)
        self.launch_button = self._add_button(layout, "Launch dialog", self._on_launch_clicked)
        self.launch_button.setToolTip(
            "Open using the current editor config. Unsaved configs use config.toml in the command preview."
        )
        self.unsaved_button = self._add_button(layout, "Unsaved changes", self._on_unsaved_changes_clicked)
        layout.addStretch(1)
        self.crash_button = self._add_button(layout, "Simulate crash", simulate_crash)
        self.crash_button.setToolTip(
            "Save the current editor state, then exit abruptly. Start the GUI without a config file to test recovery."
        )

    @staticmethod
    def _add_button(layout: QHBoxLayout, text: str, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def _on_launch_clicked(self) -> None:
        """Open the launch dialog directly so dev use does not require a prior save."""
        window = self._window_for(self._open_launch)
        state = getattr(window, "state", None)
        if window is None or state is None:
            self._open_launch()
            return

        config_path = state.current_path or Path("config.toml")
        dialog_class = self._reload_dialog_class("launch_dialog", "LaunchDialog")
        dialog_class(state.config, config_path, window).exec()

    def _on_preferences_clicked(self) -> None:
        window = self._window_for(self._open_preferences)
        if window is None or not hasattr(window, "gui_settings"):
            self._open_preferences()
            return

        dialog_class = self._reload_dialog_class("preferences_dialog", "PreferencesDialog")
        dialog = dialog_class(window.gui_settings, window)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            window.gui_settings = dialog.settings

    def _on_about_clicked(self) -> None:
        window = self._window_for(self._open_about)
        if window is None:
            self._open_about()
            return

        dialog_class = self._reload_dialog_class("about_dialog", "AboutDialog")
        dialog_class(window).exec()

    def _on_unsaved_changes_clicked(self) -> None:
        window = self._window_for(self._open_unsaved_changes)
        if window is None or not hasattr(window, "state"):
            self._open_unsaved_changes()
            return

        dialog_class = self._reload_dialog_class("unsaved_changes_dialog", "UnsavedChangesDialog")
        dialog_class(current_path=window.state.current_path, parent=window).exec()

    @staticmethod
    def _window_for(callback: Callable[[], None]) -> QWidget | None:
        window = getattr(callback, "__self__", None)
        return window if isinstance(window, QWidget) else None

    @staticmethod
    def _reload_dialog_class(module_stem: str, class_name: str):
        """Reload dialog source before returning its class, including same-second edits."""
        module_name = f"hyvis.gui.dialogs.{module_stem}"
        module = importlib.import_module(module_name)
        importlib.invalidate_caches()

        # Python's bytecode cache uses file size and whole-second mtime; clear it so
        # rapid same-size edits are always picked up by this development shortcut.
        cached_path = getattr(module, "__cached__", None)
        if cached_path:
            Path(cached_path).unlink(missing_ok=True)

        module = importlib.reload(module)
        return getattr(module, class_name)
