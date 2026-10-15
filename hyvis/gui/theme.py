"""
theme.py — Centralized semantic color definitions, enums, and shared styles for HyVis GUI.
"""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QCheckBox, QProxyStyle, QStyle, QStyleOption, QWidget


class CardTheme(StrEnum):
    """Semantic purpose themes for stacked cards, banners, and rule editors."""

    # 1. Subtle content cards (3.5% wash, 18% border)
    DEFAULT = "default"  # Neutral Slate (Standard root cards)
    INFO = "info"  # Soft Material Blue
    CYAN = "cyan"  # Sky Blue / Cyan (Search query cards)
    PURPLE = "purple"  # Violet (Page queries, joint subsets)
    SUCCESS = "success"  # Emerald (Add tags, connected indicators)
    DANGER = "danger"  # Rose (Remove tags, errors)
    WARNING = "warning"  # Amber (Warnings, caveats, offline)

    # 2. Punchy / Vivid cards & banners (8% wash, 30% border)
    INFO_VIVID = "info_vivid"  # Saturated Material Blue (Override notice banner)
    WARNING_VIVID = "warning_vivid"  # Saturated Amber
    DANGER_VIVID = "danger_vivid"  # Saturated Rose


class ThemeColors(NamedTuple):
    bg: str
    border: str
    accent: str


# Semantic color palettes for cards and banners
SEMANTIC_CARD_COLORS: dict[CardTheme, ThemeColors] = {
    # Subtle content cards
    CardTheme.DEFAULT: ThemeColors(
        bg="rgba(255, 255, 255, 0.015)",
        border="rgba(255, 255, 255, 0.08)",
        accent="#90a4ae",  # Neutral Slate
    ),
    CardTheme.INFO: ThemeColors(
        bg="rgba(33, 150, 243, 0.035)",
        border="rgba(33, 150, 243, 0.18)",
        accent="#2196f3",  # Material Blue
    ),
    CardTheme.CYAN: ThemeColors(
        bg="rgba(56, 189, 248, 0.035)",
        border="rgba(56, 189, 248, 0.18)",
        accent="#38bdf8",  # Sky Blue
    ),
    CardTheme.PURPLE: ThemeColors(
        bg="rgba(168, 85, 247, 0.035)",
        border="rgba(168, 85, 247, 0.18)",
        accent="#a855f7",  # Purple
    ),
    CardTheme.SUCCESS: ThemeColors(
        bg="rgba(52, 211, 153, 0.035)",
        border="rgba(52, 211, 153, 0.18)",
        accent="#34d399",  # Emerald
    ),
    CardTheme.DANGER: ThemeColors(
        bg="rgba(244, 63, 94, 0.035)",
        border="rgba(244, 63, 94, 0.18)",
        accent="#f43f5e",  # Rose
    ),
    CardTheme.WARNING: ThemeColors(
        bg="rgba(251, 191, 36, 0.035)",
        border="rgba(251, 191, 36, 0.18)",
        accent="#fbbf24",  # Amber
    ),
    # Vivid / Punchy
    CardTheme.INFO_VIVID: ThemeColors(
        bg="rgba(33, 150, 243, 0.08)",
        border="rgba(33, 150, 243, 0.30)",
        accent="#2196f3",  # Saturated Material Blue
    ),
    CardTheme.WARNING_VIVID: ThemeColors(
        bg="rgba(251, 191, 36, 0.08)",
        border="rgba(251, 191, 36, 0.30)",
        accent="#fbbf24",  # Saturated Amber
    ),
    CardTheme.DANGER_VIVID: ThemeColors(
        bg="rgba(244, 63, 94, 0.08)",
        border="rgba(244, 63, 94, 0.30)",
        accent="#f43f5e",  # Saturated Rose
    ),
}

# Centralized status and override highlights
STYLE_OVERRIDDEN = "border: 1.5px solid #38bdf8; background-color: rgba(56, 189, 248, 0.08);"
STYLE_ERROR = "border: 1.5px solid #f85149; background-color: rgba(248, 81, 73, 0.08);"


def get_card_stylesheet(theme: CardTheme, class_name: str = "QFrame") -> str:
    """Generate a clean stylesheet string for a card given its semantic theme."""
    colors = SEMANTIC_CARD_COLORS.get(theme, SEMANTIC_CARD_COLORS[CardTheme.DEFAULT])
    return f"{class_name} {{  border: 1px solid {colors.border};  border-radius: 6px;  background-color: {colors.bg};}}"


class HyVisAppStyle(QProxyStyle):
    """Global proxy style that expands checkbox indicators to 18px application-wide."""

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
    """Global event filter that assigns a pointing hand cursor to all checkboxes on hover."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Enter and isinstance(obj, QCheckBox):
            obj.setCursor(Qt.CursorShape.PointingHandCursor)
        return super().eventFilter(obj, event)
