"""
filters_page.py — Output filtering, thresholding, namespace prefixes, and replacements.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from hyvis.config import AppConfig, OutputFilterConfig
from hyvis.gui.widgets import (
    KeyValueEditor,
    StringListEditor,
    SubsetListEditor,
    ThresholdTableEditor,
    setup_field_tooltip,
)

# Standard canonical categories
# todo: use vibe's TagCategory enum?
ALL_CATEGORIES = [
    "general",
    "character",
    "rating",
    "artist",
    "copyright",
    "meta",
    "species",
    "lore",
    "contributor",
]


class CategoryLimitEditor(QWidget):
    """Table editor for max_tags_per_category: [Category (Combo/Text), Limit (SpinBox)]."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
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

    def _insert_row(self, row: int, category: str = "general", limit: int = 10) -> None:
        self.table.insertRow(row)
        combo = QComboBox(self)
        combo.setEditable(True)
        combo.addItems(ALL_CATEGORIES)
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
        cat = next((c for c in ALL_CATEGORIES if c not in existing), "general")
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


class FiltersPage(QWidget):
    """Configuration page for global [output_filter] settings."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cat_checkboxes: dict[str, QCheckBox] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        of_fields = OutputFilterConfig.model_fields

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(16)

        # 1. Threshold Settings
        thresh_group = QGroupBox("Threshold Settings", container)
        thresh_layout = QFormLayout(thresh_group)
        thresh_layout.setSpacing(8)

        self.default_thresh_spin = QDoubleSpinBox(self)
        self.default_thresh_spin.setRange(0.0, 1.0)
        self.default_thresh_spin.setSingleStep(0.05)
        self.default_thresh_spin.setDecimals(2)
        self.default_thresh_spin.setValue(0.40)
        setup_field_tooltip(self.default_thresh_spin, of_fields["default_threshold"])
        self.default_thresh_spin.valueChanged.connect(lambda _: self.changed.emit())
        thresh_layout.addRow(f"{of_fields['default_threshold'].title}:", self.default_thresh_spin)

        self.prefer_tlt_chk = QCheckBox(of_fields["prefer_tag_level_thresholds"].title, self)
        setup_field_tooltip(self.prefer_tlt_chk, of_fields["prefer_tag_level_thresholds"])
        self.prefer_tlt_chk.setChecked(True)
        self.prefer_tlt_chk.toggled.connect(lambda _: self.changed.emit())
        thresh_layout.addRow("", self.prefer_tlt_chk)

        self.tlt_offset_spin = QDoubleSpinBox(self)
        self.tlt_offset_spin.setRange(-1.0, 1.0)
        self.tlt_offset_spin.setSingleStep(0.05)
        self.tlt_offset_spin.setDecimals(2)
        self.tlt_offset_spin.setValue(0.00)
        setup_field_tooltip(self.tlt_offset_spin, of_fields["tag_level_threshold_relative_offset"])
        self.tlt_offset_spin.valueChanged.connect(lambda _: self.changed.emit())
        thresh_layout.addRow(f"{of_fields['tag_level_threshold_relative_offset'].title}:", self.tlt_offset_spin)

        layout.addWidget(thresh_group)

        # 2. Categories to Emit
        cat_group = QGroupBox(of_fields["output_categories"].title, container)
        setup_field_tooltip(cat_group, of_fields["output_categories"])
        cat_grid = QGridLayout(cat_group)
        cat_grid.setSpacing(8)

        for i, cat in enumerate(ALL_CATEGORIES):
            chk = QCheckBox(cat, self)
            chk.toggled.connect(lambda _: self.changed.emit())
            self._cat_checkboxes[cat] = chk
            cat_grid.addWidget(chk, i // 3, i % 3)

        layout.addWidget(cat_group)

        # 3. Inclusions & Exclusions
        inc_exc_group = QGroupBox("Tag Inclusions & Exclusions", container)
        inc_exc_layout = QHBoxLayout(inc_exc_group)
        inc_exc_layout.setSpacing(12)

        # Inclusions sub-group
        inc_sub = QGroupBox(of_fields["include_tags"].title, inc_exc_group)
        setup_field_tooltip(inc_sub, of_fields["include_tags"])
        inc_box = QVBoxLayout(inc_sub)
        self.include_editor = StringListEditor(placeholder="Add always-included tag...", parent=self)
        self.include_editor.changed.connect(self.changed.emit)
        inc_box.addWidget(self.include_editor)
        inc_exc_layout.addWidget(inc_sub)

        # Exclusions sub-group
        exc_sub = QGroupBox(of_fields["exclude_tags"].title, inc_exc_group)
        setup_field_tooltip(exc_sub, of_fields["exclude_tags"])
        exc_box = QVBoxLayout(exc_sub)
        self.exclude_editor = StringListEditor(placeholder="Add always-excluded tag...", parent=self)
        self.exclude_editor.changed.connect(self.changed.emit)
        exc_box.addWidget(self.exclude_editor)
        inc_exc_layout.addWidget(exc_sub)

        layout.addWidget(inc_exc_group)

        # 4. Custom Threshold Overrides
        overrides_group = QGroupBox("Threshold Overrides", container)
        overrides_layout = QVBoxLayout(overrides_group)
        overrides_layout.setSpacing(12)

        cat_thresh_sub = QGroupBox(of_fields["category_thresholds"].title, self)
        setup_field_tooltip(cat_thresh_sub, of_fields["category_thresholds"])
        cat_box_layout = QVBoxLayout(cat_thresh_sub)
        self.cat_thresh_editor = ThresholdTableEditor(target_header="Category Name", parent=self)
        self.cat_thresh_editor.changed.connect(self.changed.emit)
        cat_box_layout.addWidget(self.cat_thresh_editor)
        overrides_layout.addWidget(cat_thresh_sub)

        tag_thresh_sub = QGroupBox(of_fields["tag_thresholds"].title, self)
        setup_field_tooltip(tag_thresh_sub, of_fields["tag_thresholds"])
        tag_box_layout = QVBoxLayout(tag_thresh_sub)
        self.tag_thresh_editor = ThresholdTableEditor(target_header="Raw Tag Name", parent=self)
        self.tag_thresh_editor.changed.connect(self.changed.emit)
        tag_box_layout.addWidget(self.tag_thresh_editor)
        overrides_layout.addWidget(tag_thresh_sub)

        layout.addWidget(overrides_group)

        # 5. Namespace Prefixes & Tag Replacements
        pfx_group = QGroupBox("Tag Formatting & Namespace Prefixes", container)
        pfx_layout = QVBoxLayout(pfx_group)
        pfx_layout.setSpacing(12)

        # Category Prefixes
        cat_pfx_sub = QGroupBox(of_fields["category_tag_prefix_mapping"].title, self)
        setup_field_tooltip(cat_pfx_sub, of_fields["category_tag_prefix_mapping"])
        cat_pfx_layout = QVBoxLayout(cat_pfx_sub)
        self.cat_prefix_editor = KeyValueEditor(key_header="Category", val_header="Prefix", parent=self)
        self.cat_prefix_editor.changed.connect(self.changed.emit)
        cat_pfx_layout.addWidget(self.cat_prefix_editor)
        pfx_layout.addWidget(cat_pfx_sub)

        # Tag Prefix Overrides
        tag_pfx_sub = QGroupBox(of_fields["tag_prefix_overrides"].title, self)
        setup_field_tooltip(tag_pfx_sub, of_fields["tag_prefix_overrides"])
        tag_pfx_layout = QVBoxLayout(tag_pfx_sub)
        self.tag_prefix_editor = KeyValueEditor(key_header="Raw Tag", val_header="Prefix", parent=self)
        self.tag_prefix_editor.changed.connect(self.changed.emit)
        tag_pfx_layout.addWidget(self.tag_prefix_editor)
        pfx_layout.addWidget(tag_pfx_sub)

        # Tag Replacements
        rep_sub = QGroupBox(of_fields["tag_replacements"].title, self)
        setup_field_tooltip(rep_sub, of_fields["tag_replacements"])
        rep_layout = QVBoxLayout(rep_sub)
        self.tag_replacements_editor = KeyValueEditor(key_header="Original Tag", val_header="Replacement", parent=self)
        self.tag_replacements_editor.changed.connect(self.changed.emit)
        rep_layout.addWidget(self.tag_replacements_editor)
        pfx_layout.addWidget(rep_sub)

        layout.addWidget(pfx_group)

        # 6. Tag Limits (Per-Category & Joint Subsets)
        limits_group = QGroupBox("Tag Output Limits", container)
        limits_layout = QVBoxLayout(limits_group)
        limits_layout.setSpacing(12)

        # Max tags per category
        cat_limit_sub = QGroupBox(of_fields["max_tags_per_category"].title, self)
        setup_field_tooltip(cat_limit_sub, of_fields["max_tags_per_category"])
        cat_limit_layout = QVBoxLayout(cat_limit_sub)
        self.cat_limit_editor = CategoryLimitEditor(self)
        self.cat_limit_editor.changed.connect(self.changed.emit)
        cat_limit_layout.addWidget(self.cat_limit_editor)
        limits_layout.addWidget(cat_limit_sub)

        # Joint Subsets
        subset_sub = QGroupBox(of_fields["max_tags_per_subset"].title, self)
        setup_field_tooltip(subset_sub, of_fields["max_tags_per_subset"])
        subset_layout = QVBoxLayout(subset_sub)
        self.subset_editor = SubsetListEditor(self)
        self.subset_editor.changed.connect(self.changed.emit)
        subset_layout.addWidget(self.subset_editor)
        limits_layout.addWidget(subset_sub)

        layout.addWidget(limits_group)

        # Mount scroll
        scroll.setWidget(container)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def load_config(self, cfg: AppConfig) -> None:
        self.blockSignals(True)
        of = cfg.output_filter

        self.default_thresh_spin.setValue(of.default_threshold)
        self.prefer_tlt_chk.setChecked(of.prefer_tag_level_thresholds)
        self.tlt_offset_spin.setValue(of.tag_level_threshold_relative_offset)

        # Categories
        allowed_cats = set(of.output_categories)
        for cat, chk in self._cat_checkboxes.items():
            chk.blockSignals(True)
            chk.setChecked(cat in allowed_cats)
            chk.blockSignals(False)

        # Inclusions & Exclusions
        self.include_editor.set_items(of.include_tags)
        self.exclude_editor.set_items(of.exclude_tags)

        # Tables
        self.cat_thresh_editor.set_thresholds(of.category_thresholds)
        self.tag_thresh_editor.set_thresholds(of.tag_thresholds)
        self.cat_prefix_editor.set_mapping(of.category_tag_prefix_mapping)
        self.tag_prefix_editor.set_mapping(of.tag_prefix_overrides)
        self.tag_replacements_editor.set_mapping(of.tag_replacements)
        self.cat_limit_editor.set_limits(of.max_tags_per_category)
        self.subset_editor.set_subsets(of.max_tags_per_subset)

        self.blockSignals(False)

    def apply_to_dict(self, data: dict[str, Any]) -> None:
        of_dict: dict[str, Any] = data.setdefault("output_filter", {})

        of_dict["default_threshold"] = float(self.default_thresh_spin.value())
        of_dict["prefer_tag_level_thresholds"] = self.prefer_tlt_chk.isChecked()
        of_dict["tag_level_threshold_relative_offset"] = float(self.tlt_offset_spin.value())

        # Categories
        selected_cats = [cat for cat, chk in self._cat_checkboxes.items() if chk.isChecked()]
        of_dict["output_categories"] = selected_cats

        # Inclusions & Exclusions
        of_dict["include_tags"] = self.include_editor.get_items()
        of_dict["exclude_tags"] = self.exclude_editor.get_items()

        # Tables
        of_dict["category_thresholds"] = self.cat_thresh_editor.get_thresholds()
        of_dict["tag_thresholds"] = self.tag_thresh_editor.get_thresholds()
        of_dict["category_tag_prefix_mapping"] = self.cat_prefix_editor.get_mapping()
        of_dict["tag_prefix_overrides"] = self.tag_prefix_editor.get_mapping()
        of_dict["tag_replacements"] = self.tag_replacements_editor.get_mapping()
        of_dict["max_tags_per_category"] = self.cat_limit_editor.get_limits()
        of_dict["max_tags_per_subset"] = self.subset_editor.get_subsets()
