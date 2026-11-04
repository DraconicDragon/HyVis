"""
theme — Palette-driven theming, design tokens, and presets.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QCheckBox, QProxyStyle, QStyle, QStyleOption, QWidget

from hyvis.gui.theme.colors import (
    adjust_color_lightness,
    get_system_accent_color,
    hex_to_rgba,
    is_palette_dark,
)
from hyvis.gui.theme.compiler import render_surgical_stylesheet
from hyvis.gui.theme.manager import DynamicStyleMixin, ThemeManager, theme_manager
from hyvis.gui.theme.presets import BUILTIN_THEMES, ThemePreset
from hyvis.gui.theme.tokens import (
    ButtonSize,
    ButtonVariant,
    CardTheme,
    ThemeTokens,
    WidgetState,
)


class HyVisAppStyle(QProxyStyle):
    def pixelMetric(
        self,
        metric: QStyle.PixelMetric,
        option: QStyleOption | None = None,
        widget: QWidget | None = None,
    ) -> int:
        if metric in (QStyle.PixelMetric.PM_IndicatorWidth, QStyle.PixelMetric.PM_IndicatorHeight):
            return 17
        return super().pixelMetric(metric, option, widget)


class CheckBoxCursorFilter(QObject):
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Enter and isinstance(obj, QCheckBox):
            obj.setCursor(Qt.CursorShape.PointingHandCursor)
        return super().eventFilter(obj, event)


__all__ = [
    "BUILTIN_THEMES",
    "ButtonSize",
    "ButtonVariant",
    "CardTheme",
    "CheckBoxCursorFilter",
    "DynamicStyleMixin",
    "HyVisAppStyle",
    "ThemeManager",
    "ThemePreset",
    "ThemeTokens",
    "WidgetState",
    "adjust_color_lightness",
    "get_system_accent_color",
    "hex_to_rgba",
    "is_palette_dark",
    "render_surgical_stylesheet",
    "theme_manager",
]
