"""
filters_page.py — Output filtering, thresholding, namespace prefixes, and replacements.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from hyvis.config import AppConfig
from hyvis.gui.widgets import (
    KeyValueEditor,
    StringListEditor,
    SubsetListEditor,
    ThresholdTableEditor,
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


class FiltersPage(QWidget):
    """Configuration page for global [output_filter] settings."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cat_checkboxes: dict[str, QCheckBox] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
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
        self.default_thresh_spin.valueChanged.connect(lambda _: self.changed.emit())
        thresh_layout.addRow("Default Threshold (Fallback):", self.default_thresh_spin)

        self.prefer_tlt_chk = QCheckBox("Prefer Model Tag-Level Thresholds (TLT)", self)
        self.prefer_tlt_chk.setChecked(True)
        self.prefer_tlt_chk.toggled.connect(lambda _: self.changed.emit())
        thresh_layout.addRow("", self.prefer_tlt_chk)

        self.tlt_offset_spin = QDoubleSpinBox(self)
        self.tlt_offset_spin.setRange(-1.0, 1.0)
        self.tlt_offset_spin.setSingleStep(0.05)
        self.tlt_offset_spin.setDecimals(2)
        self.tlt_offset_spin.setValue(0.00)
        self.tlt_offset_spin.valueChanged.connect(lambda _: self.changed.emit())
        thresh_layout.addRow("TLT Relative Offset (-1.0 to 1.0):", self.tlt_offset_spin)

        layout.addWidget(thresh_group)

        # 2. Categories to Emit
        cat_group = QGroupBox("Categories to Output (Empty = All Allowed via Inclusions)", container)
        cat_grid = QGridLayout(cat_group)
        cat_grid.setSpacing(8)

        for i, cat in enumerate(ALL_CATEGORIES):
            chk = QCheckBox(cat, self)
            chk.toggled.connect(lambda _: self.changed.emit())
            self._cat_checkboxes[cat] = chk
            cat_grid.addWidget(chk, i // 3, i % 3)

        layout.addWidget(cat_group)

        # 3. Inclusions & Exclusions
        inc_exc_group = QGroupBox("Tag Inclusion & Exclusion Lists", container)
        inc_exc_layout = QHBoxLayout(inc_exc_group)
        inc_exc_layout.setSpacing(12)

        inc_box = QVBoxLayout()
        inc_box.addWidget(StringListEditor(placeholder="Add always-included tag...", parent=self))
        self.include_editor = inc_box.itemAt(0).widget()
        self.include_editor.changed.connect(self.changed.emit)
        inc_exc_layout.addLayout(inc_box)

        exc_box = QVBoxLayout()
        exc_box.addWidget(StringListEditor(placeholder="Add always-excluded tag...", parent=self))
        self.exclude_editor = exc_box.itemAt(0).widget()
        self.exclude_editor.changed.connect(self.changed.emit)
        inc_exc_layout.addLayout(exc_box)

        layout.addWidget(inc_exc_group)

        # 4. Custom Threshold Overrides
        overrides_group = QGroupBox("Threshold Overrides (Category & Specific Tags)", container)
        overrides_layout = QVBoxLayout(overrides_group)
        overrides_layout.setSpacing(12)

        overrides_layout.addWidget(QGroupBox("Category Threshold Overrides", self))
        cat_box = overrides_layout.itemAt(0).widget()
        cat_box_layout = QVBoxLayout(cat_box)
        self.cat_thresh_editor = ThresholdTableEditor(target_header="Category Name", parent=self)
        self.cat_thresh_editor.changed.connect(self.changed.emit)
        cat_box_layout.addWidget(self.cat_thresh_editor)

        overrides_layout.addWidget(QGroupBox("Tag Threshold Overrides", self))
        tag_box = overrides_layout.itemAt(1).widget()
        tag_box_layout = QVBoxLayout(tag_box)
        self.tag_thresh_editor = ThresholdTableEditor(target_header="Raw Tag Name", parent=self)
        self.tag_thresh_editor.changed.connect(self.changed.emit)
        tag_box_layout.addWidget(self.tag_thresh_editor)

        layout.addWidget(overrides_group)

        # 5. Namespace Prefixes & Tag Replacements
        pfx_group = QGroupBox("Tag Formatting & Replacements", container)
        pfx_layout = QVBoxLayout(pfx_group)
        pfx_layout.setSpacing(12)

        # Category Prefixes
        pfx_layout.addWidget(QGroupBox("Category Tag Prefix Mapping (e.g. character -> character:)", self))
        cat_pfx_box = pfx_layout.itemAt(0).widget()
        cat_pfx_layout = QVBoxLayout(cat_pfx_box)
        self.cat_prefix_editor = KeyValueEditor(key_header="Category", val_header="Prefix", parent=self)
        self.cat_prefix_editor.changed.connect(self.changed.emit)
        cat_pfx_layout.addWidget(self.cat_prefix_editor)

        # Tag Replacements
        pfx_layout.addWidget(QGroupBox("Tag Replacements (e.g. rating:g -> general)", self))
        rep_box = pfx_layout.itemAt(1).widget()
        rep_layout = QVBoxLayout(rep_box)
        self.tag_replacements_editor = KeyValueEditor(key_header="Original Tag", val_header="Replacement", parent=self)
        self.tag_replacements_editor.changed.connect(self.changed.emit)
        rep_layout.addWidget(self.tag_replacements_editor)

        layout.addWidget(pfx_group)

        # 6. Joint Subsets
        subset_group = QGroupBox("Joint Subset Limits (Max Tags Per Group)", container)
        subset_layout = QVBoxLayout(subset_group)
        self.subset_editor = SubsetListEditor(self)
        self.subset_editor.changed.connect(self.changed.emit)
        subset_layout.addWidget(self.subset_editor)

        layout.addWidget(subset_group)

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
        self.tag_replacements_editor.set_mapping(of.tag_replacements)
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
        of_dict["tag_replacements"] = self.tag_replacements_editor.get_mapping()
        of_dict["max_tags_per_subset"] = self.subset_editor.get_subsets()
