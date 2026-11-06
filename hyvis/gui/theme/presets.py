"""
presets.py — Built-in theme definitions bridging QPalette and ThemeTokens.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette

from hyvis.gui.theme.tokens import ThemeTokens


@dataclass(frozen=True)
class ThemePreset:
    id: str
    display_name: str
    is_dark: bool
    create_palette: Callable[[QPalette], QPalette | None]
    create_tokens: Callable[[QPalette, str | None], ThemeTokens]


def _set_disabled_text_colors(palette: QPalette, muted_color: str) -> None:
    color = QColor(muted_color)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, color)


def _build_mocha_palette(_: QPalette) -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor("#1e1e2e"))
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#cdd6f4"))
    pal.setColor(QPalette.ColorRole.Base, QColor("#181825"))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#313244"))
    pal.setColor(QPalette.ColorRole.Text, QColor("#cdd6f4"))
    pal.setColor(QPalette.ColorRole.Button, QColor("#313244"))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor("#cdd6f4"))
    pal.setColor(QPalette.ColorRole.Mid, QColor("#45475a"))
    pal.setColor(QPalette.ColorRole.Highlight, QColor("#89b4fa"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#11111b"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#6c7086"))
    _set_disabled_text_colors(pal, "#6c7086")
    return pal


def _build_latte_palette(_: QPalette) -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor("#eff1f5"))
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#4c4f69"))
    pal.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#e6e9ef"))
    pal.setColor(QPalette.ColorRole.Text, QColor("#4c4f69"))
    pal.setColor(QPalette.ColorRole.Button, QColor("#e6e9ef"))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor("#4c4f69"))
    pal.setColor(QPalette.ColorRole.Mid, QColor("#bcc0cc"))
    pal.setColor(QPalette.ColorRole.Highlight, QColor("#1e66f5"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#8c8fa1"))
    _set_disabled_text_colors(pal, "#8c8fa1")
    return pal


BUILTIN_THEMES: dict[str, ThemePreset] = {
    "system": ThemePreset(
        id="system",
        display_name="System (Default)",
        is_dark=True,
        create_palette=lambda _: None,
        create_tokens=lambda pal, accent: ThemeTokens.from_palette(pal, accent_override=accent),
    ),
    "catppuccin_mocha": ThemePreset(
        id="catppuccin_mocha",
        display_name="Catppuccin Mocha (Dark)",
        is_dark=True,
        create_palette=_build_mocha_palette,
        create_tokens=lambda pal, accent: ThemeTokens.from_palette(pal, accent_override=accent),
    ),
    "catppuccin_latte": ThemePreset(
        id="catppuccin_latte",
        display_name="Catppuccin Latte (Light)",
        is_dark=False,
        create_palette=_build_latte_palette,
        create_tokens=lambda pal, accent: ThemeTokens.from_palette(pal, accent_override=accent),
    ),
}
