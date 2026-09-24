"""
widgets.py — Reusable specialized input widgets for HyVis configuration.

Includes:
  - StringListEditor: Reusable list for include/exclude tags.
  - TagServiceListEditor: Stacked comboboxes with friendly service names, [X] buttons,
    and automatic resolution of raw hex keys.
  - KeyValueEditor: Reusable table for prefix mappings and tag replacements.
  - ThresholdTableEditor: Table for category and tag thresholds with TLT override checkboxes.
  - SubsetListEditor: Table for managing max_tags_per_subset rule groups.
  - CategoryLimitEditor: Table editor for category limit mappings.
  - CategoryTagEditor: Dynamic category list editor starting empty with vibe suggestions.
  - TagQueryCard & TagQueryListEditor: Stacked cards for Hydrus search queries avoiding comma bugs.
  - SectionCard: Card container with inline header, checkable toggle, and title tooltip.
  - SmoothScrollArea: Momentum-based easing scroll area with event-filtered wheel routing.
  - setup_field_tooltip / bind_field_metadata / add_form_row: Metadata wiring helpers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hyvis.gui.theme import STYLE_ERROR, STYLE_OVERRIDDEN, CardTheme, get_card_stylesheet

HYDRUS_BUILTIN_ALL_KNOWN_TAGS_KEY = "616c6c206b6e6f776e2074616773"


def setup_field_tooltip(widget: QWidget, field_info: Any) -> None:
    """Set the widget tooltip from Pydantic Field description if present."""
    if hasattr(field_info, "description") and field_info.description:
        widget.setToolTip(field_info.description)


def bind_field_metadata(widget: QWidget, field_info: Any, set_placeholder: bool = True) -> None:
    """
    Bind Pydantic Field metadata to a Qt widget.
    Applies description as tooltip, and the first example as placeholder if applicable.
    """
    setup_field_tooltip(widget, field_info)
    if set_placeholder and hasattr(field_info, "examples") and field_info.examples:
        first_example = field_info.examples[0]
        if hasattr(widget, "setPlaceholderText") and isinstance(first_example, (str, int, float)):
            widget.setPlaceholderText(str(first_example))


# region Form Helpers & Styling


def set_widget_override_state(widget: QWidget, is_overridden: bool, is_error: bool = False) -> None:
    """Apply or clear visual override/error highlighting on an input widget, card, or table/list."""
    target = widget
    if hasattr(widget, "list_widget"):
        target = widget.list_widget
    elif hasattr(widget, "table"):
        target = widget.table

    if isinstance(target, SectionCard):
        target.set_highlight_state(is_overridden=is_overridden, is_error=is_error)
        return

    if is_error:
        target.setStyleSheet(STYLE_ERROR)
    elif is_overridden:
        target.setStyleSheet(STYLE_OVERRIDDEN)
    else:
        target.setStyleSheet("")


def add_form_row(
    layout: QFormLayout,
    fields_or_info: Any,
    field_name_or_widget: str | QWidget,
    widget: QWidget | None = None,
    label_text: str | None = None,
) -> QLabel:
    """
    Add a row to a QFormLayout, assigning field_info.description tooltip
    to BOTH the newly created QLabel and the input widget simultaneously.
    Automatically assigns widget.setObjectName(field_name) when field_name is passed.
    """
    if isinstance(field_name_or_widget, str):
        field_name = field_name_or_widget
        field_info = fields_or_info[field_name]
        target_widget = widget
    else:
        field_name = None
        field_info = fields_or_info
        target_widget = field_name_or_widget

    assert target_widget is not None

    if field_name:
        target_widget.setObjectName(field_name)

    title = label_text or getattr(field_info, "title", None) or "Field"
    label = QLabel(f"{title}:")
    setup_field_tooltip(label, field_info)
    bind_field_metadata(target_widget, field_info)

    if (
        isinstance(target_widget, (QAbstractSpinBox, QComboBox))
        and target_widget.focusPolicy() == Qt.FocusPolicy.WheelFocus
    ):
        target_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    layout.addRow(label, target_widget)
    return label


# endregion


# region Section Card


class SectionCard(QFrame):
    """
    A structured card with an inline header:
    [ Optional CheckBox ] [ Title Label (with tooltip) ] [ Stretch ] [ Status Badge ]
    Clicking the title label toggles the checkbox when checkable.
    """

    toggled = Signal(bool)

    def __init__(
        self,
        title: str = "",
        tooltip: str = "",
        field_name: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._is_checkable = False

        if field_name:
            self.setObjectName(field_name)

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "SectionCard {"
            "  border: 1px solid rgba(255, 255, 255, 0.10);"
            "  border-radius: 6px;"
            "  background: rgba(255, 255, 255, 0.015);"
            "}"
        )

        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(10, 8, 10, 10)
        card_layout.setSpacing(8)

        # Header row
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self._checkbox = QCheckBox(self)
        self._checkbox.setVisible(False)
        self._checkbox.toggled.connect(self._on_check_toggled)
        header_layout.addWidget(self._checkbox)

        self._title_label = QLabel(title, self)
        self._title_label.setStyleSheet("font-weight: 650;")
        if tooltip:
            self._title_label.setToolTip(tooltip)
        self._title_label.mousePressEvent = self._on_title_clicked
        header_layout.addWidget(self._title_label)

        header_layout.addStretch(1)

        self._badge_label = QLabel(self)
        self._badge_label.setStyleSheet("font-size: 12px; font-weight: 500;")
        self._badge_label.setVisible(False)
        header_layout.addWidget(self._badge_label)

        card_layout.addLayout(header_layout)

        # Content container
        self._content_widget = QWidget(self)
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(6)
        card_layout.addWidget(self._content_widget)

    @property
    def content_layout(self) -> QVBoxLayout:
        return self._content_layout

    def set_highlight_state(self, is_overridden: bool = False, is_error: bool = False) -> None:
        """Apply error or override border highlight without destroying card geometry."""
        if is_error:
            self.setStyleSheet(
                "SectionCard {"
                "  border: 1.5px solid #f85149;"
                "  border-radius: 6px;"
                "  background: rgba(248, 81, 73, 0.08);"
                "}"
            )
        elif is_overridden:
            self.setStyleSheet(
                "SectionCard {"
                "  border: 1.5px solid #38bdf8;"
                "  border-radius: 6px;"
                "  background: rgba(56, 189, 248, 0.08);"
                "}"
            )
        else:
            self.setStyleSheet(
                "SectionCard {"
                "  border: 1px solid rgba(255, 255, 255, 0.10);"
                "  border-radius: 6px;"
                "  background: rgba(255, 255, 255, 0.015);"
                "}"
            )

    def setContentLayout(self, layout: QFormLayout | QVBoxLayout | QHBoxLayout) -> None:
        """Replace internal content layout with a specialized layout."""
        QWidget().setLayout(self._content_layout)
        self._content_layout = layout
        self._content_widget.setLayout(layout)

    def setCheckable(self, checkable: bool) -> None:
        self._is_checkable = checkable
        self._checkbox.setVisible(checkable)
        self._title_label.setCursor(Qt.CursorShape.PointingHandCursor if checkable else Qt.CursorShape.ArrowCursor)
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

    def setBadge(self, text: str, color: str = "#38bdf8") -> None:
        if text:
            self._badge_label.setText(f"<span style='color: {color};'>{text}</span>")
            self._badge_label.setVisible(True)
        else:
            self._badge_label.clear()
            self._badge_label.setVisible(False)

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


# endregion


# region String List Editor


class StringListEditor(QWidget):
    """
    A widget for viewing, adding, and removing a list of strings.
    Used for include_tags, exclude_tags, and general tag groups.
    Supports multi-line paste splitting and dynamic height expansion.
    """

    changed = Signal()

    def __init__(
        self,
        placeholder: str = "Enter item...",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 1. List view (compact default height, auto-expands with items)
        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._adjust_height()
        layout.addWidget(self.list_widget)

        # 2. Input and Action Bar
        input_layout = QHBoxLayout()
        input_layout.setSpacing(6)

        self.input_line = QLineEdit(self)
        self.input_line.setPlaceholderText(placeholder)
        self.input_line.returnPressed.connect(self._on_add)
        input_layout.addWidget(self.input_line, stretch=1)

        self.add_btn = QPushButton("Add", self)
        self.add_btn.clicked.connect(self._on_add)
        input_layout.addWidget(self.add_btn)

        self.remove_btn = QPushButton("Remove", self)
        self.remove_btn.clicked.connect(self._on_remove)
        input_layout.addWidget(self.remove_btn)

        layout.addLayout(input_layout)

    def _adjust_height(self) -> None:
        """Starts compact (2-3 items) and expands dynamically up to a maximum cap."""
        count = self.list_widget.count()
        target = max(68, min(68 + max(0, count - 2) * 22, 160))
        self.list_widget.setFixedHeight(target)

    def get_items(self) -> list[str]:
        items: list[str] = []
        for i in range(self.list_widget.count()):
            text = self.list_widget.item(i).text().strip()
            if text:
                items.append(text)
        return items

    def set_items(self, items: Sequence[str]) -> None:
        self.blockSignals(True)
        self.list_widget.clear()
        for item in items:
            cleaned = str(item).strip()
            if cleaned:
                self.list_widget.addItem(QListWidgetItem(cleaned))
        self._adjust_height()
        self.blockSignals(False)

    def _on_add(self) -> None:
        raw_text = self.input_line.text()
        if not raw_text.strip():
            return

        # Split on newlines, strip each tag, discard empty lines
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if not lines:
            return

        existing = set(self.get_items())
        added_any = False
        for tag in lines:
            if tag not in existing:
                self.list_widget.addItem(QListWidgetItem(tag))
                existing.add(tag)
                added_any = True

        self.input_line.clear()
        if added_any:
            self._adjust_height()
            self.changed.emit()

    def _on_remove(self) -> None:
        selected = self.list_widget.selectedItems()
        if not selected:
            return

        for item in selected:
            row = self.list_widget.row(item)
            self.list_widget.takeItem(row)

        self._adjust_height()
        self.changed.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self._on_remove()
        else:
            super().keyPressEvent(event)


# endregion


# region Category Tag Editor


class CategoryTagEditor(QWidget):
    """
    Dynamic category list editor.
    Starts empty by default; populates suggestions dynamically from vibe model metadata.
    Filters out already-added categories from the combobox to prevent duplicates.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._suggestions: list[str] = []
        self._provenance: dict[str, list[str]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 1. List of active categories
        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._adjust_height()
        layout.addWidget(self.list_widget)

        # 2. Add bar with editable combobox
        input_layout = QHBoxLayout()
        input_layout.setSpacing(6)

        self.combo = SuggestionComboBox(self)
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.combo.about_to_show_popup.connect(self._refresh_combo)
        line_edit = self.combo.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText("Select or type category...")
            line_edit.returnPressed.connect(self._on_add)
        input_layout.addWidget(self.combo, stretch=1)

        self.add_btn = QPushButton("+ Add", self)
        self.add_btn.clicked.connect(self._on_add)
        input_layout.addWidget(self.add_btn)

        self.remove_btn = QPushButton("- Remove Selected", self)
        self.remove_btn.clicked.connect(self._on_remove)
        input_layout.addWidget(self.remove_btn)

        layout.addLayout(input_layout)

    def _adjust_height(self) -> None:
        count = self.list_widget.count()
        target = max(68, min(68 + max(0, count - 2) * 22, 160))
        self.list_widget.setFixedHeight(target)

    def _refresh_combo(self) -> None:
        self.combo.blockSignals(True)
        current = self.combo.currentText()
        self.combo.clear()

        # Omit categories that are already in the list
        existing = set(self.get_items())
        available = [c for c in self._suggestions if c not in existing]

        for cat in available:
            self.combo.addItem(cat)
            models = self._provenance.get(cat, [])
            if models:
                self.combo.setItemData(
                    self.combo.count() - 1,
                    f"From: {', '.join(models)}",
                    Qt.ItemDataRole.ToolTipRole,
                )

        if current in available:
            self.combo.setCurrentText(current)
        elif available:
            self.combo.setCurrentText(available[0])
        else:
            self.combo.setCurrentText("")

        self.combo.blockSignals(False)

    def set_suggestions(self, suggestions: Sequence[str] | Mapping[str, Sequence[str]]) -> None:
        if isinstance(suggestions, Mapping):
            self._suggestions = sorted(suggestions.keys())
            self._provenance = {k: list(v) for k, v in suggestions.items()}
        else:
            self._suggestions = sorted(dict.fromkeys(suggestions))
            self._provenance = {}
        self._refresh_combo()

    def get_items(self) -> list[str]:
        items: list[str] = []
        for i in range(self.list_widget.count()):
            text = self.list_widget.item(i).text().strip()
            if text:
                items.append(text)
        return items

    def set_items(self, items: Sequence[str]) -> None:
        self.blockSignals(True)
        self.list_widget.clear()
        for item in items:
            cleaned = str(item).strip()
            if cleaned:
                self.list_widget.addItem(QListWidgetItem(cleaned))
        self._adjust_height()
        self._refresh_combo()
        self.blockSignals(False)

    def _on_add(self) -> None:
        raw_text = self.combo.currentText()
        lines = [line.strip().lower() for line in raw_text.splitlines() if line.strip()]
        if not lines:
            return

        existing = set(self.get_items())
        added_any = False
        for cat in lines:
            if cat not in existing:
                self.list_widget.addItem(QListWidgetItem(cat))
                existing.add(cat)
                added_any = True

        if added_any:
            self._adjust_height()
            self._refresh_combo()
            self.changed.emit()

    def _on_remove(self) -> None:
        selected = self.list_widget.selectedItems()
        if not selected:
            return

        for item in selected:
            row = self.list_widget.row(item)
            self.list_widget.takeItem(row)

        self._adjust_height()
        self._refresh_combo()
        self.changed.emit()


# endregion


# region Tag Service List Editor


class TagServiceListEditor(QWidget):
    """
    Stacked row editor for Hydrus tag service keys.
    Displays human-readable service names while strictly preserving underlying 64-char hex keys.
    Non-editable: values are strictly backed by Hydrus entities with offline/unrecognized preservation.
    """

    changed = Signal()

    def __init__(
        self,
        writable_only: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.writable_only = writable_only
        self._available_services: dict[str, str] = {}
        self._row_widgets: list[QWidget] = []

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(6)

        # 1. Stack container
        self._stack_widget = QWidget(self)
        self._stack_layout = QVBoxLayout(self._stack_widget)
        self._stack_layout.setContentsMargins(0, 0, 0, 0)
        self._stack_layout.setSpacing(6)
        self._root_layout.addWidget(self._stack_widget)

        # 2. Empty placeholder label
        self.empty_label = QLabel("(No services selected — click '+ Add Service' below)", self)
        self.empty_label.setStyleSheet("color: #888; font-style: italic; padding: 4px;")
        self._root_layout.addWidget(self.empty_label)

        # 3. Add button
        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("+ Add Service", self)
        self.add_btn.clicked.connect(self._on_add_clicked)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addStretch()
        self._root_layout.addLayout(btn_layout)

        self._update_empty_state()

    def set_available_services(self, services: dict[str, str]) -> None:
        self._available_services = dict(services)
        for row_widget in self._row_widgets:
            combo: QComboBox | None = row_widget.findChild(QComboBox)
            if not combo:
                continue
            current_key = combo.currentData()
            self._repopulate_combo(combo, selected_key=str(current_key) if current_key else None)

    def get_items(self) -> list[str]:
        keys: list[str] = []
        for row_widget in self._row_widgets:
            combo: QComboBox | None = row_widget.findChild(QComboBox)
            if combo:
                data = combo.currentData()
                if data:
                    keys.append(str(data).strip())
        return keys

    def set_items(self, keys: Sequence[str]) -> None:
        self.blockSignals(True)
        self._clear_rows()
        for key in keys:
            self._add_row(key)
        self._update_empty_state()
        self.blockSignals(False)

    def _clear_rows(self) -> None:
        for row in self._row_widgets:
            self._stack_layout.removeWidget(row)
            row.deleteLater()
        self._row_widgets.clear()

    def _repopulate_combo(self, combo: QComboBox, selected_key: str | None = None) -> None:
        combo.blockSignals(True)
        combo.clear()

        # Offline State
        if not self._available_services:
            if selected_key:
                short_k = f"{selected_key[:8]}..." if len(selected_key) > 12 else selected_key
                combo.addItem(f"Service: {short_k} (Offline)", userData=selected_key)
                combo.setCurrentIndex(0)
                combo.setEnabled(False)
            else:
                combo.addItem("⚠ Connect to Hydrus to select services", userData="")
                combo.setCurrentIndex(0)
                combo.setEnabled(False)
            combo.blockSignals(False)
            return

        # Connected State
        combo.setEnabled(True)

        for key, name in self._available_services.items():
            short_key = f"{key[:8]}..." if len(key) > 12 else key
            display = f"{name}  ({short_key})"
            combo.addItem(display, userData=key)

        if selected_key:
            idx = combo.findData(selected_key)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                short_key = f"{selected_key[:8]}..." if len(selected_key) > 12 else selected_key
                combo.addItem(f"❌ Unrecognized Service  ({short_key})", userData=selected_key)
                combo.setCurrentIndex(combo.count() - 1)
        else:
            # Pick the first service that isn't already selected in another row
            existing_keys = set(self.get_items())
            found_idx = -1
            for i in range(combo.count()):
                k = combo.itemData(i)
                if k and k not in existing_keys:
                    found_idx = i
                    break
            combo.setCurrentIndex(max(found_idx, 0))

        combo.blockSignals(False)

    def _add_row(self, initial_key: str | None = None) -> QWidget:
        row_widget = QWidget(self._stack_widget)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        combo = QComboBox(row_widget)
        combo.setEditable(False)  # NON-EDITABLE: Pure entity selector
        self._repopulate_combo(combo, selected_key=initial_key)
        combo.currentIndexChanged.connect(lambda _: self.changed.emit())
        row_layout.addWidget(combo, stretch=1)

        del_btn = QPushButton("✕", row_widget)
        del_btn.setFixedWidth(28)
        del_btn.setToolTip("Remove this service")
        del_btn.setStyleSheet(
            "QPushButton { color: #888; font-weight: bold; border: 1px solid #444; border-radius: 3px; }"
            "QPushButton:hover { color: #d32f2f; border-color: #d32f2f; background: rgba(211, 47, 47, 0.1); }"
        )
        del_btn.clicked.connect(lambda: self._on_remove_row(row_widget))
        row_layout.addWidget(del_btn)

        self._stack_layout.addWidget(row_widget)
        self._row_widgets.append(row_widget)
        self._update_empty_state()
        return row_widget

    def _on_add_clicked(self) -> None:
        self._add_row()
        self.changed.emit()

    def _on_remove_row(self, row_widget: QWidget) -> None:
        if row_widget in self._row_widgets:
            self._row_widgets.remove(row_widget)
            self._stack_layout.removeWidget(row_widget)
            row_widget.deleteLater()
            self._update_empty_state()
            self.changed.emit()

    def _update_empty_state(self) -> None:
        self.empty_label.setVisible(len(self._row_widgets) == 0)


# endregion


# region Key-Value Mapping Editor


class KeyValueEditor(QWidget):
    """
    A 2-column table widget for editing string-to-string mappings.
    Supports editable comboboxes with next-available pre-population and duplicate prevention.
    """

    changed = Signal()

    def __init__(
        self,
        key_header: str = "Key",
        val_header: str = "Value",
        use_combobox_for_key: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._key_header = key_header
        self._val_header = val_header
        self._use_combobox_for_key = use_combobox_for_key
        self._key_suggestions: list[str] = []
        self._provenance: dict[str, list[str]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels([self._key_header, self._val_header])
        self.table.horizontalHeader().setStyleSheet("QHeaderView::section { padding: 4px 10px; }")
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 160)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._on_item_changed)
        self._adjust_height()
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)
        self.add_btn = QPushButton("+ Add Row", self)
        self.add_btn.clicked.connect(self._on_add_row)
        btn_layout.addWidget(self.add_btn)

        self.remove_btn = QPushButton("- Remove Selected", self)
        self.remove_btn.clicked.connect(self._on_remove_row)
        btn_layout.addWidget(self.remove_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def _adjust_height(self) -> None:
        rows = self.table.rowCount()
        target = max(86, min(32 + max(rows, 1) * 30, 210))
        self.table.setFixedHeight(target)

    def _get_used_keys(self, exclude_row: int = -1) -> set[str]:
        used = set()
        for r in range(self.table.rowCount()):
            if r == exclude_row:
                continue
            if self._use_combobox_for_key:
                combo: QComboBox | None = self.table.cellWidget(r, 0)
                if combo and combo.currentText().strip():
                    used.add(combo.currentText().strip())
            else:
                item = self.table.item(r, 0)
                if item and item.text().strip():
                    used.add(item.text().strip())
        return used

    def _populate_combo_items(self, combo: QComboBox, current_val: str, row_idx: int) -> None:
        combo.blockSignals(True)
        combo.clear()
        used = self._get_used_keys(exclude_row=row_idx)
        available = [c for c in self._key_suggestions if c not in used or c == current_val]

        for cat in available:
            combo.addItem(cat)
            models = self._provenance.get(cat, [])
            if models:
                combo.setItemData(
                    combo.count() - 1,
                    f"From: {', '.join(models)}",
                    Qt.ItemDataRole.ToolTipRole,
                )
        combo.setCurrentText(current_val)
        combo.blockSignals(False)

    def _refresh_all_combos(self) -> None:
        if not self._use_combobox_for_key:
            return
        for r in range(self.table.rowCount()):
            combo: QComboBox | None = self.table.cellWidget(r, 0)
            if combo:
                self._populate_combo_items(combo, combo.currentText(), r)

    def set_key_suggestions(self, suggestions: Sequence[str] | Mapping[str, Sequence[str]]) -> None:
        if isinstance(suggestions, Mapping):
            self._key_suggestions = sorted(suggestions.keys())
            self._provenance = {k: list(v) for k, v in suggestions.items()}
        else:
            self._key_suggestions = sorted(dict.fromkeys(suggestions))
            self._provenance = {}
        self._refresh_all_combos()

    def get_mapping(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for row in range(self.table.rowCount()):
            if self._use_combobox_for_key:
                combo: QComboBox | None = self.table.cellWidget(row, 0)
                key = combo.currentText().strip() if combo else ""
            else:
                key_item = self.table.item(row, 0)
                key = key_item.text().strip() if key_item else ""

            val_item = self.table.item(row, 1)
            val = val_item.text().strip() if val_item else ""
            if key:
                mapping[key] = val
        return mapping

    def set_mapping(self, mapping: Mapping[str, str]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        for row, (k, v) in enumerate(mapping.items()):
            self.table.insertRow(row)
            if self._use_combobox_for_key:
                combo = SuggestionComboBox(self)
                combo.setEditable(True)
                self._populate_combo_items(combo, str(k), row)
                combo.about_to_show_popup.connect(
                    lambda c=combo, r=row: self._populate_combo_items(c, c.currentText(), r)
                )
                combo.currentTextChanged.connect(lambda _: self._on_key_changed())
                self.table.setCellWidget(row, 0, combo)
            else:
                self.table.setItem(row, 0, QTableWidgetItem(str(k)))

            self.table.setItem(row, 1, QTableWidgetItem(str(v)))

        self._adjust_height()
        self.table.blockSignals(False)
        self._refresh_all_combos()

    def _on_key_changed(self) -> None:
        self._refresh_all_combos()
        self.changed.emit()

    def _on_add_row(self) -> None:
        self.table.blockSignals(True)
        row = self.table.rowCount()
        self.table.insertRow(row)

        if self._use_combobox_for_key:
            used = self._get_used_keys()
            next_cat = next((c for c in self._key_suggestions if c not in used), "")
            combo = SuggestionComboBox(self)
            combo.setEditable(True)
            self._populate_combo_items(combo, next_cat, row)
            combo.about_to_show_popup.connect(lambda c=combo, r=row: self._populate_combo_items(c, c.currentText(), r))
            combo.currentTextChanged.connect(lambda _: self._on_key_changed())
            self.table.setCellWidget(row, 0, combo)
        else:
            self.table.setItem(row, 0, QTableWidgetItem(""))

        self.table.setItem(row, 1, QTableWidgetItem(""))
        self._adjust_height()
        self.table.blockSignals(False)
        self._refresh_all_combos()

        if not self._use_combobox_for_key:
            item = self.table.item(row, 0)
            self.table.setCurrentItem(item)
            self.table.editItem(item)
        self.changed.emit()

    def _on_remove_row(self) -> None:
        selected_rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        if not selected_rows:
            return

        self.table.blockSignals(True)
        for row in selected_rows:
            self.table.removeRow(row)
        self._adjust_height()
        self.table.blockSignals(False)
        self._refresh_all_combos()
        self.changed.emit()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        del item
        self.changed.emit()


# endregion


# region Threshold Table Editor


class ThresholdTableEditor(QWidget):
    """
    A specialized 3-column table for category_thresholds and tag_thresholds.
    Columns: [Target, Threshold, Override TLT].
    Pre-populates new rows with next unused category and prevents duplicate selections.
    """

    changed = Signal()

    def __init__(
        self,
        target_header: str = "Target",
        use_combobox_for_target: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._target_header = target_header
        self._use_combobox_for_target = use_combobox_for_target
        self._target_suggestions: list[str] = []
        self._provenance: dict[str, list[str]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels([self._target_header, "Threshold", "Override TLT"])
        self.table.horizontalHeader().setStyleSheet("QHeaderView::section { padding: 4px 10px; }")
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 95)
        self.table.setColumnWidth(2, 110)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._on_table_item_changed)
        self._adjust_height()
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)
        self.add_btn = QPushButton("+ Add Entry", self)
        self.add_btn.clicked.connect(self._on_add_row)
        btn_layout.addWidget(self.add_btn)

        self.remove_btn = QPushButton("- Remove Selected", self)
        self.remove_btn.clicked.connect(self._on_remove_row)
        btn_layout.addWidget(self.remove_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def _adjust_height(self) -> None:
        rows = self.table.rowCount()
        target = max(86, min(32 + max(rows, 1) * 30, 210))
        self.table.setFixedHeight(target)

    def _get_used_targets(self, exclude_row: int = -1) -> set[str]:
        used = set()
        for r in range(self.table.rowCount()):
            if r == exclude_row:
                continue
            if self._use_combobox_for_target:
                combo: QComboBox | None = self.table.cellWidget(r, 0)
                if combo and combo.currentText().strip():
                    used.add(combo.currentText().strip())
            else:
                item = self.table.item(r, 0)
                if item and item.text().strip():
                    used.add(item.text().strip())
        return used

    def _populate_combo_items(self, combo: QComboBox, current_val: str, row_idx: int) -> None:
        combo.blockSignals(True)
        combo.clear()
        used = self._get_used_targets(exclude_row=row_idx)
        available = [c for c in self._target_suggestions if c not in used or c == current_val]

        for cat in available:
            combo.addItem(cat)
            models = self._provenance.get(cat, [])
            if models:
                combo.setItemData(
                    combo.count() - 1,
                    f"From: {', '.join(models)}",
                    Qt.ItemDataRole.ToolTipRole,
                )
        combo.setCurrentText(current_val)
        combo.blockSignals(False)

    def _refresh_all_combos(self) -> None:
        if not self._use_combobox_for_target:
            return
        for r in range(self.table.rowCount()):
            combo: QComboBox | None = self.table.cellWidget(r, 0)
            if combo:
                self._populate_combo_items(combo, combo.currentText(), r)

    def set_target_suggestions(self, suggestions: Sequence[str] | Mapping[str, Sequence[str]]) -> None:
        if isinstance(suggestions, Mapping):
            self._target_suggestions = sorted(suggestions.keys())
            self._provenance = {k: list(v) for k, v in suggestions.items()}
        else:
            self._target_suggestions = sorted(dict.fromkeys(suggestions))
            self._provenance = {}
        self._refresh_all_combos()

    def get_thresholds(self) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for row in range(self.table.rowCount()):
            if self._use_combobox_for_target:
                combo: QComboBox | None = self.table.cellWidget(row, 0)
                target = combo.currentText().strip() if combo else ""
            else:
                target_item = self.table.item(row, 0)
                target = target_item.text().strip() if target_item else ""

            if not target:
                continue

            spin: QDoubleSpinBox | None = self.table.cellWidget(row, 1)
            thresh_val = spin.value() if spin else 0.40

            chk_container: QWidget | None = self.table.cellWidget(row, 2)
            override_val = False
            if chk_container:
                chk = chk_container.findChild(QCheckBox)
                if chk:
                    override_val = chk.isChecked()

            results[target] = {
                "threshold": float(thresh_val),
                "override_tlt": bool(override_val),
            }
        return results

    def set_thresholds(self, data: Mapping[str, Any]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        for row, (target, conf) in enumerate(data.items()):
            self.table.insertRow(row)

            if self._use_combobox_for_target:
                combo = SuggestionComboBox(self)
                combo.setEditable(True)
                self._populate_combo_items(combo, str(target), row)
                combo.about_to_show_popup.connect(
                    lambda c=combo, r=row: self._populate_combo_items(c, c.currentText(), r)
                )
                combo.currentTextChanged.connect(lambda _: self._on_target_changed())
                self.table.setCellWidget(row, 0, combo)
            else:
                self.table.setItem(row, 0, QTableWidgetItem(str(target)))

            val = conf.threshold if hasattr(conf, "threshold") else conf.get("threshold", 0.40)
            spin = QDoubleSpinBox(self)
            spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            spin.setRange(0.0, 1.0)
            spin.setSingleStep(0.05)
            spin.setDecimals(2)
            spin.setValue(float(val))
            spin.valueChanged.connect(lambda _: self.changed.emit())
            self.table.setCellWidget(row, 1, spin)

            ovr = conf.override_tlt if hasattr(conf, "override_tlt") else conf.get("override_tlt", False)
            chk = QCheckBox(self)
            chk.setChecked(bool(ovr))
            chk.toggled.connect(lambda _: self.changed.emit())

            chk_container = QWidget(self)
            chk_layout = QHBoxLayout(chk_container)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_layout.addWidget(chk)
            self.table.setCellWidget(row, 2, chk_container)

        self._adjust_height()
        self.table.blockSignals(False)
        self._refresh_all_combos()

    def _on_target_changed(self) -> None:
        self._refresh_all_combos()
        self.changed.emit()

    def _on_add_row(self) -> None:
        row = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.insertRow(row)

        if self._use_combobox_for_target:
            used = self._get_used_targets()
            next_cat = next((c for c in self._target_suggestions if c not in used), "")
            combo = SuggestionComboBox(self)
            combo.setEditable(True)
            self._populate_combo_items(combo, next_cat, row)
            combo.about_to_show_popup.connect(lambda c=combo, r=row: self._populate_combo_items(c, c.currentText(), r))
            combo.currentTextChanged.connect(lambda _: self._on_target_changed())
            self.table.setCellWidget(row, 0, combo)
        else:
            self.table.setItem(row, 0, QTableWidgetItem(""))

        spin = QDoubleSpinBox(self)
        spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        spin.setRange(0.0, 1.0)
        spin.setSingleStep(0.05)
        spin.setDecimals(2)
        spin.setValue(0.50)
        spin.valueChanged.connect(lambda _: self.changed.emit())
        self.table.setCellWidget(row, 1, spin)

        chk = QCheckBox(self)
        chk.setChecked(False)
        chk.toggled.connect(lambda _: self.changed.emit())

        chk_container = QWidget(self)
        chk_layout = QHBoxLayout(chk_container)
        chk_layout.setContentsMargins(0, 0, 0, 0)
        chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chk_layout.addWidget(chk)

        self.table.setCellWidget(row, 2, chk_container)
        self._adjust_height()
        self.table.blockSignals(False)
        self._refresh_all_combos()

        if not self._use_combobox_for_target:
            item = self.table.item(row, 0)
            self.table.setCurrentItem(item)
            self.table.editItem(item)
        self.changed.emit()

    def _on_remove_row(self) -> None:
        selected_rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        if not selected_rows:
            return

        self.table.blockSignals(True)
        for row in selected_rows:
            self.table.removeRow(row)
        self._adjust_height()
        self.table.blockSignals(False)
        self._refresh_all_combos()
        self.changed.emit()

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        del item
        self.changed.emit()


# endregion


# region Category Limit Editor


class CategoryLimitEditor(QWidget):
    """
    Table editor for max_tags_per_category: [Category (Combo/Text), Limit (SpinBox)].
    Pre-populates new rows with next unused category and prevents duplicate selections.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._suggestions: list[str] = []
        self._provenance: dict[str, list[str]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Category", "Max Tags"])
        self.table.horizontalHeader().setStyleSheet("QHeaderView::section { padding: 4px 10px; }")
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 100)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._adjust_height()
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("+ Add Category Limit", self)
        self.add_btn.clicked.connect(self._on_add_row)
        btn_layout.addWidget(self.add_btn)

        self.remove_btn = QPushButton("- Remove Selected", self)
        self.remove_btn.clicked.connect(self._on_remove_row)
        btn_layout.addWidget(self.remove_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def _adjust_height(self) -> None:
        rows = self.table.rowCount()
        target = max(86, min(32 + max(rows, 1) * 30, 210))
        self.table.setFixedHeight(target)

    def _get_used_categories(self, exclude_row: int = -1) -> set[str]:
        used = set()
        for r in range(self.table.rowCount()):
            if r == exclude_row:
                continue
            combo: QComboBox | None = self.table.cellWidget(r, 0)
            if combo and combo.currentText().strip():
                used.add(combo.currentText().strip())
        return used

    def _populate_combo_items(self, combo: QComboBox, current_val: str, row_idx: int) -> None:
        combo.blockSignals(True)
        combo.clear()
        used = self._get_used_categories(exclude_row=row_idx)
        available = [c for c in self._suggestions if c not in used or c == current_val]

        for cat in available:
            combo.addItem(cat)
            models = self._provenance.get(cat, [])
            if models:
                combo.setItemData(
                    combo.count() - 1,
                    f"From: {', '.join(models)}",
                    Qt.ItemDataRole.ToolTipRole,
                )
        combo.setCurrentText(current_val)
        combo.blockSignals(False)

    def _refresh_all_combos(self) -> None:
        for r in range(self.table.rowCount()):
            combo: QComboBox | None = self.table.cellWidget(r, 0)
            if combo:
                self._populate_combo_items(combo, combo.currentText(), r)

    def set_suggestions(self, suggestions: Sequence[str] | Mapping[str, Sequence[str]]) -> None:
        if isinstance(suggestions, Mapping):
            self._suggestions = sorted(suggestions.keys())
            self._provenance = {k: list(v) for k, v in suggestions.items()}
        else:
            self._suggestions = sorted(dict.fromkeys(suggestions))
            self._provenance = {}
        self._refresh_all_combos()

    def get_limits(self) -> dict[str, int]:
        limits: dict[str, int] = {}
        for row in range(self.table.rowCount()):
            combo: QComboBox | None = self.table.cellWidget(row, 0)
            cat = combo.currentText().strip() if combo else ""
            spin: QSpinBox | None = self.table.cellWidget(row, 1)
            val = spin.value() if spin else 1
            if cat:
                limits[cat] = val
        return limits

    def set_limits(self, limits: Mapping[str, int]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for row, (cat, val) in enumerate(limits.items()):
            self._insert_row(row, cat, int(val))
        self._adjust_height()
        self.table.blockSignals(False)
        self._refresh_all_combos()

    def _insert_row(self, row: int, category: str = "", limit: int = 10) -> None:
        self.table.insertRow(row)
        combo = SuggestionComboBox(self)
        combo.setEditable(True)
        self._populate_combo_items(combo, category, row)
        combo.about_to_show_popup.connect(lambda c=combo, r=row: self._populate_combo_items(c, c.currentText(), r))
        combo.currentTextChanged.connect(lambda _: self._on_category_changed())
        self.table.setCellWidget(row, 0, combo)

        spin = QSpinBox(self)
        spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        spin.setRange(1, 9999)
        spin.setValue(limit)
        spin.valueChanged.connect(lambda _: self.changed.emit())
        self.table.setCellWidget(row, 1, spin)

    def _on_category_changed(self) -> None:
        self._refresh_all_combos()
        self.changed.emit()

    def _on_add_row(self) -> None:
        self.table.blockSignals(True)
        row = self.table.rowCount()
        used = self._get_used_categories()
        next_cat = next((c for c in self._suggestions if c not in used), "")
        self._insert_row(row, next_cat, 10)
        self._adjust_height()
        self.table.blockSignals(False)
        self._refresh_all_combos()
        self.changed.emit()

    def _on_remove_row(self) -> None:
        selected_rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        if not selected_rows:
            return
        self.table.blockSignals(True)
        for row in selected_rows:
            self.table.removeRow(row)
        self._adjust_height()
        self.table.blockSignals(False)
        self._refresh_all_combos()
        self.changed.emit()


# endregion


# region Tag Subset List Editor (Joint Subset Limits)


class TagSubsetCard(QFrame):
    """
    A single card representing a Joint Subset Limit rule (max_tags_per_subset).
    Eliminates comma bugs by managing tags via StringListEditor (with multi-line paste).
    """

    changed = Signal()
    delete_requested = Signal()

    def __init__(self, index: int = 1, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._index = index

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(get_card_stylesheet(CardTheme.PAGE, "TagSubsetCard"))

        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(10, 8, 10, 10)
        card_layout.setSpacing(8)

        # 1. Header
        header_layout = QHBoxLayout()
        self.title_label = QLabel(f"<b>Subset Group #{self._index}</b>", self)
        self.title_label.setStyleSheet("font-size: 12px;")
        header_layout.addWidget(self.title_label)

        header_layout.addStretch(1)

        self.del_btn = QPushButton("✕", self)
        self.del_btn.setFixedWidth(26)
        self.del_btn.setToolTip("Remove this subset group")
        self.del_btn.setStyleSheet(
            "QPushButton { color: #888; font-weight: bold; border: 1px solid #444; border-radius: 3px; }"
            "QPushButton:hover { color: #d32f2f; border-color: #d32f2f; background: rgba(211, 47, 47, 0.1); }"
        )
        self.del_btn.clicked.connect(self.delete_requested.emit)
        header_layout.addWidget(self.del_btn)

        card_layout.addLayout(header_layout)

        # 2. Limit row
        limit_row = QHBoxLayout()
        limit_row.setSpacing(8)
        limit_lbl = QLabel("Max Output Limit:", self)
        limit_lbl.setStyleSheet("color: #b0bec5;")
        limit_row.addWidget(limit_lbl)

        self.limit_spin = QSpinBox(self)
        self.limit_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.limit_spin.setRange(1, 999)
        self.limit_spin.setValue(1)
        self.limit_spin.valueChanged.connect(lambda _: self.changed.emit())
        limit_row.addWidget(self.limit_spin)

        tags_suffix = QLabel("tag(s)", self)
        tags_suffix.setStyleSheet("color: #888;")
        limit_row.addWidget(tags_suffix)

        limit_row.addStretch(1)
        card_layout.addLayout(limit_row)

        # 3. Tags in subset
        t_label = QLabel("Tags in Subset:", self)
        t_label.setStyleSheet("color: #b0bec5;")
        card_layout.addWidget(t_label)

        self.tags_editor = StringListEditor(placeholder="Add tag to subset group...", parent=self)
        self.tags_editor.changed.connect(self.changed.emit)
        card_layout.addWidget(self.tags_editor)

    def set_index(self, index: int) -> None:
        self._index = index
        self.title_label.setText(f"<b>Subset Group #{self._index}</b>")

    def get_subset(self) -> dict[str, Any]:
        return {
            "tags": self.tags_editor.get_items(),
            "limit": int(self.limit_spin.value()),
        }

    def set_subset(self, tags: Sequence[str], limit: int = 1) -> None:
        self.tags_editor.set_items(tags)
        self.limit_spin.setValue(int(limit))


class TagSubsetListEditor(QWidget):
    """
    Stacked card editor for [[output_filter.max_tags_per_subset]].
    Eliminates the comma-splitting bug by using individual tag lists per group.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards: list[TagSubsetCard] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 1. Cards container
        self._cards_widget = QWidget(self)
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        layout.addWidget(self._cards_widget)

        # 2. Empty placeholder
        self.empty_label = QLabel("(No joint subset limits configured — click '+ Add Subset Group' below)", self)
        self.empty_label.setStyleSheet("color: #888; font-style: italic; padding: 4px;")
        layout.addWidget(self.empty_label)

        # 3. Add button
        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("+ Add Subset Group", self)
        self.add_btn.clicked.connect(self._on_add_clicked)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self._update_empty_state()

    def get_subsets(self) -> list[dict[str, Any]]:
        subsets: list[dict[str, Any]] = []
        for card in self._cards:
            s = card.get_subset()
            if s["tags"]:  # Only export groups that have at least one tag
                subsets.append(s)
        return subsets

    def set_subsets(self, subsets: Sequence[Any]) -> None:
        self.blockSignals(True)
        self._clear_cards()
        for idx, s in enumerate(subsets, start=1):
            tags = getattr(s, "tags", None) or (s.get("tags") if isinstance(s, dict) else [])
            limit = getattr(s, "limit", None) or (s.get("limit", 1) if isinstance(s, dict) else 1)
            self._add_card(tags, limit, idx)
        self._update_empty_state()
        self.blockSignals(False)

    def _clear_cards(self) -> None:
        for card in self._cards:
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

    def _add_card(
        self,
        tags: Sequence[str] | None = None,
        limit: int = 1,
        index: int | None = None,
    ) -> TagSubsetCard:
        idx = index or (len(self._cards) + 1)
        card = TagSubsetCard(index=idx, parent=self._cards_widget)
        if tags is not None:
            card.set_subset(tags, limit)
        card.changed.connect(self.changed.emit)
        card.delete_requested.connect(lambda: self._on_delete_card(card))
        self._cards_layout.addWidget(card)
        self._cards.append(card)
        self._update_empty_state()
        return card

    def _on_add_clicked(self) -> None:
        self._add_card()
        self.changed.emit()

    def _on_delete_card(self, card: TagSubsetCard) -> None:
        if card in self._cards:
            self._cards.remove(card)
            self._cards_layout.removeWidget(card)
            card.deleteLater()
            self._renumber_cards()
            self._update_empty_state()
            self.changed.emit()

    def _renumber_cards(self) -> None:
        for idx, card in enumerate(self._cards, start=1):
            card.set_index(idx)

    def _update_empty_state(self) -> None:
        self.empty_label.setVisible(len(self._cards) == 0)


# endregion


# region Tag Rule & Query Stacked Card Editors


class TagRuleCard(QFrame):
    """
    A single card mapping tags to a specific Hydrus service key.
    Used for:
      - [[hydrus.tag_queries]] (allow_search_all=True, theme=CardTheme.QUERY)
      - [[hydrus.add_tags]] (allow_search_all=False, theme=CardTheme.ADD)
      - [[hydrus.remove_tags]] (allow_search_all=False, theme=CardTheme.REMOVE)
    """

    changed = Signal()
    delete_requested = Signal()

    def __init__(
        self,
        index: int = 1,
        title_prefix: str = "Rule",
        tags_label: str = "Tags:",
        placeholder: str = "Enter tag and press Enter or Add...",
        allow_search_all: bool = False,
        theme: CardTheme = CardTheme.DEFAULT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._available_services: dict[str, Any] = {}
        self._index = index
        self._title_prefix = title_prefix
        self._allow_search_all = allow_search_all
        self._theme = theme

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(get_card_stylesheet(self._theme, "TagRuleCard"))

        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(10, 8, 10, 10)
        card_layout.setSpacing(8)

        # 1. Header row
        header_layout = QHBoxLayout()
        self.title_label = QLabel(f"<b>{self._title_prefix} #{self._index}</b>", self)
        self.title_label.setStyleSheet("font-size: 12px;")
        header_layout.addWidget(self.title_label)

        header_layout.addStretch(1)

        self.del_btn = QPushButton("✕", self)
        self.del_btn.setFixedWidth(26)
        self.del_btn.setToolTip("Remove this rule")
        self.del_btn.setStyleSheet(
            "QPushButton { color: #888; font-weight: bold; border: 1px solid #444; border-radius: 3px; }"
            "QPushButton:hover { color: #d32f2f; border-color: #d32f2f; background: rgba(211, 47, 47, 0.1); }"
        )
        self.del_btn.clicked.connect(self.delete_requested.emit)
        header_layout.addWidget(self.del_btn)

        card_layout.addLayout(header_layout)

        # 2. Target Service row
        svc_row = QHBoxLayout()
        svc_row.setSpacing(8)
        svc_label = QLabel("Target Service:", self)
        svc_label.setStyleSheet("color: #b0bec5;")
        svc_row.addWidget(svc_label)

        self.service_combo = QComboBox(self)
        self.service_combo.setEditable(False)
        self._repopulate_services()
        self.service_combo.currentIndexChanged.connect(lambda _: self.changed.emit())
        svc_row.addWidget(self.service_combo, stretch=1)

        card_layout.addLayout(svc_row)

        # 3. Tags list editor
        t_label = QLabel(tags_label, self)
        t_label.setStyleSheet("color: #b0bec5;")
        card_layout.addWidget(t_label)

        self.tags_editor = StringListEditor(placeholder=placeholder, parent=self)
        self.tags_editor.changed.connect(self.changed.emit)
        card_layout.addWidget(self.tags_editor)

    def set_index(self, index: int) -> None:
        self._index = index
        self.title_label.setText(f"<b>{self._title_prefix} #{self._index}</b>")

    def set_available_services(self, services: dict[str, Any]) -> None:
        self._available_services = dict(services)
        current_key = self.service_combo.currentData()
        self._repopulate_services(selected_key=str(current_key) if current_key else None)

    def _repopulate_services(self, selected_key: str | None = None) -> None:
        self.service_combo.blockSignals(True)
        self.service_combo.clear()

        # Offline State
        if not self._available_services:
            if selected_key and selected_key != HYDRUS_BUILTIN_ALL_KNOWN_TAGS_KEY:
                short_k = f"{selected_key[:8]}..." if len(selected_key) > 12 else selected_key
                self.service_combo.addItem(f"Service: {short_k} (Offline)", userData=selected_key)
            else:
                msg = "⚠ Connect to Hydrus to select service"
                self.service_combo.addItem(msg, userData="")

            self.service_combo.setCurrentIndex(0)
            self.service_combo.setEnabled(False)
            self.service_combo.blockSignals(False)
            return

        self.service_combo.setEnabled(True)

        # Allow Search All ("All Known Tags") only for Tag Queries
        if self._allow_search_all:
            all_known_info = self._available_services.get(HYDRUS_BUILTIN_ALL_KNOWN_TAGS_KEY)
            if all_known_info:
                name = (
                    all_known_info.get("name", "All Known Tags")
                    if isinstance(all_known_info, dict)
                    else str(all_known_info)
                )
                short_k = f"{HYDRUS_BUILTIN_ALL_KNOWN_TAGS_KEY[:8]}..."
                display = f"{name} (virtual)  ({short_k})"
            else:
                display = "All Known Tags (virtual)"
            self.service_combo.addItem(display, userData="")

        # Populate services
        for key, val in self._available_services.items():
            if self._allow_search_all and key == HYDRUS_BUILTIN_ALL_KNOWN_TAGS_KEY:
                continue

            if isinstance(val, dict):
                name = val.get("name", key)
                stype = val.get("type")
                is_virtual = bool(val.get("is_virtual", False) or stype == 10)
            else:
                name = str(val)
                is_virtual = False

            if not self._allow_search_all and is_virtual:
                continue

            virt_label = " (virtual)" if is_virtual else ""
            short_key = f"{key[:8]}..." if len(key) > 12 else key
            self.service_combo.addItem(f"{name}{virt_label}  ({short_key})", userData=key)

        # Resolve selection
        if selected_key:
            if self._allow_search_all and (selected_key == HYDRUS_BUILTIN_ALL_KNOWN_TAGS_KEY or selected_key == ""):
                self.service_combo.setCurrentIndex(0)
            else:
                idx = self.service_combo.findData(selected_key)
                if idx >= 0:
                    self.service_combo.setCurrentIndex(idx)
                else:
                    short_key = f"{selected_key[:8]}..." if len(selected_key) > 12 else selected_key
                    self.service_combo.addItem(f"❌ Unrecognized Service  ({short_key})", userData=selected_key)
                    self.service_combo.setCurrentIndex(self.service_combo.count() - 1)
        else:
            self.service_combo.setCurrentIndex(0)

        self.service_combo.blockSignals(False)

    def get_rule(self) -> dict[str, Any]:
        tags = self.tags_editor.get_items()
        selected_key = str(self.service_combo.currentData() or "").strip()
        service_keys = [selected_key] if selected_key else []
        return {"tags": tags, "tag_service_keys": service_keys}

    def set_rule(self, tags: Sequence[Any], service_keys: Sequence[str]) -> None:
        self.tags_editor.set_items([str(t) for t in tags])
        key = service_keys[0] if service_keys else ""
        self._repopulate_services(selected_key=key)


class TagRuleListEditor(QWidget):
    """
    Stacked list editor for multi-rule tag blocks.
    Used for tag_queries, add_tags, and remove_tags.
    """

    changed = Signal()

    def __init__(
        self,
        title_prefix: str = "Rule",
        tags_label: str = "Tags:",
        placeholder: str = "Enter tag and press Enter or Add...",
        allow_search_all: bool = False,
        theme: CardTheme = CardTheme.DEFAULT,
        add_btn_text: str = "+ Add Rule",
        empty_text: str = "(No rules configured — click button below)",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cards: list[TagRuleCard] = []
        self._available_services: dict[str, Any] = {}
        self._title_prefix = title_prefix
        self._tags_label = tags_label
        self._placeholder = placeholder
        self._allow_search_all = allow_search_all
        self._theme = theme

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(8)

        # 1. Cards container
        self._cards_widget = QWidget(self)
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        self._root_layout.addWidget(self._cards_widget)

        # 2. Empty placeholder
        self.empty_label = QLabel(empty_text, self)
        self.empty_label.setStyleSheet("color: #888; font-style: italic; padding: 4px;")
        self._root_layout.addWidget(self.empty_label)

        # 3. Add button
        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton(add_btn_text, self)
        self.add_btn.clicked.connect(self._on_add_clicked)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addStretch()
        self._root_layout.addLayout(btn_layout)

        self._update_empty_state()

    def set_available_services(self, services: dict[str, Any]) -> None:
        self._available_services = dict(services)
        for card in self._cards:
            card.set_available_services(services)

    def get_rules(self) -> list[dict[str, Any]]:
        rules: list[dict[str, Any]] = []
        for card in self._cards:
            r = card.get_rule()
            if r["tags"]:  # Only export rules that have at least one tag
                rules.append(r)
        return rules

    def set_rules(self, rules: Sequence[Any]) -> None:
        self.blockSignals(True)
        self._clear_cards()
        for idx, r in enumerate(rules, start=1):
            tags = getattr(r, "tags", None) or (r.get("tags") if isinstance(r, dict) else [])
            keys = getattr(r, "tag_service_keys", None) or (r.get("tag_service_keys") if isinstance(r, dict) else [])
            self._add_card(tags, keys, idx)
        self._update_empty_state()
        self.blockSignals(False)

    def _clear_cards(self) -> None:
        for card in self._cards:
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

    def _add_card(
        self,
        tags: Sequence[Any] | None = None,
        keys: Sequence[str] | None = None,
        index: int | None = None,
    ) -> TagRuleCard:
        idx = index or (len(self._cards) + 1)
        card = TagRuleCard(
            index=idx,
            title_prefix=self._title_prefix,
            tags_label=self._tags_label,
            placeholder=self._placeholder,
            allow_search_all=self._allow_search_all,
            theme=self._theme,
            parent=self._cards_widget,
        )
        card.set_available_services(self._available_services)
        if tags is not None:
            card.set_rule(tags, keys or [])
        else:
            card.set_rule([], [])

        card.changed.connect(self.changed.emit)
        card.delete_requested.connect(lambda: self._on_delete_card(card))

        self._cards_layout.addWidget(card)
        self._cards.append(card)
        self._update_empty_state()
        return card

    def _on_add_clicked(self) -> None:
        self._add_card()
        self.changed.emit()

    def _on_delete_card(self, card: TagRuleCard) -> None:
        if card in self._cards:
            self._cards.remove(card)
            self._cards_layout.removeWidget(card)
            card.deleteLater()
            self._renumber_cards()
            self._update_empty_state()
            self.changed.emit()

    def _renumber_cards(self) -> None:
        for idx, card in enumerate(self._cards, start=1):
            card.set_index(idx)

    def _update_empty_state(self) -> None:
        self.empty_label.setVisible(len(self._cards) == 0)


class TagQueryListEditor(TagRuleListEditor):
    """Convenience subclass for [[hydrus.tag_queries]] with CardTheme.QUERY (Cyan)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            title_prefix="Query",
            tags_label="Search Tags:",
            placeholder="Enter search tag and press Enter or Add...",
            allow_search_all=True,
            theme=CardTheme.QUERY,
            add_btn_text="+ Add Tag Query",
            empty_text="(No tag queries configured — click '+ Add Tag Query' below)",
            parent=parent,
        )

    # Maintain method aliases
    get_queries = TagRuleListEditor.get_rules
    set_queries = TagRuleListEditor.set_rules


# endregion


# region Page Query List Editor


class PageQueryListEditor(QWidget):
    """
    Stacked row editor for [[hydrus.page_queries]].
    Displays open media pages with automatic disambiguation indices and file count previews.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._available_pages: list[dict[str, Any]] = []
        self._row_widgets: list[QWidget] = []

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(6)

        # 1. Stack container
        self._stack_widget = QWidget(self)
        self._stack_layout = QVBoxLayout(self._stack_widget)
        self._stack_layout.setContentsMargins(0, 0, 0, 0)
        self._stack_layout.setSpacing(6)
        self._root_layout.addWidget(self._stack_widget)

        # 2. Empty placeholder label
        self.empty_label = QLabel("(No page queries configured — click '+ Add Page Query' below)", self)
        self.empty_label.setStyleSheet("color: #888; font-style: italic; padding: 4px;")
        self._root_layout.addWidget(self.empty_label)

        # 3. Add button
        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("+ Add Page Query", self)
        self.add_btn.clicked.connect(self._on_add_clicked)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addStretch()
        self._root_layout.addLayout(btn_layout)

        self._update_empty_state()

    def set_available_pages(self, pages: list[dict[str, Any]]) -> None:
        self._available_pages = list(pages)
        for row_widget in self._row_widgets:
            combo: QComboBox | None = row_widget.findChild(QComboBox)
            if not combo:
                continue
            curr_data = combo.currentData()
            self._repopulate_combo(combo, selected_target=curr_data if isinstance(curr_data, dict) else None)

    def get_queries(self) -> list[dict[str, Any]]:
        queries: list[dict[str, Any]] = []
        for row_widget in self._row_widgets:
            combo: QComboBox | None = row_widget.findChild(QComboBox)
            if combo:
                data = combo.currentData()
                if isinstance(data, dict) and data.get("name"):
                    q: dict[str, Any] = {"name": data["name"]}
                    if data.get("index") is not None:
                        q["index"] = int(data["index"])
                    queries.append(q)
        return queries

    def set_queries(self, queries: Sequence[Any]) -> None:
        self.blockSignals(True)
        self._clear_rows()
        for q in queries:
            name = getattr(q, "name", None) or (q.get("name") if isinstance(q, dict) else "")
            idx = (
                getattr(q, "index", None) if hasattr(q, "index") else (q.get("index") if isinstance(q, dict) else None)
            )
            if name:
                self._add_row({"name": str(name), "index": idx})
        self._update_empty_state()
        self.blockSignals(False)

    def _clear_rows(self) -> None:
        for row in self._row_widgets:
            self._stack_layout.removeWidget(row)
            row.deleteLater()
        self._row_widgets.clear()

    def _repopulate_combo(self, combo: QComboBox, selected_target: dict[str, Any] | None = None) -> None:
        combo.blockSignals(True)
        combo.clear()

        # Offline / No pages open
        if not self._available_pages:
            if selected_target:
                name = selected_target.get("name", "Unknown Page")
                idx = selected_target.get("index")
                idx_str = f" [index {idx}]" if idx is not None else ""
                combo.addItem(f"{name}{idx_str} (Offline / Closed)", userData=selected_target)
                combo.setCurrentIndex(0)
                combo.setEnabled(False)
            else:
                combo.addItem("⚠ Connect to Hydrus with open media tabs", userData=None)
                combo.setCurrentIndex(0)
                combo.setEnabled(False)
            combo.blockSignals(False)
            return

        combo.setEnabled(True)

        # Populate open media pages
        for p in self._available_pages:
            name = p["name"]
            idx = p["index"]
            raw_idx = p.get("raw_index", 0)
            has_dups = p.get("has_duplicates", False)
            num_files = p.get("num_files")

            files_str = f" · {num_files} files" if num_files is not None else ""
            tab_str = (
                f" (Tab #{raw_idx + 1}{files_str})"
                if has_dups
                else (f" ({num_files} files)" if num_files is not None else "")
            )
            display = f"{name}{tab_str}"

            combo.addItem(display, userData={"name": name, "index": idx})

        # Selection resolution
        if selected_target:
            target_name = selected_target.get("name")
            target_idx = selected_target.get("index")

            found_idx = -1
            for i in range(combo.count()):
                d = combo.itemData(i)
                if (
                    isinstance(d, dict)
                    and d.get("name") == target_name
                    and (target_idx is None or d.get("index") == target_idx)
                ):
                    found_idx = i
                    break

            if found_idx >= 0:
                combo.setCurrentIndex(found_idx)
            else:
                idx_str = f" [index {target_idx}]" if target_idx is not None else ""
                combo.addItem(f"{target_name}{idx_str} (Tab Closed in Hydrus)", userData=selected_target)
                combo.setCurrentIndex(combo.count() - 1)
        else:
            combo.setCurrentIndex(0)

        combo.blockSignals(False)

    def _add_row(self, initial_target: dict[str, Any] | None = None) -> QWidget:
        row_widget = QWidget(self._stack_widget)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        combo = QComboBox(row_widget)
        combo.setEditable(False)
        self._repopulate_combo(combo, selected_target=initial_target)
        combo.currentIndexChanged.connect(lambda _: self.changed.emit())
        row_layout.addWidget(combo, stretch=1)

        del_btn = QPushButton("✕", row_widget)
        del_btn.setFixedWidth(28)
        del_btn.setToolTip("Remove this page query")
        del_btn.setStyleSheet(
            "QPushButton { color: #888; font-weight: bold; border: 1px solid #444; border-radius: 3px; }"
            "QPushButton:hover { color: #d32f2f; border-color: #d32f2f; background: rgba(211, 47, 47, 0.1); }"
        )
        del_btn.clicked.connect(lambda: self._on_remove_row(row_widget))
        row_layout.addWidget(del_btn)

        self._stack_layout.addWidget(row_widget)
        self._row_widgets.append(row_widget)
        self._update_empty_state()
        return row_widget

    def _on_add_clicked(self) -> None:
        self._add_row()
        self.changed.emit()

    def _on_remove_row(self, row_widget: QWidget) -> None:
        if row_widget in self._row_widgets:
            self._row_widgets.remove(row_widget)
            self._stack_layout.removeWidget(row_widget)
            row_widget.deleteLater()
            self._update_empty_state()
            self.changed.emit()

    def _update_empty_state(self) -> None:
        self.empty_label.setVisible(len(self._row_widgets) == 0)


# endregion


# region SuggestionComboBox


class SuggestionComboBox(QComboBox):
    """QComboBox that notifies listeners right before showing its dropdown popup and enforces StrongFocus."""

    about_to_show_popup = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Never allow mouse wheel to steal focus
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def showPopup(self) -> None:
        self.about_to_show_popup.emit()
        super().showPopup()


# endregion


# region Smooth Scrolling Area


class SmoothScrollArea(QScrollArea):
    """
    Momentum-based smooth scrolling area with proactive Safe-Scroll filtering.

    Safe-Scroll Rules:
      1. Proactively strips WheelFocus from all child spinboxes and comboboxes,
         preventing the mouse wheel from auto-focusing them on first touch.
      2. Non-editable QComboBox: Wheel never cycles options; smoothly scrolls the page.
      3. Spinboxes & Editable ComboBoxes: Wheel only changes value if explicitly clicked/focused;
         otherwise smoothly scrolls the page.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Bind the animation to the vertical scrollbar's 'value' property
        self._anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setDuration(300)  # 300ms animation duration feels snappy but smooth
        self._target_value = 0.0

        # Install filter on the scrollbar itself to fix instant-jumps on hover
        self.verticalScrollBar().installEventFilter(self)

        # Global event filter to intercept wheel events before delivery
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

    def setWidget(self, widget: QWidget) -> None:
        super().setWidget(widget)
        self._neutralize_child_wheel_focus(widget)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        w = self.widget()
        if w:
            self._neutralize_child_wheel_focus(w)

    def _neutralize_child_wheel_focus(self, container: QWidget) -> None:
        """Strip Qt.WheelFocus from all child inputs so wheel rotation never triggers focus."""
        for spin in container.findChildren(QAbstractSpinBox):
            if spin.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        for combo in container.findChildren(QComboBox):
            if combo.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(event, QWheelEvent):
            # 1. Scrollbar hover reroute
            if obj == self.verticalScrollBar():
                self.wheelEvent(event)  # Reroute to smooth scroll
                return True

            # 2. Only process widgets inside this scroll area
            if isinstance(obj, QWidget) and self.isAncestorOf(obj):
                target_input: QWidget | None = None
                curr: QObject | None = obj
                while curr is not None and isinstance(curr, QWidget) and self.isAncestorOf(curr):
                    if isinstance(curr, (QAbstractSpinBox, QComboBox)):
                        target_input = curr
                        break
                    curr = curr.parent()

                if target_input is not None:
                    # Ensure StrongFocus is active (never auto-focus on wheel)
                    if target_input.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                        target_input.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

                    # Non-editable ComboBox: never cycle with wheel on closed widget
                    if isinstance(target_input, QComboBox) and not target_input.isEditable():
                        self.wheelEvent(event)
                        return True

                    # SpinBox or Editable ComboBox: only allow if user explicitly focused it
                    active_focus = QApplication.focusWidget()
                    is_focused = active_focus is not None and (
                        active_focus == target_input or target_input.isAncestorOf(active_focus)
                    )

                    if not is_focused:
                        self.wheelEvent(event)  # Steal the event to continue the smooth glide
                        return True
                    else:
                        return False

                # Prevent child widgets from interrupting an active glide animation
                if self._anim.state() == QPropertyAnimation.State.Running:
                    self.wheelEvent(event)
                    return True

        return super().eventFilter(obj, event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return

        vbar = self.verticalScrollBar()

        # If animation is stopped, our baseline target is the current visual position
        if self._anim.state() != QPropertyAnimation.State.Running:
            self._target_value = vbar.value()

        # Each notch (120 delta) scrolls a certain amount.
        # Tuning: vbar.singleStep() * 3.5 is roughly standard OS scroll speed.
        step = vbar.singleStep() * 5.25 * (delta / 120.0)

        self._target_value -= step
        self._target_value = max(vbar.minimum(), min(self._target_value, vbar.maximum()))

        self._anim.stop()
        self._anim.setStartValue(vbar.value())
        self._anim.setEndValue(self._target_value)
        self._anim.start()

        event.accept()


# endregion
