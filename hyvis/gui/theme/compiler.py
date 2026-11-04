"""
compiler.py — Surgical stylesheet compiler.
"""

from __future__ import annotations

from hyvis.gui.theme.tokens import ThemeTokens


def render_surgical_stylesheet(t: ThemeTokens) -> str:
    """Generate scoped QSS for custom cards, scrollbars, and button variants."""
    purple_wash = "rgba(168, 85, 247, 0.035)" if t.is_dark else "rgba(168, 85, 247, 0.06)"
    purple_border = "rgba(168, 85, 247, 0.18)" if t.is_dark else "rgba(168, 85, 247, 0.25)"

    return f"""
/* Dividers & Splitters */
Divider {{
    color: palette(mid);
    background-color: palette(mid);
    border: none;
}}
QSplitter::handle:vertical {{
    height: 6px;
    background-color: transparent;
    border-top: 1px solid palette(mid);
}}
QSplitter::handle:horizontal {{
    width: 6px;
    background-color: transparent;
    border-left: 1px solid palette(mid);
}}

/* Sidebar Navigation */
QListWidget#sidebar {{
    background: transparent;
    border: none;
    outline: none;
    padding-top: 8px;
}}
QListWidget#sidebar::item {{
    padding: 10px 14px;
    margin: 4px 12px 8px 0px;
    border-radius: {t.radius_md}px;
    color: {t.text_primary};
}}
QListWidget#sidebar::item:hover {{
    background-color: {t.bg_subtle_hover};
    color: {t.text_primary};
}}
QListWidget#sidebar::item:selected {{
    background-color: {t.accent_wash};
    color: {t.accent};
    font-weight: 600;
}}

/* Validation & Overrides */
QLineEdit[state="error"], QListWidget[state="error"], QTableWidget[state="error"], AutoResizingTableWidget[state="error"] {{
    border: 1.5px solid {t.status_danger} !important;
    background-color: {t.status_danger_wash} !important;
}}
QLineEdit[state="overridden"], QListWidget[state="overridden"], QTableWidget[state="overridden"] {{
    border: 1.5px solid {t.accent} !important;
    background-color: {t.accent_wash} !important;
}}

/* Custom Cards */
SectionCard, TagSubsetCard, TagRuleCard {{
    background-color: {t.bg_surface};
    border: 1px solid {t.border_subtle};
    border-radius: {t.radius_md}px;
}}

SectionCard[cardTheme="default"], TagRuleCard[cardTheme="default"] {{
    background-color: {t.bg_surface};
    border-color: {t.border_subtle};
}}
SectionCard[cardTheme="info"], TagRuleCard[cardTheme="info"] {{
    background-color: {t.status_info_wash};
    border-color: {t.status_info_border};
}}
SectionCard[cardTheme="cyan"], TagRuleCard[cardTheme="cyan"] {{
    background-color: {t.accent_wash};
    border-color: {t.accent_border};
}}
SectionCard[cardTheme="purple"], TagSubsetCard[cardTheme="purple"] {{
    background-color: {purple_wash};
    border-color: {purple_border};
}}
SectionCard[cardTheme="success"], TagRuleCard[cardTheme="success"] {{
    background-color: {t.status_success_wash};
    border-color: {t.status_success_border};
}}
SectionCard[cardTheme="danger"], TagRuleCard[cardTheme="danger"] {{
    background-color: {t.status_danger_wash};
    border-color: {t.status_danger_border};
}}
SectionCard[cardTheme="warning"], TagRuleCard[cardTheme="warning"] {{
    background-color: {t.status_warning_wash};
    border-color: {t.status_warning_border};
}}

/* Vivid Cards */
SectionCard[cardTheme="info_vivid"] {{
    background-color: {t.status_info_wash};
    border: 1.5px solid {t.status_info};
}}
SectionCard[cardTheme="warning_vivid"] {{
    background-color: {t.status_warning_wash};
    border: 1.5px solid {t.status_warning};
}}
SectionCard[cardTheme="danger_vivid"] {{
    background-color: {t.status_danger_wash};
    border: 1.5px solid {t.status_danger};
}}

/* Card State Overrides */
SectionCard[state="error"], TagSubsetCard[state="error"], TagRuleCard[state="error"] {{
    border: 1.5px solid {t.status_danger} !important;
    background-color: {t.status_danger_wash} !important;
}}
SectionCard[state="overridden"], TagSubsetCard[state="overridden"], TagRuleCard[state="overridden"] {{
    border: 1.5px solid {t.accent} !important;
    background-color: {t.accent_wash} !important;
}}

/* Opt-In AppButton Variants */
QPushButton[btnVariant] {{
    border-radius: {t.radius_sm}px;
    font-weight: 500;
}}
QPushButton[btnSize="sm"] {{
    padding: 3px 8px;
    font-size: 12px;
}}
QPushButton[btnSize="md"] {{
    padding: 6px 14px;
    font-size: 13px;
}}
QPushButton[btnSize="lg"] {{
    padding: 10px 20px;
    font-size: 14px;
    font-weight: 600;
}}

/* Button Variants */
QPushButton[btnVariant="default"] {{
    background-color: {t.bg_subtle_hover};
    border: 1px solid {t.border_default};
    color: {t.text_primary};
}}
QPushButton[btnVariant="default"]:hover {{
    background-color: {t.border_subtle};
    border-color: {t.border_strong};
}}
QPushButton[btnVariant="default"]:pressed {{
    background-color: {t.border_default};
}}

QPushButton[btnVariant="primary"] {{
    background-color: {t.accent};
    border: 1px solid {t.accent};
    color: {t.text_on_accent};
}}
QPushButton[btnVariant="primary"]:hover {{
    background-color: {t.accent_hover};
    border-color: {t.accent_hover};
}}
QPushButton[btnVariant="primary"]:pressed {{
    background-color: {t.accent_pressed};
}}

QPushButton[btnVariant="ghost"] {{
    background-color: transparent;
    border: none;
    color: {t.text_secondary};
}}
QPushButton[btnVariant="ghost"]:hover {{
    background-color: {t.bg_subtle_hover};
    color: {t.text_primary};
}}
QPushButton[btnVariant="ghost"]:pressed {{
    background-color: {t.border_subtle};
}}

QPushButton[btnVariant="danger"] {{
    background-color: {t.status_danger_wash};
    border: 1px solid {t.status_danger_border};
    color: {t.status_danger};
}}
QPushButton[btnVariant="danger"]:hover {{
    background-color: {t.status_danger};
    border-color: {t.status_danger};
    color: {t.text_on_accent};
}}

/* Custom Scrollbars */
QScrollBar:vertical {{
    background: {t.scrollbar_rail};
    width: 8px;
    margin: 6px 0px 6px 0px;
    border-radius: {t.radius_sm}px;
}}
QScrollBar::handle:vertical {{
    background: {t.scrollbar_thumb};
    min-height: 28px;
    border-radius: {t.radius_sm}px;
}}
QScrollBar::handle:vertical:hover {{
    background: {t.scrollbar_thumb_hover};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; width: 0px; }}

QScrollBar:horizontal {{
    background: {t.scrollbar_rail};
    height: 8px;
    margin: 0px 6px 0px 6px;
    border-radius: {t.radius_sm}px;
}}
QScrollBar::handle:horizontal {{
    background: {t.scrollbar_thumb};
    min-width: 28px;
    border-radius: {t.radius_sm}px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {t.scrollbar_thumb_hover};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ height: 0px; width: 0px; }}
"""
