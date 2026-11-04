"""
colors.py — Color calculations and native platform palette introspection.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication


def hex_to_rgba(hex_code: str, alpha: float) -> str:
    """Convert a hex color code to an rgba(r, g, b, a) CSS string."""
    color = QColor(hex_code)
    if not color.isValid():
        return f"rgba(128, 128, 128, {alpha})"
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha:.3f})"


def adjust_color_lightness(hex_code: str, factor: float) -> str:
    """Adjust the lightness of a hex color (factor > 1.0 brightens; < 1.0 darkens)."""
    color = QColor(hex_code)
    if not color.isValid():
        return hex_code

    h = color.hslHueF()
    s = color.hslSaturationF()
    l = color.lightnessF()
    a = color.alphaF()
    new_l = max(0.0, min(1.0, l * factor))
    color.setHslF(h, s, new_l, a)
    return color.name()


def get_system_accent_color(palette: QPalette | None = None, fallback: str = "#38bdf8") -> str:
    """Extract active OS accent color from palette, with highlight fallback."""
    pal = palette or (QApplication.instance().palette() if QApplication.instance() else None)
    if not pal:
        return fallback

    # In KDE Plasma (or maybe all *nixes?), Windows, and macOS, the user-customized accent color is stored in Highlight.
    # We check Highlight first so desktop portal accent settings (e.g. KDE Purple) are respected.
    highlight_color = pal.color(QPalette.ColorRole.Highlight)
    if highlight_color.isValid() and highlight_color.alpha() > 0:
        return highlight_color.name()

    accent_role = getattr(QPalette.ColorRole, "Accent", None)
    if accent_role is not None:
        accent_color = pal.color(accent_role)
        if accent_color.isValid() and accent_color.alpha() > 0:
            return accent_color.name()

    return fallback


def is_palette_dark(palette: QPalette | None = None) -> bool:
    """Detect dark mode using native Qt QStyleHints with window luminance fallback."""
    app = QGuiApplication.instance()
    if app:
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False

    pal = palette or (app.palette() if app else None)
    if pal:
        return pal.color(QPalette.ColorRole.Window).lightnessF() < 0.5
    return True
