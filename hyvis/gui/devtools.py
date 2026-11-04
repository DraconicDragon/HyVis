"""Small development-only controls embedded in the main window."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QWidget

from hyvis.gui.theme import BUILTIN_THEMES, theme_manager


class DevToolsStrip(QWidget):
    """Quick access to dialogs, themes, and recovery testing while developing the GUI."""

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
        self._last_custom_hex: str | None = None

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

        # Theme Switcher
        layout.addWidget(QLabel("Theme:", self))
        self.theme_combo = QComboBox(self)
        self.theme_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for theme_id, preset in BUILTIN_THEMES.items():
            self.theme_combo.addItem(preset.display_name, userData=theme_id)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        layout.addWidget(self.theme_combo)

        # Accent Color Picker
        layout.addWidget(QLabel("Accent:", self))
        self.accent_combo = QComboBox(self)
        self.accent_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.accent_combo.addItem("System Default", userData=None)
        self.accent_combo.addItem("Custom...", userData="custom")
        self.accent_combo.insertSeparator(2)

        catppuccin_colors = {
            "Rosewater": "#dc8a78",
            "Flamingo": "#dd7878",
            "Pink": "#ea76cb",
            "Mauve": "#8839ef",
            "Red": "#d20f39",
            "Maroon": "#e64553",
            "Peach": "#fe640b",
            "Yellow": "#df8e1d",
            "Green": "#40a02b",
            "Teal": "#179299",
            "Sky": "#04a5e5",
            "Sapphire": "#209fb5",
            "Blue": "#1e66f5",
            "Lavender": "#7287fd",
        }
        for name, hex_val in catppuccin_colors.items():
            self.accent_combo.addItem(name, userData=hex_val)

        # Use activated instead of currentIndexChanged so re-selecting Custom... always prompts
        self.accent_combo.activated.connect(self._on_accent_activated)
        layout.addWidget(self.accent_combo)

        layout.addStretch(1)
        self.crash_button = self._add_button(layout, "Simulate crash", simulate_crash)
        self.crash_button.setToolTip(
            "Save the current editor state, then exit abruptly. Start the GUI without a config file to test recovery."
        )

    def _on_theme_changed(self) -> None:
        theme_id = self.theme_combo.currentData()
        if theme_id:
            theme_manager.set_theme(str(theme_id))

    def _on_accent_activated(self, index: int) -> None:
        val = self.accent_combo.itemData(index)

        if val == "custom":
            initial = QColor(self._last_custom_hex) if self._last_custom_hex else QColor()
            color = QColorDialog.getColor(initial=initial, parent=self, title="Select Custom Accent Color")

            if color.isValid():
                hex_val = color.name()
                self._last_custom_hex = hex_val
                self.accent_combo.setItemText(1, f"Custom ({hex_val})...")
                theme_manager.set_accent(hex_val)
            else:
                # If user cancelled and no custom color was ever picked, revert to System Default
                if not self._last_custom_hex:
                    self.accent_combo.setCurrentIndex(0)
                    theme_manager.set_accent(None)
        else:
            # Revert the "Custom..." label if switching away
            if self._last_custom_hex:
                self.accent_combo.setItemText(1, "Custom...")
            theme_manager.set_accent(val)

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
