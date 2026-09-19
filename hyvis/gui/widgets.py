"""
widgets.py — Reusable specialized input widgets for HyVis configuration.

Includes:
  - StringListEditor: Reusable list for service keys, queries, and include/exclude tags.
  - KeyValueEditor: Reusable table for prefix mappings and tag replacements.
  - ThresholdTableEditor: Table for category and tag thresholds with TLT override checkboxes.
  - SubsetListEditor: Table for managing max_tags_per_subset rule groups.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# region String List Editor


class StringListEditor(QWidget):
    """
    A widget for viewing, adding, and removing a list of strings.
    Used for include_tags, exclude_tags, output_categories, and service keys.
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
        """Return all items currently in the list."""
        items: list[str] = []
        for i in range(self.list_widget.count()):
            text = self.list_widget.item(i).text().strip()
            if text:
                items.append(text)
        return items

    def set_items(self, items: Sequence[str]) -> None:
        """Populate the list widget without emitting dirty signals."""
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

        # Avoid duplicate entries in simple lists
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
        """Return current valid key-value pairs."""
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
        """Populate table from mapping without firing changed signals."""
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

        # Focus new key cell
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
    Columns: [Target (Tag or Category), Threshold (0.00-1.00), Override TLT (Checkbox)].
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
        """Return dict formatted as {target: {'threshold': float, 'override_tlt': bool}}."""
        results: dict[str, dict[str, Any]] = {}

        for row in range(self.table.rowCount()):
            target_item = self.table.item(row, 0)
            target = target_item.text().strip() if target_item else ""
            if not target:
                continue

            spin: QDoubleSpinBox | None = self.table.cellWidget(row, 1)
            thresh_val = spin.value() if spin else 0.40

            chk: QCheckBox | None = self.table.cellWidget(row, 2)
            override_val = chk.isChecked() if chk else False

            results[target] = {
                "threshold": float(thresh_val),
                "override_tlt": bool(override_val),
            }

        return results

    def set_thresholds(self, data: Mapping[str, Any]) -> None:
        """Populate table from config dictionary."""
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        for row, (target, conf) in enumerate(data.items()):
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(target)))

            # Threshold spinbox
            val = conf.threshold if hasattr(conf, "threshold") else conf.get("threshold", 0.40)
            spin = QDoubleSpinBox(self)
            spin.setRange(0.0, 1.0)
            spin.setSingleStep(0.05)
            spin.setDecimals(2)
            spin.setValue(float(val))
            spin.valueChanged.connect(lambda _: self.changed.emit())
            self.table.setCellWidget(row, 1, spin)

            # Override TLT checkbox
            ovr = conf.override_tlt if hasattr(conf, "override_tlt") else conf.get("override_tlt", False)
            chk = QCheckBox(self)
            chk.setChecked(bool(ovr))
            chk.toggled.connect(lambda _: self.changed.emit())

            # Center checkbox in cell
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

        # 1. Table
        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Tags (comma-separated)", "Limit"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table)

        # 2. Action Buttons
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
        """Return list of dicts: [{'tags': ['cat', 'dog'], 'limit': 1}, ...]"""
        subsets: list[dict[str, Any]] = []

        for row in range(self.table.rowCount()):
            tag_item = self.table.item(row, 0)
            raw_text = tag_item.text().strip() if tag_item else ""
            if not raw_text:
                continue

            # Split on comma
            tags = [t.strip() for t in raw_text.split(",") if t.strip()]
            if not tags:
                continue

            spin: QSpinBox | None = self.table.cellWidget(row, 1)
            limit = spin.value() if spin else 1

            subsets.append({"tags": tags, "limit": limit})

        return subsets

    def set_subsets(self, subsets: Sequence[Any]) -> None:
        """Populate table from TagSubsetConfig sequence."""
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
