from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hyvis.gui.base_widgets import AutoResizingTableWidget, FloatSpinBox, SuggestionComboBox

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

        self.table = AutoResizingTableWidget(0, 2, parent=self)
        self.table.setHorizontalHeaderLabels([self._key_header, self._val_header])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 160)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._on_item_changed)
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

    def _get_used_keys(self, exclude_row: int = -1) -> set[str]:
        used = set()
        for r in range(self.table.rowCount()):
            if r == exclude_row:
                continue
            if self._use_combobox_for_key:
                combo = self.table.cellWidget(r, 0)
                if isinstance(combo, QComboBox) and combo.currentText().strip():
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
            combo = self.table.cellWidget(r, 0)
            if isinstance(combo, QComboBox):
                self._populate_combo_items(combo, combo.currentText(), r)

    def set_key_suggestions(self, suggestions: Sequence[str] | Mapping[str, Sequence[str]]) -> None:
        if isinstance(suggestions, Mapping):
            s_map = cast(Mapping[str, Sequence[str]], suggestions)
            self._key_suggestions = sorted(s_map.keys())
            self._provenance = {k: list(v) for k, v in s_map.items()}
        else:
            self._key_suggestions = sorted(dict.fromkeys(suggestions))
            self._provenance = {}
        self._refresh_all_combos()

    def get_mapping(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for row in range(self.table.rowCount()):
            if self._use_combobox_for_key:
                combo = self.table.cellWidget(row, 0)
                key = combo.currentText().strip() if isinstance(combo, QComboBox) else ""
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
        self.table.blockSignals(False)
        self._refresh_all_combos()

        if not self._use_combobox_for_key:
            item = self.table.item(row, 0)
            if item is not None:
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

        self.table = AutoResizingTableWidget(0, 3, parent=self)
        self.table.setHorizontalHeaderLabels([self._target_header, "Threshold", "Override TLT"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 95)
        self.table.setColumnWidth(2, 110)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._on_table_item_changed)
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

    def _get_used_targets(self, exclude_row: int = -1) -> set[str]:
        used = set()
        for r in range(self.table.rowCount()):
            if r == exclude_row:
                continue
            if self._use_combobox_for_target:
                combo = self.table.cellWidget(r, 0)
                if isinstance(combo, QComboBox) and combo.currentText().strip():
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
            combo = self.table.cellWidget(r, 0)
            if isinstance(combo, QComboBox):
                self._populate_combo_items(combo, combo.currentText(), r)

    def set_target_suggestions(self, suggestions: Sequence[str] | Mapping[str, Sequence[str]]) -> None:
        if isinstance(suggestions, Mapping):
            s_map = cast(Mapping[str, Sequence[str]], suggestions)
            self._target_suggestions = sorted(s_map.keys())
            self._provenance = {k: list(v) for k, v in s_map.items()}
        else:
            self._target_suggestions = sorted(dict.fromkeys(suggestions))
            self._provenance = {}
        self._refresh_all_combos()

    def get_thresholds(self) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for row in range(self.table.rowCount()):
            if self._use_combobox_for_target:
                combo = self.table.cellWidget(row, 0)
                target = combo.currentText().strip() if isinstance(combo, QComboBox) else ""
            else:
                target_item = self.table.item(row, 0)
                target = target_item.text().strip() if target_item else ""

            if not target:
                continue

            spin = self.table.cellWidget(row, 1)
            thresh_val = spin.value() if isinstance(spin, QDoubleSpinBox) else 0.40

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
            spin = FloatSpinBox(self)
            spin.setRange(0.0, 1.0)
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

        spin = FloatSpinBox(self)
        spin.setRange(0.0, 1.0)
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
        self._refresh_all_combos()

        if not self._use_combobox_for_target:
            item = self.table.item(row, 0)
            if item is not None:
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

        self.table = AutoResizingTableWidget(0, 2, parent=self)
        self.table.setHorizontalHeaderLabels(["Category", "Max Tags"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 100)
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

    def _get_used_categories(self, exclude_row: int = -1) -> set[str]:
        used = set()
        for r in range(self.table.rowCount()):
            if r == exclude_row:
                continue
            combo = self.table.cellWidget(r, 0)
            if isinstance(combo, QComboBox) and combo.currentText().strip():
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
            combo = self.table.cellWidget(r, 0)
            if isinstance(combo, QComboBox):
                self._populate_combo_items(combo, combo.currentText(), r)

    def set_suggestions(self, suggestions: Sequence[str] | Mapping[str, Sequence[str]]) -> None:
        if isinstance(suggestions, Mapping):
            s_map = cast(Mapping[str, Sequence[str]], suggestions)
            self._suggestions = sorted(s_map.keys())
            self._provenance = {k: list(v) for k, v in s_map.items()}
        else:
            self._suggestions = sorted(dict.fromkeys(suggestions))
            self._provenance = {}
        self._refresh_all_combos()

    def get_limits(self) -> dict[str, int]:
        limits: dict[str, int] = {}
        for row in range(self.table.rowCount()):
            combo = self.table.cellWidget(row, 0)
            cat = combo.currentText().strip() if isinstance(combo, QComboBox) else ""
            spin = self.table.cellWidget(row, 1)
            val = spin.value() if isinstance(spin, QSpinBox) else 1
            if cat:
                limits[cat] = val
        return limits

    def set_limits(self, limits: Mapping[str, int]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for row, (cat, val) in enumerate(limits.items()):
            self._insert_row(row, cat, int(val))
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
        spin.setRange(1, 2_147_483_647)
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
        self.table.blockSignals(False)
        self._refresh_all_combos()
        self.changed.emit()


# endregion
