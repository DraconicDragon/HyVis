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

    INFO = "info"  # Sky Blue / Cyan (Search queries, notices, info)
    SUCCESS = "success"  # Emerald (Add tags, connected indicators)
    WARNING = "warning"  # Amber / Yellow (Warnings, caveats, offline)
    DANGER = "danger"  # Rose / Red (Remove tags, errors)
    PURPLE = "purple"  # Violet (Page queries, joint subsets)
    DEFAULT = "default"  # Neutral Slate (Standard root cards)


class ThemeColors(NamedTuple):
    bg: str
    border: str
    accent: str


# Semantic color palettes for cards (subtle 3.5% wash + 18% border)
SEMANTIC_CARD_COLORS: dict[CardTheme, ThemeColors] = {
    CardTheme.INFO: ThemeColors(
        bg="rgba(56, 189, 248, 0.035)",
        border="rgba(56, 189, 248, 0.18)",
        accent="#38bdf8",  # Sky Blue
    ),
    CardTheme.SUCCESS: ThemeColors(
        bg="rgba(52, 211, 153, 0.035)",
        border="rgba(52, 211, 153, 0.18)",
        accent="#34d399",  # Emerald
    ),
    CardTheme.WARNING: ThemeColors(
        bg="rgba(251, 191, 36, 0.035)",
        border="rgba(251, 191, 36, 0.18)",
        accent="#fbbf24",  # Amber
    ),
    CardTheme.DANGER: ThemeColors(
        bg="rgba(244, 63, 94, 0.035)",
        border="rgba(244, 63, 94, 0.18)",
        accent="#f43f5e",  # Rose
    ),
    CardTheme.PURPLE: ThemeColors(
        bg="rgba(168, 85, 247, 0.035)",
        border="rgba(168, 85, 247, 0.18)",
        accent="#a855f7",  # Purple
    ),
    CardTheme.DEFAULT: ThemeColors(
        bg="rgba(255, 255, 255, 0.015)",
        border="rgba(255, 255, 255, 0.08)",
        accent="#90a4ae",  # Neutral Slate
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
