"""
compiler.py — Surgical stylesheet compiler.
"""

from __future__ import annotations

from hyvis.gui.theme.tokens import ThemeTokens

# todo: add AppButton to base_widgets and look over qpushbutton QSS again


# region Core, Dividers


def _build_dividers_and_splitters(t: ThemeTokens) -> str:
    del t
    return """
/* Dividers & Splitters */
Divider {
    color: palette(mid);
    background-color: palette(mid);
    border: none;
}
QSplitter::handle:vertical {
    height: 6px;
    background-color: transparent;
    border-top: 1px solid palette(mid);
}
"""


# endregion


# region Custom Scrollbars


def _build_scrollbars(t: ThemeTokens) -> str:
    return f"""
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
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
    width: 0px;
}}

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
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    height: 0px;
    width: 0px;
}}
"""


# endregion


# region Cards


def _build_cards(t: ThemeTokens) -> str:
    purple_wash = "rgba(168, 85, 247, 0.12)" if t.is_dark else "rgba(168, 85, 247, 0.09)"
    purple_border = "rgba(168, 85, 247, 0.50)" if t.is_dark else "rgba(168, 85, 247, 0.40)"

    return f"""
/* Custom Cards Base */
SectionCard, TagSubsetCard, TagRuleCard {{
    background-color: {t.bg_surface};
    border: 1px solid {t.border_subtle};
    border-radius: {t.radius_md}px;
}}

/* Card Semantic Themes */
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

/* Vivid Card Variants */
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
"""


# endregion


# region Button Variants


def _build_buttons(t: ThemeTokens) -> str:
    return f"""
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

/* Style Variants */
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
QPushButton[btnVariant="danger_ghost"] {{
    background-color: transparent;
    border: 1px solid {t.border_default};
    color: {t.text_muted};
}}
QPushButton[btnVariant="danger_ghost"]:hover {{
    background-color: {t.status_danger_wash};
    border-color: {t.status_danger};
    color: {t.status_danger};
}}
"""


# endregion


# region Form Valid. Text Roles


def _build_validation_and_text_roles(t: ThemeTokens) -> str:
    return f"""
/* Targeted Form State Highlights */
QLineEdit[state="error"], QListWidget[state="error"], QTableWidget[state="error"], AutoResizingTableWidget[state="error"] {{
    border: 1.5px solid {t.status_danger} !important;
    background-color: {t.status_danger_wash} !important;
}}
QLineEdit[state="overridden"], QListWidget[state="overridden"], QTableWidget[state="overridden"] {{
    border: 1.5px solid {t.accent} !important;
    background-color: {t.accent_wash} !important;
}}

/* Semantic Text Roles */
QLabel[textRole="secondary"] {{ color: {t.text_secondary}; }}
QLabel[textRole="muted"]     {{ color: {t.text_muted}; }}
QLabel[textRole="accent"]    {{ color: {t.accent}; }}
QLabel[textRole="success"]   {{ color: {t.status_success}; }}
QLabel[textRole="warning"]   {{ color: {t.status_warning}; }}
QLabel[textRole="danger"]    {{ color: {t.status_danger}; }}
QLabel[textRole="info"]      {{ color: {t.status_info}; }}
"""


# endregion


# region ObjectName specifics


def _build_app_specific_widgets(t: ThemeTokens) -> str:
    return f"""
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
    border: 1px solid transparent;
    color: {t.text_primary};
}}
QListWidget#sidebar::item:hover {{
    background-color: {t.bg_subtle_hover};
    color: {t.text_primary};
}}
QListWidget#sidebar::item:selected {{
    background-color: {t.accent_wash};
    border: 1px solid {t.accent_border};
    color: {t.text_primary};
    font-weight: 600;
}}

/* Main Window Issues Panel & Drawer */
QFrame#issues_panel {{
    background-color: {t.bg_surface};
    border-top: 1px solid {t.border_default};
}}
QPushButton#close_issues_btn {{
    border: none;
    color: {t.text_muted};
    font-weight: bold;
    border-radius: 3px;
}}
QPushButton#close_issues_btn:hover {{
    color: {t.text_primary};
    background-color: {t.bg_subtle_hover};
}}
QListWidget#issues_list {{
    background: transparent;
    border: 1px solid {t.border_subtle};
    border-radius: {t.radius_sm}px;
    outline: none;
}}
QListWidget#issues_list::item {{
    padding: 6px 10px;
    color: {t.status_danger};
    border-bottom: 1px solid {t.border_subtle};
    border-radius: {t.radius_sm}px;
}}
QListWidget#issues_list::item:hover {{
    background-color: {t.status_danger_wash};
    color: {t.text_primary};
}}
QListWidget#issues_list::item:selected {{
    background-color: {t.status_danger_wash};
    color: {t.text_primary};
}}

/* Bottom Status Button */
QPushButton#status_btn {{
    background: transparent;
    border: none;
    text-align: left;
    padding: 4px 8px;
    border-radius: {t.radius_sm}px;
    color: {t.status_success};
    font-weight: normal;
}}
QPushButton#status_btn:hover {{
    background-color: {t.status_success_wash};
}}
QPushButton#status_btn[state="error"] {{
    color: {t.status_danger};
    font-weight: 600;
}}
QPushButton#status_btn[state="error"]:hover {{
    background-color: {t.status_danger_wash};
}}

/* Launch Dialog Command Text Box & Dialog Tips */
QTextEdit#cmd_edit, AutoResizingTextEdit#cmd_edit {{
    background-color: {t.bg_surface};
    border: 1px solid {t.border_default};
    border-radius: {t.radius_sm}px;
    padding: 4px 6px;
    color: {t.text_primary};
}}
QTextEdit#cmd_edit:focus, AutoResizingTextEdit#cmd_edit:focus {{
    border-color: {t.accent};
}}
QLabel#dialog_tip {{
    color: {t.text_secondary};
}}
"""


# endregion


# region Master Compiler


def render_surgical_stylesheet(t: ThemeTokens) -> str:
    """Combine all modular styling blocks into the final application QSS."""
    return "\n".join(
        [
            _build_dividers_and_splitters(t),
            _build_scrollbars(t),
            _build_cards(t),
            _build_buttons(t),
            _build_validation_and_text_roles(t),
            _build_app_specific_widgets(t),
        ]
    )


# endregion
