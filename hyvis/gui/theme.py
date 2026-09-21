"""
theme.py — Centralized semantic color definitions, enums, and shared styles for HyVis GUI.
"""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple


class CardTheme(StrEnum):
    """Semantic purpose themes for stacked cards and rule editors."""

    QUERY = "query"  # Search / Ingestion (Cyan)
    PAGE = "page"  # Client Pages / Navigation (Purple)
    ADD = "add"  # Post-Run Additions (Emerald)
    REMOVE = "remove"  # Post-Run Cleanups (Rose)
    DEFAULT = "default"  # Neutral Slate


class ThemeColors(NamedTuple):
    bg: str
    border: str
    accent: str


# Semantic color palettes for cards (subtle 3.5% wash + 18% border)
SEMANTIC_CARD_COLORS: dict[CardTheme, ThemeColors] = {
    CardTheme.QUERY: ThemeColors(
        bg="rgba(56, 189, 248, 0.035)",
        border="rgba(56, 189, 248, 0.18)",
        accent="#38bdf8",  # Sky Blue
    ),
    CardTheme.PAGE: ThemeColors(
        bg="rgba(168, 85, 247, 0.035)",
        border="rgba(168, 85, 247, 0.18)",
        accent="#a855f7",  # Purple
    ),
    CardTheme.ADD: ThemeColors(
        bg="rgba(52, 211, 153, 0.035)",
        border="rgba(52, 211, 153, 0.18)",
        accent="#34d399",  # Emerald
    ),
    CardTheme.REMOVE: ThemeColors(
        bg="rgba(244, 63, 94, 0.035)",
        border="rgba(244, 63, 94, 0.18)",
        accent="#f43f5e",  # Rose
    ),
    CardTheme.DEFAULT: ThemeColors(
        bg="rgba(255, 255, 255, 0.015)",
        border="rgba(255, 255, 255, 0.08)",
        accent="#90a4ae",  # Neutral Slate
    ),
}

# Centralized status and override highlights
STYLE_OVERRIDDEN = "border: 1.5px solid #38bdf8 !important; background-color: rgba(56, 189, 248, 0.08) !important;"
STYLE_ERROR = "border: 1.5px solid #f85149 !important; background-color: rgba(248, 81, 73, 0.08) !important;"


def get_card_stylesheet(theme: CardTheme, class_name: str = "QFrame") -> str:
    """Generate a clean stylesheet string for a card given its semantic theme."""
    colors = SEMANTIC_CARD_COLORS.get(theme, SEMANTIC_CARD_COLORS[CardTheme.DEFAULT])
    return f"{class_name} {{  border: 1px solid {colors.border};  border-radius: 6px;  background-color: {colors.bg};}}"
