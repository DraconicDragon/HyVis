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

STYLE_OVERRIDDEN = "border: 1.5px solid #38bdf8 !important; background-color: rgba(56, 189, 248, 0.08) !important;"
STYLE_ERROR = "border: 1.5px solid #f85149 !important; background-color: rgba(248, 81, 73, 0.08) !important;"


def set_widget_override_state(widget: QWidget, is_overridden: bool, is_error: bool = False) -> None:
    """Apply or clear visual override/error highlighting on an input widget or table/list."""
    target = widget
    if hasattr(widget, "list_widget"):
        target = widget.list_widget
    elif hasattr(widget, "table"):
        target = widget.table

    if is_error:
        target.setStyleSheet(STYLE_ERROR)
    elif is_overridden:
        target.setStyleSheet(STYLE_OVERRIDDEN)
    else:
        target.setStyleSheet("")


def add_form_row(
    layout: QFormLayout,
    field_info: Any,
    widget: QWidget,
    label_text: str | None = None,
) -> QLabel:
    """
    Add a row to a QFormLayout, assigning field_info.description tooltip
    to BOTH the newly created QLabel and the input widget simultaneously.
    """
    title = label_text or getattr(field_info, "title", None) or "Field"
    label = QLabel(f"{title}:")
    setup_field_tooltip(label, field_info)
    bind_field_metadata(widget, field_info)
    layout.addRow(label, widget)
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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._is_checkable = False

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

    def setContentLayout(self, layout: QFormLayout | QVBoxLayout | QHBoxLayout) -> None:
        """Replace internal content layout with a specialized layout."""
        QWidget().setLayout(self._content_layout)
        self._content_layout = layout  # type: ignore[assignment]
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

        # 1. List view
        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.setMinimumHeight(95)
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
        self.blockSignals(False)

    def _on_add(self) -> None:
        text = self.input_line.text().strip()
        if not text:
            return

        existing = set(self.get_items())
        if text not in existing:
            self.list_widget.addItem(QListWidgetItem(text))
            self.input_line.clear()
            self.changed.emit()

    def _on_remove(self) -> None:
        selected = self.list_widget.selectedItems()
        if not selected:
            return

        for item in selected:
            row = self.list_widget.row(item)
            self.list_widget.takeItem(row)

        self.changed.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self._on_remove()
        else:
            super().keyPressEvent(event)


# endregion


# region Tag Service List Editor


class TagServiceListEditor(QWidget):
    """
    Stacked row editor for Hydrus tag service keys.
    Displays human-readable service names with underlying hex keys.
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
            self._repopulate_combo(combo, selected_key=current_key)

    def get_items(self) -> list[str]:
        keys: list[str] = []
        for row_widget in self._row_widgets:
            combo: QComboBox | None = row_widget.findChild(QComboBox)
            if combo:
                key = str(combo.currentData() or combo.currentText()).strip()
                if key:
                    keys.append(key)
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
                display = f"Unknown Service  ({short_key})"
                combo.addItem(display, userData=selected_key)
                combo.setCurrentIndex(combo.count() - 1)

        combo.blockSignals(False)

    def _add_row(self, initial_key: str | None = None) -> QWidget:
        row_widget = QWidget(self._stack_widget)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        combo = QComboBox(row_widget)
        combo.setEditable(True)
        self._repopulate_combo(combo, selected_key=initial_key)

        if initial_key is None and self._available_services:
            existing_keys = set(self.get_items())
            for key in self._available_services:
                if key not in existing_keys:
                    idx = combo.findData(key)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                    break

        combo.currentIndexChanged.connect(lambda _: self.changed.emit())
        line_edit = combo.lineEdit()
        if line_edit is not None:
            line_edit.editingFinished.connect(lambda: self._on_combo_edited(combo))
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

    def _on_combo_edited(self, combo: QComboBox) -> None:
        text = combo.currentText().strip()
        if text and combo.findData(text) < 0:
            combo.setItemData(combo.currentIndex(), text)
        self.changed.emit()

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
    Used for category_tag_prefix_mapping, tag_prefix_overrides, and tag_replacements.
    """

    changed = Signal()

    def __init__(
        self,
        key_header: str = "Key",
        val_header: str = "Value",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._key_header = key_header
        self._val_header = val_header

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 1. Table
        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels([self._key_header, self._val_header])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table)

        # 2. Add / Remove Buttons
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

    def get_mapping(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for row in range(self.table.rowCount()):
            key_item = self.table.item(row, 0)
            val_item = self.table.item(row, 1)
            key = key_item.text().strip() if key_item else ""
            val = val_item.text().strip() if val_item else ""
            if key:
                mapping[key] = val
        return mapping

    def set_mapping(self, mapping: Mapping[str, str]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        for row, (k, v) in enumerate(mapping.items()):
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(k)))
            self.table.setItem(row, 1, QTableWidgetItem(str(v)))

        self.table.blockSignals(False)

    def _on_add_row(self) -> None:
        self.table.blockSignals(True)
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(""))
        self.table.setItem(row, 1, QTableWidgetItem(""))
        self.table.blockSignals(False)

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
        self.table.blockSignals(False)
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
    """

    changed = Signal()

    def __init__(
        self,
        target_header: str = "Target",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._target_header = target_header

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 1. Table
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels([self._target_header, "Threshold", "Override TLT"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._on_table_item_changed)
        layout.addWidget(self.table)

        # 2. Action buttons
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

    def get_thresholds(self) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}

        for row in range(self.table.rowCount()):
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
            self.table.setItem(row, 0, QTableWidgetItem(str(target)))

            val = conf.threshold if hasattr(conf, "threshold") else conf.get("threshold", 0.40)
            spin = QDoubleSpinBox(self)
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

        self.table.blockSignals(False)

    def _on_add_row(self) -> None:
        row = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.insertRow(row)

        self.table.setItem(row, 0, QTableWidgetItem(""))

        spin = QDoubleSpinBox(self)
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
        self.table.blockSignals(False)

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
        self.table.blockSignals(False)
        self.changed.emit()

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        del item
        self.changed.emit()


# endregion


# region Subset List Editor


class SubsetListEditor(QWidget):
    """
    Table editor for max_tags_per_subset rule groups.
    Columns: [Tags (comma-separated), Limit (SpinBox)].
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Tags (comma-separated)", "Limit"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)

        self.add_btn = QPushButton("+ Add Subset Group", self)
        self.add_btn.clicked.connect(self._on_add_row)
        btn_layout.addWidget(self.add_btn)

        self.remove_btn = QPushButton("- Remove Selected", self)
        self.remove_btn.clicked.connect(self._on_remove_row)
        btn_layout.addWidget(self.remove_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def get_subsets(self) -> list[dict[str, Any]]:
        subsets: list[dict[str, Any]] = []

        for row in range(self.table.rowCount()):
            tag_item = self.table.item(row, 0)
            raw_text = tag_item.text().strip() if tag_item else ""
            if not raw_text:
                continue

            tags = [t.strip() for t in raw_text.split(",") if t.strip()]
            if not tags:
                continue

            spin: QSpinBox | None = self.table.cellWidget(row, 1)
            limit = spin.value() if spin else 1

            subsets.append({"tags": tags, "limit": limit})

        return subsets

    def set_subsets(self, subsets: Sequence[Any]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        for row, subset in enumerate(subsets):
            self.table.insertRow(row)
            tags_list = getattr(subset, "tags", None) or subset.get("tags", [])
            limit_val = getattr(subset, "limit", None) or subset.get("limit", 1)

            self.table.setItem(row, 0, QTableWidgetItem(", ".join(tags_list)))

            spin = QSpinBox(self)
            spin.setRange(1, 999)
            spin.setValue(int(limit_val))
            spin.valueChanged.connect(lambda _: self.changed.emit())
            self.table.setCellWidget(row, 1, spin)

        self.table.blockSignals(False)

    def _on_add_row(self) -> None:
        row = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.insertRow(row)

        self.table.setItem(row, 0, QTableWidgetItem("safe, questionable, explicit"))

        spin = QSpinBox(self)
        spin.setRange(1, 999)
        spin.setValue(1)
        spin.valueChanged.connect(lambda _: self.changed.emit())
        self.table.setCellWidget(row, 1, spin)

        self.table.blockSignals(False)

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
        self.table.blockSignals(False)
        self.changed.emit()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        del item
        self.changed.emit()


# endregion


# region Category Limit Editor


class CategoryLimitEditor(QWidget):
    """Table editor for max_tags_per_category: [Category (Combo/Text), Limit (SpinBox)]."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._suggestions: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Category", "Max Tags"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
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

    def set_suggestions(self, suggestions: list[str]) -> None:
        self._suggestions = list(suggestions)

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
        self.table.blockSignals(False)

    def _insert_row(self, row: int, category: str = "", limit: int = 10) -> None:
        self.table.insertRow(row)
        combo = QComboBox(self)
        combo.setEditable(True)
        if self._suggestions:
            combo.addItems(self._suggestions)
        combo.setCurrentText(category)
        combo.currentTextChanged.connect(lambda _: self.changed.emit())
        self.table.setCellWidget(row, 0, combo)

        spin = QSpinBox(self)
        spin.setRange(1, 9999)
        spin.setValue(limit)
        spin.valueChanged.connect(lambda _: self.changed.emit())
        self.table.setCellWidget(row, 1, spin)

    def _on_add_row(self) -> None:
        self.table.blockSignals(True)
        row = self.table.rowCount()
        existing = set(self.get_limits().keys())
        cat = next((c for c in self._suggestions if c not in existing), "")
        self._insert_row(row, cat, 10)
        self.table.blockSignals(False)
        self.changed.emit()

    def _on_remove_row(self) -> None:
        selected_rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        if not selected_rows:
            return
        self.table.blockSignals(True)
        for row in selected_rows:
            self.table.removeRow(row)
        self.table.blockSignals(False)
        self.changed.emit()


# endregion


# region Category Tag Editor


class CategoryTagEditor(QWidget):
    """
    Dynamic category list editor.
    Starts empty by default; populates suggestions dynamically from vibe model metadata.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._suggestions: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 1. List of active categories (starts completely empty)
        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.setMinimumHeight(95)
        layout.addWidget(self.list_widget)

        # 2. Add bar with editable combobox
        input_layout = QHBoxLayout()
        input_layout.setSpacing(6)

        self.combo = QComboBox(self)
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
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

    def _refresh_combo(self) -> None:
        self.combo.blockSignals(True)
        current = self.combo.currentText()
        self.combo.clear()
        self.combo.addItems(self._suggestions)
        self.combo.setCurrentText(current)
        self.combo.blockSignals(False)

    def set_suggestions(self, suggestions: Sequence[str]) -> None:
        """Update suggestion list strictly from dynamic models / vibe catalog."""
        self._suggestions = sorted(dict.fromkeys(suggestions))
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
        self.blockSignals(False)

    def _on_add(self) -> None:
        text = self.combo.currentText().strip().lower()
        if not text:
            return

        existing = set(self.get_items())
        if text not in existing:
            self.list_widget.addItem(QListWidgetItem(text))
            self.combo.setCurrentText("")
            self.changed.emit()

    def _on_remove(self) -> None:
        selected = self.list_widget.selectedItems()
        if not selected:
            return

        for item in selected:
            row = self.list_widget.row(item)
            self.list_widget.takeItem(row)

        self.changed.emit()


# endregion


# region Tag Query Stacked Card Editor


class TagQueryCard(QFrame):
    """
    A single query card mapping to one [[hydrus.tag_queries]] entry.
    Contains:
      - Target Service dropdown (resolving friendly names, with 'All Known Tags' default).
      - Tag list editor (StringListEditor) that cleanly supports commas, colons, and parentheses.
    """

    changed = Signal()
    delete_requested = Signal()

    def __init__(
        self,
        index: int = 1,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._available_services: dict[str, str] = {}
        self._index = index

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "TagQueryCard {"
            "  border: 1px solid rgba(255, 255, 255, 0.08);"
            "  border-radius: 6px;"
            "  background: rgba(255, 255, 255, 0.015);"
            "}"
        )

        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(10, 8, 10, 10)
        card_layout.setSpacing(8)

        # 1. Header row
        header_layout = QHBoxLayout()
        self.title_label = QLabel(f"<b>Query #{self._index}</b>", self)
        self.title_label.setStyleSheet("font-size: 12px;")
        header_layout.addWidget(self.title_label)

        header_layout.addStretch(1)

        self.del_btn = QPushButton("✕", self)
        self.del_btn.setFixedWidth(26)
        self.del_btn.setToolTip("Remove this query block")
        self.del_btn.setStyleSheet(
            "QPushButton { color: #888; font-weight: bold; border: 1px solid #444; border-radius: 3px; }"
            "QPushButton:hover { color: #d32f2f; border-color: #d32f2f; background: rgba(211, 47, 47, 0.1); }"
        )
        self.del_btn.clicked.connect(self.delete_requested.emit)
        header_layout.addWidget(self.del_btn)

        card_layout.addLayout(header_layout)

        # 2. Service selection row
        svc_row = QHBoxLayout()
        svc_row.setSpacing(8)
        svc_label = QLabel("Target Service:", self)
        svc_label.setStyleSheet("color: #b0bec5;")
        svc_row.addWidget(svc_label)

        self.service_combo = QComboBox(self)
        self.service_combo.setEditable(True)
        self._repopulate_services()
        self.service_combo.currentIndexChanged.connect(lambda _: self.changed.emit())
        svc_row.addWidget(self.service_combo, stretch=1)

        card_layout.addLayout(svc_row)

        # 3. Tags list editor
        tags_label = QLabel("Search Tags:", self)
        tags_label.setStyleSheet("color: #b0bec5;")
        card_layout.addWidget(tags_label)

        self.tags_editor = StringListEditor(placeholder="Enter search tag and press Enter or Add...", parent=self)
        self.tags_editor.changed.connect(self.changed.emit)
        card_layout.addWidget(self.tags_editor)

    def set_index(self, index: int) -> None:
        self._index = index
        self.title_label.setText(f"<b>Query #{self._index}</b>")

    def set_available_services(self, services: dict[str, str]) -> None:
        self._available_services = dict(services)
        current_key = self.service_combo.currentData()
        self._repopulate_services(selected_key=current_key)

    def _repopulate_services(self, selected_key: str | None = None) -> None:
        self.service_combo.blockSignals(True)
        self.service_combo.clear()

        # Virtual 'all known tags' default option (empty key)
        self.service_combo.addItem("All Known Tags (Search All)", userData="")

        for key, name in self._available_services.items():
            short_key = f"{key[:8]}..." if len(key) > 12 else key
            display = f"{name}  ({short_key})"
            self.service_combo.addItem(display, userData=key)

        if selected_key:
            idx = self.service_combo.findData(selected_key)
            if idx >= 0:
                self.service_combo.setCurrentIndex(idx)
            else:
                short_key = f"{selected_key[:8]}..." if len(selected_key) > 12 else selected_key
                display = f"Unknown Service  ({short_key})"
                self.service_combo.addItem(display, userData=selected_key)
                self.service_combo.setCurrentIndex(self.service_combo.count() - 1)
        else:
            self.service_combo.setCurrentIndex(0)

        self.service_combo.blockSignals(False)

    def get_query(self) -> dict[str, Any]:
        tags = self.tags_editor.get_items()
        selected_key = str(self.service_combo.currentData() or "").strip()
        service_keys = [selected_key] if selected_key else []
        return {"tags": tags, "tag_service_keys": service_keys}

    def set_query(self, tags: Sequence[Any], service_keys: Sequence[str]) -> None:
        self.tags_editor.set_items([str(t) for t in tags])
        key = service_keys[0] if service_keys else ""
        self._repopulate_services(selected_key=key)


class TagQueryListEditor(QWidget):
    """
    Stacked list editor for multiple [[hydrus.tag_queries]].
    Each entry is represented as an independent TagQueryCard.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards: list[TagQueryCard] = []
        self._available_services: dict[str, str] = {}

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(8)

        # 1. Container for cards
        self._cards_widget = QWidget(self)
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        self._root_layout.addWidget(self._cards_widget)

        # 2. Empty placeholder
        self.empty_label = QLabel("(No tag queries configured — click '+ Add Tag Query' below)", self)
        self.empty_label.setStyleSheet("color: #888; font-style: italic; padding: 4px;")
        self._root_layout.addWidget(self.empty_label)

        # 3. Add button
        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("+ Add Tag Query", self)
        self.add_btn.clicked.connect(self._on_add_clicked)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addStretch()
        self._root_layout.addLayout(btn_layout)

        self._update_empty_state()

    def set_available_services(self, services: dict[str, str]) -> None:
        self._available_services = dict(services)
        for card in self._cards:
            card.set_available_services(services)

    def get_queries(self) -> list[dict[str, Any]]:
        queries: list[dict[str, Any]] = []
        for card in self._cards:
            q = card.get_query()
            if q["tags"]:  # Only persist queries with at least one tag
                queries.append(q)
        return queries

    def set_queries(self, queries: Sequence[Any]) -> None:
        self.blockSignals(True)
        self._clear_cards()
        for idx, q in enumerate(queries, start=1):
            tags = getattr(q, "tags", None) or (q.get("tags") if isinstance(q, dict) else [])
            keys = getattr(q, "tag_service_keys", None) or (q.get("tag_service_keys") if isinstance(q, dict) else [])
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
    ) -> TagQueryCard:
        idx = index or (len(self._cards) + 1)
        card = TagQueryCard(index=idx, parent=self._cards_widget)
        card.set_available_services(self._available_services)
        if tags is not None:
            card.set_query(tags, keys or [])
        else:
            card.set_query(["system:untagged"], [])

        card.changed.connect(self.changed.emit)
        card.delete_requested.connect(lambda: self._on_delete_card(card))

        self._cards_layout.addWidget(card)
        self._cards.append(card)
        self._update_empty_state()
        return card

    def _on_add_clicked(self) -> None:
        self._add_card()
        self.changed.emit()

    def _on_delete_card(self, card: TagQueryCard) -> None:
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


class SmoothScrollArea(QScrollArea):
    """Experimental smooth scrolling area using QPropertyAnimation and Event Filters."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Bind the animation to the vertical scrollbar's 'value' property
        self._anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setDuration(300)  # 300ms animation duration feels snappy but smooth
        self._target_value = 0.0

        # 1. Install filter on the scrollbar itself to fix instant-jumps on hover
        self.verticalScrollBar().installEventFilter(self)

        # 2. Install global application filter to monitor child widgets (tables, lists)
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel:
            # If the user is scrolling while hovering directly over the scrollbar
            if obj == self.verticalScrollBar():
                self.wheelEvent(event)  # Reroute to smooth scroll
                return True

            # If the animation is running, prevent child tables/lists from stealing the scroll
            if (
                self._anim.state() == QPropertyAnimation.State.Running
                and isinstance(obj, QWidget)
                and self.isAncestorOf(obj)
            ):
                self.wheelEvent(event)  # Steal the event to continue the smooth glide
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
        step = vbar.singleStep() * 3.5 * (delta / 120.0)

        self._target_value -= step
        self._target_value = max(vbar.minimum(), min(self._target_value, vbar.maximum()))

        self._anim.stop()
        self._anim.setStartValue(vbar.value())
        self._anim.setEndValue(self._target_value)
        self._anim.start()

        event.accept()
