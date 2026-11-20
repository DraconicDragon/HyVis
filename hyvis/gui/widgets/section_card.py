from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QCheckBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from hyvis.gui.base_widgets import SemanticLabel
from hyvis.gui.schema_binding import validate_field_ui_metadata
from hyvis.gui.theme import AutoRepolishMixin, CardTheme, TextRole, ThemeProperty, WidgetState


class SectionCard(AutoRepolishMixin, QFrame):
    """
    Structured card with an inline header and QSS property integration.
    Uses AutoRepolishMixin for automatic repolishing on property updates.
    """

    _CONTENT_MARGINS = (6, 0, 6, 4)

    toggled = Signal(bool)

    def __init__(
        self,
        title: str = "",
        tooltip: str = "",
        field_name: str | None = None,
        theme: CardTheme = CardTheme.DEFAULT,
        parent: QWidget | None = None,
        *,
        field_info: Any = None,
    ) -> None:
        super().__init__(parent)
        if field_info is not None:
            validate_field_ui_metadata(field_info, field_label=field_name or "SectionCard")
            title = title or str(field_info.title)
            tooltip = tooltip or str(field_info.description)

        self._title = title
        self._is_checkable = False
        self._theme = theme

        if field_name:
            self.setObjectName(field_name)

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setProperty(ThemeProperty.CARD_THEME, theme)
        self.setProperty(ThemeProperty.STATE, WidgetState.DEFAULT)

        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(8, 6, 8, 8)
        card_layout.setSpacing(8)

        # Header container
        self._header_widget = QWidget(self)
        header_layout = QHBoxLayout(self._header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        self._header_layout = header_layout

        self._checkbox = QCheckBox(self._header_widget)
        self._checkbox.setVisible(False)
        self._checkbox.toggled.connect(self._on_check_toggled)
        header_layout.addWidget(self._checkbox)

        self._title_label = QLabel(title, self._header_widget)
        self._title_label.setStyleSheet("font-weight: 650;")
        if tooltip:
            self._title_label.setToolTip(tooltip)
        self._title_label.mousePressEvent = self._on_title_clicked
        header_layout.addWidget(self._title_label)

        header_layout.addStretch(1)

        self._badge_label = SemanticLabel(parent=self._header_widget, role=TextRole.ACCENT)
        self._badge_label.setStyleSheet("font-size: 12px; font-weight: 500;")
        self._badge_label.setVisible(False)
        header_layout.addWidget(self._badge_label)

        card_layout.addWidget(self._header_widget)
        self._update_header_visibility()

        # Content container
        self._content_widget = QWidget(self)
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(*self._CONTENT_MARGINS)
        self._content_layout.setSpacing(6)
        card_layout.addWidget(self._content_widget)

    @property
    def content_layout(self) -> QVBoxLayout:
        return self._content_layout

    def set_highlight_state(self, is_overridden: bool = False, is_error: bool = False) -> None:
        """Set state property; AutoRepolishMixin auto-repolishes QSS rules."""
        if is_error:
            self.setProperty(ThemeProperty.STATE, WidgetState.ERROR)
        elif is_overridden:
            self.setProperty(ThemeProperty.STATE, WidgetState.OVERRIDDEN)
        else:
            self.setProperty(ThemeProperty.STATE, WidgetState.DEFAULT)

    def _update_header_visibility(self) -> None:
        # Hide header container if no title is present and card isn't checkable
        has_content = bool(self._title or self._is_checkable or self._badge_label.isVisible())
        self._header_widget.setVisible(has_content)

    def addHeaderWidget(self, widget: QWidget) -> None:
        """Add an action widget to the right side of the header row."""
        self._header_layout.addWidget(widget)
        self._header_widget.setVisible(True)

    def setContentLayout(self, layout: QFormLayout | QVBoxLayout | QHBoxLayout) -> None:
        """Replace internal content layout with a specialized layout."""
        if self._title or self._is_checkable:
            layout.setContentsMargins(*self._CONTENT_MARGINS)
        QWidget().setLayout(self._content_layout)
        self._content_layout = layout
        self._content_widget.setLayout(layout)

    def setCheckable(self, checkable: bool) -> None:
        self._is_checkable = checkable
        self._checkbox.setVisible(checkable)
        self._title_label.setCursor(Qt.CursorShape.PointingHandCursor if checkable else Qt.CursorShape.ArrowCursor)
        self._update_header_visibility()
        if not checkable:
            self._content_widget.setEnabled(True)

    def setChecked(self, checked: bool) -> None:
        self._checkbox.blockSignals(True)
        self._checkbox.setChecked(checked)
        self._checkbox.blockSignals(False)
        self._content_widget.setEnabled(checked if self._is_checkable else True)

    def isChecked(self) -> bool:
        return self._checkbox.isChecked() if self._is_checkable else True

    def setTitle(self, title: str) -> None:
        self._title = title
        self._title_label.setText(title)
        self._update_header_visibility()

    def setBadge(self, text: str, tone: TextRole = TextRole.ACCENT) -> None:
        self._badge_label.setProperty(ThemeProperty.TEXT_ROLE, tone)
        if text:
            self._badge_label.setText(text)
            self._badge_label.setVisible(True)
        else:
            self._badge_label.clear()
            self._badge_label.setVisible(False)
        self._update_header_visibility()

    def set_info_tooltip(self, tooltip: str) -> None:
        """Set the tooltip directly on the title label."""
        self._title_label.setToolTip(tooltip)

    def _on_check_toggled(self, checked: bool) -> None:
        self._content_widget.setEnabled(checked)
        self.toggled.emit(checked)

    def _on_title_clicked(self, event: QMouseEvent) -> None:
        if self._is_checkable and event.button() == Qt.MouseButton.LeftButton:
            self._checkbox.toggle()
        else:
            QLabel.mousePressEvent(self._title_label, event)
