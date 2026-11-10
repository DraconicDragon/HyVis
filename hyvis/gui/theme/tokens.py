"""
tokens.py — ThemeTokens schema, semantic enums, and palette-driven token construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtGui import QColor, QPalette

from hyvis.gui.theme.colors import (
    adjust_color_lightness,
    get_system_accent_color,
    hex_to_rgba,
    is_palette_dark,
)

# region Semantic Enums


class ThemeProperty(StrEnum):
    CARD_THEME = "cardTheme"
    STATE = "state"
    BTN_VARIANT = "btnVariant"
    BTN_SIZE = "btnSize"
    TEXT_ROLE = "textRole"


class CardTheme(StrEnum):
    DEFAULT = "default"
    INFO = "info"
    CYAN = "cyan"
    PURPLE = "purple"
    SUCCESS = "success"
    DANGER = "danger"
    WARNING = "warning"
    INFO_VIVID = "info_vivid"
    WARNING_VIVID = "warning_vivid"
    DANGER_VIVID = "danger_vivid"


class WidgetState(StrEnum):
    DEFAULT = "default"
    OVERRIDDEN = "overridden"
    ERROR = "error"


class ButtonVariant(StrEnum):
    DEFAULT = "default"
    PRIMARY = "primary"
    GHOST = "ghost"
    DANGER = "danger"
    DANGER_GHOST = "danger_ghost"


class TextRole(StrEnum):
    SECONDARY = "secondary"
    MUTED = "muted"
    ACCENT = "accent"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"
    INFO = "info"


class ButtonSize(StrEnum):
    SM = "sm"
    MD = "md"
    LG = "lg"


# endregion


# region Design Tokens


@dataclass(frozen=True, kw_only=True)
class ThemeTokens:
    is_dark: bool

    # Surfaces
    bg_surface: str
    bg_subtle_hover: str

    # Borders & Dividers
    border_subtle: str
    border_default: str
    border_strong: str
    divider: str

    # Text
    text_primary: str
    text_secondary: str
    text_muted: str
    text_disabled: str
    text_on_accent: str

    # Accent
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_wash: str
    accent_border: str

    # Semantic Statuses
    status_success: str
    status_success_wash: str
    status_success_border: str

    status_warning: str
    status_warning_wash: str
    status_warning_border: str

    status_danger: str
    status_danger_wash: str
    status_danger_border: str

    status_info: str
    status_info_wash: str
    status_info_border: str

    # Scrollbars
    scrollbar_rail: str
    scrollbar_thumb: str
    scrollbar_thumb_hover: str

    # Geometry
    radius_sm: int = 4
    radius_md: int = 6
    radius_lg: int = 8

    @classmethod
    def from_palette(cls, palette: QPalette, accent_override: str | None = None) -> ThemeTokens:
        """Derive theme tokens directly from the active system or custom QPalette."""
        dark = is_palette_dark(palette)
        accent_hex = accent_override or get_system_accent_color(palette)

        text_color = palette.color(QPalette.ColorRole.WindowText).name()
        base_color = palette.color(QPalette.ColorRole.Base).name()
        window_color = palette.color(QPalette.ColorRole.Window).name()
        mid_color = palette.color(QPalette.ColorRole.Mid).name()

        card_surface = adjust_color_lightness(window_color, 1.10) if dark else base_color

        # Semantic status colors
        success_hex = "#34d399" if dark else "#059669"
        warning_hex = "#fbbf24" if dark else "#d97706"
        danger_hex = "#f43f5e" if dark else "#e11d48"
        info_hex = "#38bdf8" if dark else "#0284c7"

        accent_color_obj = QColor(accent_hex)
        contrast_text = "#ffffff" if accent_color_obj.lightnessF() < 0.65 else "#000000"

        return cls(
            is_dark=dark,
            bg_surface=card_surface,
            bg_subtle_hover=hex_to_rgba(text_color, 0.05),
            border_subtle=hex_to_rgba(text_color, 0.10 if dark else 0.08),
            border_default=hex_to_rgba(text_color, 0.18 if dark else 0.15),
            border_strong=hex_to_rgba(text_color, 0.30 if dark else 0.25),
            divider=mid_color if mid_color != window_color else hex_to_rgba(text_color, 0.12),
            text_primary=text_color,
            text_secondary=hex_to_rgba(text_color, 0.75 if dark else 0.70),
            text_muted=hex_to_rgba(text_color, 0.50),
            text_disabled=hex_to_rgba(text_color, 0.4 if dark else 0.55),
            text_on_accent=contrast_text,
            accent=accent_hex,
            accent_hover=adjust_color_lightness(accent_hex, 1.15 if dark else 0.90),
            accent_pressed=adjust_color_lightness(accent_hex, 0.85 if dark else 0.75),
            accent_wash=hex_to_rgba(accent_hex, 0.15 if dark else 0.11),
            accent_border=hex_to_rgba(accent_hex, 0.55 if dark else 0.45),
            status_success=success_hex,
            status_success_wash=hex_to_rgba(success_hex, 0.14 if dark else 0.10),
            status_success_border=hex_to_rgba(success_hex, 0.50 if dark else 0.40),
            status_warning=warning_hex,
            status_warning_wash=hex_to_rgba(warning_hex, 0.14 if dark else 0.10),
            status_warning_border=hex_to_rgba(warning_hex, 0.50 if dark else 0.40),
            status_danger=danger_hex,
            status_danger_wash=hex_to_rgba(danger_hex, 0.14 if dark else 0.10),
            status_danger_border=hex_to_rgba(danger_hex, 0.50 if dark else 0.40),
            status_info=info_hex,
            status_info_wash=hex_to_rgba(info_hex, 0.14 if dark else 0.10),
            status_info_border=hex_to_rgba(info_hex, 0.50 if dark else 0.40),
            scrollbar_rail="rgba(0, 0, 0, 0.25)" if dark else "rgba(0, 0, 0, 0.06)",
            scrollbar_thumb="rgba(255, 255, 255, 0.20)" if dark else "rgba(0, 0, 0, 0.20)",
            scrollbar_thumb_hover="rgba(255, 255, 255, 0.35)" if dark else "rgba(0, 0, 0, 0.35)",
        )


# endregion
