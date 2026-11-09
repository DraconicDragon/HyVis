"""
manager.py — Runtime theme controller with instant, zero-delay synchronization.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from hyvis.gui.theme.colors import get_system_accent_color
from hyvis.gui.theme.compiler import render_surgical_stylesheet
from hyvis.gui.theme.presets import BUILTIN_THEMES
from hyvis.gui.theme.tokens import ThemeProperty, ThemeTokens

logger = logging.getLogger(__name__)


THEME_PROPERTY_NAMES = tuple(p.encode() for p in ThemeProperty)


# region Dynamic Repolish Mixin


class AutoRepolishMixin:
    """Automatically unpolishes and repolishes QSS when dynamic styling properties change."""

    _is_repolishing: bool = False

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.DynamicPropertyChange:
            prop_name = getattr(event, "propertyName", lambda: b"")()
            if prop_name in THEME_PROPERTY_NAMES and not getattr(self, "_is_repolishing", False):
                style = getattr(self, "style", lambda: None)()
                if style:
                    self._is_repolishing = True
                    try:
                        style.unpolish(self)
                        style.polish(self)
                        getattr(self, "update", lambda: None)()
                    finally:
                        self._is_repolishing = False

        return super().event(event)  # type: ignore


# endregion


# region Palette Event Filter


class _ApplicationPaletteFilter(QObject):
    """Listens exclusively to external OS palette changes on the application object."""

    def __init__(self, manager: ThemeManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = manager

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            if obj is QApplication.instance() and not self._manager._internal_palette_change:
                self._manager.refresh()
        return super().eventFilter(obj, event)


# endregion


# region Theme Manager


class ThemeManager(QObject):
    """Manages active theme presets and surgical QSS instantly."""

    theme_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._active_preset_id: str = "system"
        self._active_accent_hex: str | None = None
        self._tokens: ThemeTokens | None = None
        self._palette_filter: _ApplicationPaletteFilter | None = None
        self._system_palette: QPalette | None = None
        self._internal_palette_change: bool = False

    @property
    def current_tokens(self) -> ThemeTokens:
        if self._tokens is None:
            self._recalculate_tokens()
        assert self._tokens is not None
        return self._tokens

    def init_app(self, app: QApplication) -> None:
        """Initialize palette synchronization and event listeners."""
        self._system_palette = QPalette(app.palette())

        self._palette_filter = _ApplicationPaletteFilter(self, app)
        app.installEventFilter(self._palette_filter)

        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self.refresh)

        self._apply_theme_state()

    def set_theme(self, preset_id: str) -> None:
        """Apply a theme preset immediately."""
        if preset_id not in BUILTIN_THEMES:
            preset_id = "system"

        self._active_preset_id = preset_id
        self._apply_theme_state()

    def set_accent(self, accent_hex: str | None) -> None:
        """Override the accent color across both QPalette and ThemeTokens."""
        if self._active_accent_hex == accent_hex:
            return
        self._active_accent_hex = accent_hex
        self._apply_theme_state()

    def refresh(self) -> None:
        """Handle external OS palette updates."""
        app = QApplication.instance()
        if not app:
            return

        # If on system theme, capture the fresh OS palette
        if self._active_preset_id == "system" and not self._internal_palette_change:
            self._system_palette = QPalette(app.palette())

        self._apply_theme_state()

    def _apply_theme_state(self) -> None:
        """Unified pipeline: updates QPalette, recalculates tokens, and applies surgical QSS."""
        app = QApplication.instance()
        if not app:
            return

        preset = BUILTIN_THEMES.get(self._active_preset_id, BUILTIN_THEMES["system"])

        # 1. Resolve base palette (system vs custom preset)
        if self._active_preset_id == "system":
            base_pal = QPalette(self._system_palette or app.palette())
        else:
            custom_pal = preset.create_palette(self._system_palette or app.palette())
            base_pal = QPalette(custom_pal if custom_pal is not None else app.palette())

        # 2. Resolve effective accent: user selection -> system default accent
        effective_accent = self._active_accent_hex or get_system_accent_color(self._system_palette)

        # 3. Inject effective accent into QPalette so native inputs, selections, and focus rings update
        accent_col = QColor(effective_accent)
        base_pal.setColor(QPalette.ColorRole.Highlight, accent_col)

        accent_role = getattr(QPalette.ColorRole, "Accent", None)
        if accent_role is not None:
            base_pal.setColor(accent_role, accent_col)

        # High-contrast selection text
        contrast_col = QColor("#ffffff" if accent_col.lightnessF() < 0.65 else "#000000")
        base_pal.setColor(QPalette.ColorRole.HighlightedText, contrast_col)

        # 4. Apply the updated palette to QApplication
        self._internal_palette_change = True
        try:
            app.setPalette(base_pal)
        finally:
            self._internal_palette_change = False

        # 5. Generate tokens and apply surgical QSS
        old_tokens = self._tokens
        self._tokens = preset.create_tokens(base_pal, effective_accent)

        if old_tokens == self._tokens:
            return

        app.setStyleSheet(render_surgical_stylesheet(self._tokens))
        self.theme_changed.emit(self._tokens)

    def _recalculate_tokens(self) -> None:
        app = QApplication.instance()
        pal = app.palette() if app else QPalette()
        preset = BUILTIN_THEMES.get(self._active_preset_id, BUILTIN_THEMES["system"])
        effective_accent = self._active_accent_hex or get_system_accent_color(self._system_palette)
        self._tokens = preset.create_tokens(pal, effective_accent)


theme_manager = ThemeManager()

# endregion
