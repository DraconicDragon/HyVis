"""
filters_page.py — Output filtering, thresholding, namespace prefixes, and per-model overrides.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from hyvis.config import AppConfig, OutputFilterConfig
from hyvis.gui.widgets import (
    DEFAULT_SUGGESTIONS,
    CategoryTagEditor,
    KeyValueEditor,
    StringListEditor,
    SubsetListEditor,
    ThresholdTableEditor,
)


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
        combo.addItems(DEFAULT_SUGGESTIONS)
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
        cat = next((c for c in DEFAULT_SUGGESTIONS if c not in existing), "general")
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
    """Configuration page for [output_filter] and per-model filter overrides."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._global_filter: dict[str, Any] = {}
        self._models_data: list[dict[str, Any]] = []
        self._current_scope: int = 0  # 0 = Global, 1..N = Model index + 1
        self._section_titles: dict[QGroupBox, str] = {}

        self._setup_ui()

    def _setup_ui(self) -> None:
        of_fields = OutputFilterConfig.model_fields

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # 1. Top Scope Selector Bar
        scope_layout = QHBoxLayout()
        scope_layout.setSpacing(8)

        scope_label = QLabel("<b>Configuration Target:</b>", self)
        scope_layout.addWidget(scope_label)

        self.scope_combo = QComboBox(self)
        self.scope_combo.addItem("🌐 Global Output Filter (Default)")
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        scope_layout.addWidget(self.scope_combo, stretch=1)

        root.addLayout(scope_layout)

        # 2. Model Override Notice Banner (Hidden in Global Scope)
        self.banner_frame = QFrame(self)
        self.banner_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self.banner_frame.setStyleSheet(
            "QFrame { background-color: rgba(33, 150, 243, 0.08); border: 1px solid rgba(33, 150, 243, 0.3); border-radius: 4px; padding: 6px; }"
        )
        banner_layout = QHBoxLayout(self.banner_frame)
        banner_layout.setContentsMargins(6, 4, 6, 4)

        self.banner_label = QLabel(self.banner_frame)
        self.banner_label.setStyleSheet("border: none; background: transparent;")
        banner_layout.addWidget(self.banner_label, stretch=1)

        self.reset_overrides_btn = QPushButton("↺ Reset All Overrides to Global", self.banner_frame)
        self.reset_overrides_btn.setToolTip("Discard all model-specific overrides and revert to Global Filter")
        self.reset_overrides_btn.clicked.connect(self._on_reset_overrides_clicked)
        banner_layout.addWidget(self.reset_overrides_btn)

        self.banner_frame.setVisible(False)
        root.addWidget(self.banner_frame)

        # 3. Main Scroll Area for Filter Sections
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # Section 1: Threshold Settings
        self.thresh_group = QGroupBox("Threshold Settings", container)
        self._section_titles[self.thresh_group] = "Threshold Settings"
        self.thresh_group.toggled.connect(lambda chk: self._on_section_toggled(self.thresh_group, chk))
        thresh_layout = QFormLayout(self.thresh_group)
        thresh_layout.setSpacing(8)

        self.default_thresh_spin = QDoubleSpinBox(self)
        self.default_thresh_spin.setRange(0.0, 1.0)
        self.default_thresh_spin.setSingleStep(0.05)
        self.default_thresh_spin.setDecimals(2)
        self.default_thresh_spin.setValue(0.40)
        self.default_thresh_spin.valueChanged.connect(lambda _: self._on_field_changed())
        thresh_layout.addRow(f"{of_fields['default_threshold'].title}:", self.default_thresh_spin)

        self.prefer_tlt_chk = QCheckBox(of_fields["prefer_tag_level_thresholds"].title, self)
        self.prefer_tlt_chk.setChecked(True)
        self.prefer_tlt_chk.toggled.connect(lambda _: self._on_field_changed())
        thresh_layout.addRow("", self.prefer_tlt_chk)

        self.tlt_offset_spin = QDoubleSpinBox(self)
        self.tlt_offset_spin.setRange(-1.0, 1.0)
        self.tlt_offset_spin.setSingleStep(0.05)
        self.tlt_offset_spin.setDecimals(2)
        self.tlt_offset_spin.setValue(0.00)
        self.tlt_offset_spin.valueChanged.connect(lambda _: self._on_field_changed())
        thresh_layout.addRow(f"{of_fields['tag_level_threshold_relative_offset'].title}:", self.tlt_offset_spin)

        layout.addWidget(self.thresh_group)

        # Section 2: Output Categories (Dynamic)
        self.cat_group = QGroupBox(of_fields["output_categories"].title, container)
        self._section_titles[self.cat_group] = of_fields["output_categories"].title or "Output Categories"
        self.cat_group.toggled.connect(lambda chk: self._on_section_toggled(self.cat_group, chk))
        cat_layout = QVBoxLayout(self.cat_group)
        self.cat_editor = CategoryTagEditor(self)
        self.cat_editor.changed.connect(self._on_field_changed)
        cat_layout.addWidget(self.cat_editor)

        layout.addWidget(self.cat_group)

        # Section 3: Inclusions & Exclusions
        self.inc_exc_group = QGroupBox("Tag Inclusions && Exclusions", container)
        self._section_titles[self.inc_exc_group] = "Tag Inclusions && Exclusions"
        self.inc_exc_group.toggled.connect(lambda chk: self._on_section_toggled(self.inc_exc_group, chk))
        inc_exc_layout = QHBoxLayout(self.inc_exc_group)
        inc_exc_layout.setSpacing(12)

        inc_sub = QGroupBox(of_fields["include_tags"].title, self.inc_exc_group)
        inc_box = QVBoxLayout(inc_sub)
        self.include_editor = StringListEditor(placeholder="Add always-included tag...", parent=self)
        self.include_editor.changed.connect(self._on_field_changed)
        inc_box.addWidget(self.include_editor)
        inc_exc_layout.addWidget(inc_sub)

        exc_sub = QGroupBox(of_fields["exclude_tags"].title, self.inc_exc_group)
        exc_box = QVBoxLayout(exc_sub)
        self.exclude_editor = StringListEditor(placeholder="Add always-excluded tag...", parent=self)
        self.exclude_editor.changed.connect(self._on_field_changed)
        exc_box.addWidget(self.exclude_editor)
        inc_exc_layout.addWidget(exc_sub)

        layout.addWidget(self.inc_exc_group)

        # Section 4: Custom Threshold Overrides
        self.overrides_group = QGroupBox("Threshold Overrides", container)
        self._section_titles[self.overrides_group] = "Threshold Overrides"
        self.overrides_group.toggled.connect(lambda chk: self._on_section_toggled(self.overrides_group, chk))
        overrides_layout = QVBoxLayout(self.overrides_group)
        overrides_layout.setSpacing(12)

        cat_thresh_sub = QGroupBox(of_fields["category_thresholds"].title, self)
        cat_box_layout = QVBoxLayout(cat_thresh_sub)
        self.cat_thresh_editor = ThresholdTableEditor(target_header="Category Name", parent=self)
        self.cat_thresh_editor.changed.connect(self._on_field_changed)
        cat_box_layout.addWidget(self.cat_thresh_editor)
        overrides_layout.addWidget(cat_thresh_sub)

        tag_thresh_sub = QGroupBox(of_fields["tag_thresholds"].title, self)
        tag_box_layout = QVBoxLayout(tag_thresh_sub)
        self.tag_thresh_editor = ThresholdTableEditor(target_header="Raw Tag Name", parent=self)
        self.tag_thresh_editor.changed.connect(self._on_field_changed)
        tag_box_layout.addWidget(self.tag_thresh_editor)
        overrides_layout.addWidget(tag_thresh_sub)

        layout.addWidget(self.overrides_group)

        # Section 5: Namespace Prefixes & Tag Replacements
        self.pfx_group = QGroupBox("Tag Formatting && Namespace Prefixes", container)
        self._section_titles[self.pfx_group] = "Tag Formatting && Namespace Prefixes"
        self.pfx_group.toggled.connect(lambda chk: self._on_section_toggled(self.pfx_group, chk))
        pfx_layout = QVBoxLayout(self.pfx_group)
        pfx_layout.setSpacing(12)

        cat_pfx_sub = QGroupBox(of_fields["category_tag_prefix_mapping"].title, self)
        cat_pfx_layout = QVBoxLayout(cat_pfx_sub)
        self.cat_prefix_editor = KeyValueEditor(key_header="Category", val_header="Prefix", parent=self)
        self.cat_prefix_editor.changed.connect(self._on_field_changed)
        cat_pfx_layout.addWidget(self.cat_prefix_editor)
        pfx_layout.addWidget(cat_pfx_sub)

        tag_pfx_sub = QGroupBox(of_fields["tag_prefix_overrides"].title, self)
        tag_pfx_layout = QVBoxLayout(tag_pfx_sub)
        self.tag_prefix_editor = KeyValueEditor(key_header="Raw Tag", val_header="Prefix", parent=self)
        self.tag_prefix_editor.changed.connect(self._on_field_changed)
        tag_pfx_layout.addWidget(self.tag_prefix_editor)
        pfx_layout.addWidget(tag_pfx_sub)

        rep_sub = QGroupBox(of_fields["tag_replacements"].title, self)
        rep_layout = QVBoxLayout(rep_sub)
        self.tag_replacements_editor = KeyValueEditor(key_header="Original Tag", val_header="Replacement", parent=self)
        self.tag_replacements_editor.changed.connect(self._on_field_changed)
        rep_layout.addWidget(self.tag_replacements_editor)
        pfx_layout.addWidget(rep_sub)

        layout.addWidget(self.pfx_group)

        # Section 6: Tag Output Limits
        self.limits_group = QGroupBox("Tag Output Limits", container)
        self._section_titles[self.limits_group] = "Tag Output Limits"
        self.limits_group.toggled.connect(lambda chk: self._on_section_toggled(self.limits_group, chk))
        limits_layout = QVBoxLayout(self.limits_group)
        limits_layout.setSpacing(12)

        cat_limit_sub = QGroupBox(of_fields["max_tags_per_category"].title, self)
        cat_limit_layout = QVBoxLayout(cat_limit_sub)
        self.cat_limit_editor = CategoryLimitEditor(self)
        self.cat_limit_editor.changed.connect(self._on_field_changed)
        cat_limit_layout.addWidget(self.cat_limit_editor)
        limits_layout.addWidget(cat_limit_sub)

        subset_sub = QGroupBox(of_fields["max_tags_per_subset"].title, self)
        subset_layout = QVBoxLayout(subset_sub)
        self.subset_editor = SubsetListEditor(self)
        self.subset_editor.changed.connect(self._on_field_changed)
        subset_layout.addWidget(self.subset_editor)
        limits_layout.addWidget(subset_sub)

        layout.addWidget(self.limits_group)

        scroll.setWidget(container)
        root.addWidget(scroll, stretch=1)

    # region Scope & Synchronization

    def sync_models(self, models_data: list[dict[str, Any]]) -> None:
        """Update active models list and refresh scope dropdown options."""
        self._models_data = list(models_data)
        self._refresh_scope_combo()
        self._update_category_suggestions()
        self._update_tooltips()

    def set_scope_by_model_index(self, model_index: int) -> None:
        """Programmatically switch scope to a specific model (e.g. from ModelsPage)."""
        target_idx = model_index + 1
        if 0 <= target_idx < self.scope_combo.count():
            self.scope_combo.setCurrentIndex(target_idx)

    def _refresh_scope_combo(self) -> None:
        self.scope_combo.blockSignals(True)
        prev_idx = self.scope_combo.currentIndex()
        self.scope_combo.clear()
        self.scope_combo.addItem("🌐 Global Output Filter (Default)")

        for i, m in enumerate(self._models_data):
            model_id = str(m.get("model_id") or f"Model {i + 1}")
            has_overrides = bool(m.get("output_filter"))
            status = "Custom Overrides" if has_overrides else "Inheriting Global"
            self.scope_combo.addItem(f"Model {i + 1}: {model_id} ({status})")

        new_idx = min(prev_idx, self.scope_combo.count() - 1) if prev_idx >= 0 else 0
        self.scope_combo.setCurrentIndex(new_idx)
        self.scope_combo.blockSignals(False)

    def _update_category_suggestions(self) -> None:
        suggestions = set(DEFAULT_SUGGESTIONS)
        try:
            import vibe

            for m in self._models_data:
                m_id = m.get("model_id")
                if m_id:
                    try:
                        desc = vibe.describe(m_id)
                        if hasattr(desc, "tagger") and hasattr(desc.tagger, "catalog") and desc.tagger.catalog:
                            for lbl in desc.tagger.catalog.labels:
                                if lbl.category:
                                    suggestions.add(lbl.category)
                    except Exception:
                        pass
        except Exception:
            pass
        self.cat_editor.set_suggestions(sorted(suggestions))

    def _on_scope_changed(self, index: int) -> None:
        if index < 0:
            return
        self._save_active_scope_to_state()
        self._current_scope = index
        self._load_active_scope_from_state()
        self._update_tooltips()

    # endregion

    # region Data Loading & Saving

    def load_config(self, cfg: AppConfig) -> None:
        """Populate baseline global filter and all per-model overrides from AppConfig."""
        self.blockSignals(True)
        self._global_filter = cfg.output_filter.model_dump(mode="json")
        self._models_data = [m.model_dump(mode="json") for m in cfg.inference.models]

        self._refresh_scope_combo()
        self._current_scope = 0
        self.scope_combo.setCurrentIndex(0)

        self._load_active_scope_from_state()
        self._update_category_suggestions()
        self._update_tooltips()
        self.blockSignals(False)

    def apply_to_dict(self, data: dict[str, Any]) -> None:
        """Persist current filter form into global output_filter and model overrides."""
        self._save_active_scope_to_state()

        # 1. Write global filter
        data["output_filter"] = copy.deepcopy(self._global_filter)

        # 2. Write per-model overrides
        inf_dict = data.setdefault("inference", {})
        models_list = inf_dict.setdefault("models", [])

        for i, m_state in enumerate(self._models_data):
            override = m_state.get("output_filter")
            if i < len(models_list):
                models_list[i]["output_filter"] = copy.deepcopy(override) if override else None

    def _load_active_scope_from_state(self) -> None:
        self.blockSignals(True)

        is_global = self._current_scope == 0
        self.banner_frame.setVisible(not is_global)

        if is_global:
            # Global Scope: all groups active and non-checkable
            for group in (
                self.thresh_group,
                self.cat_group,
                self.inc_exc_group,
                self.overrides_group,
                self.pfx_group,
                self.limits_group,
            ):
                group.blockSignals(True)
                group.setCheckable(False)
                group.setChecked(True)
                group.setTitle(self._section_titles[group])
                group.blockSignals(False)

            self._populate_widgets_from_dict(self._global_filter)
        else:
            # Model Scope: checkable groups indicate overrides
            model_idx = self._current_scope - 1
            m = self._models_data[model_idx]
            model_id = str(m.get("model_id") or f"Model {model_idx + 1}")
            self.banner_label.setText(
                f"<b>Configuring overrides for: <span style='color: #4fc3f7;'>{model_id}</span></b><br>"
                "<span style='color: #bbb;'>Unchecked sections inherit live baseline values from the Global Output Filter.</span>"
            )

            m_filter = m.get("output_filter") or {}

            # Section 1: Thresholds
            has_thresh = any(
                k in m_filter
                for k in ("default_threshold", "prefer_tag_level_thresholds", "tag_level_threshold_relative_offset")
            )
            self._setup_override_group(
                self.thresh_group,
                has_thresh,
                m_filter if has_thresh else self._global_filter,
            )

            # Section 2: Categories
            has_cat = "output_categories" in m_filter
            self._setup_override_group(
                self.cat_group,
                has_cat,
                m_filter if has_cat else self._global_filter,
            )

            # Section 3: Inclusions & Exclusions
            has_inc_exc = any(k in m_filter for k in ("include_tags", "exclude_tags"))
            self._setup_override_group(
                self.inc_exc_group,
                has_inc_exc,
                m_filter if has_inc_exc else self._global_filter,
            )

            # Section 4: Threshold Overrides
            has_overrides = any(k in m_filter for k in ("category_thresholds", "tag_thresholds"))
            self._setup_override_group(
                self.overrides_group,
                has_overrides,
                m_filter if has_overrides else self._global_filter,
            )

            # Section 5: Formatting & Prefixes
            has_pfx = any(
                k in m_filter for k in ("category_tag_prefix_mapping", "tag_prefix_overrides", "tag_replacements")
            )
            self._setup_override_group(
                self.pfx_group,
                has_pfx,
                m_filter if has_pfx else self._global_filter,
            )

            # Section 6: Limits
            has_limits = any(k in m_filter for k in ("max_tags_per_category", "max_tags_per_subset"))
            self._setup_override_group(
                self.limits_group,
                has_limits,
                m_filter if has_limits else self._global_filter,
            )

        self.blockSignals(False)

    def _setup_override_group(self, group: QGroupBox, is_overridden: bool, data_source: dict[str, Any]) -> None:
        group.blockSignals(True)
        group.setCheckable(True)
        group.setChecked(is_overridden)
        status_suffix = " (Custom Override)" if is_overridden else " (Inheriting from Global)"
        group.setTitle(f"{self._section_titles[group]}{status_suffix}")
        group.blockSignals(False)

        # Populate widgets with appropriate values
        self._populate_section_widgets(group, data_source)

    def _populate_widgets_from_dict(self, d: dict[str, Any]) -> None:
        self.default_thresh_spin.setValue(float(d.get("default_threshold", 0.40)))
        self.prefer_tlt_chk.setChecked(bool(d.get("prefer_tag_level_thresholds", True)))
        self.tlt_offset_spin.setValue(float(d.get("tag_level_threshold_relative_offset", 0.00)))

        self.cat_editor.set_items(d.get("output_categories", []))
        self.include_editor.set_items(d.get("include_tags", []))
        self.exclude_editor.set_items(d.get("exclude_tags", []))

        self.cat_thresh_editor.set_thresholds(d.get("category_thresholds", {}))
        self.tag_thresh_editor.set_thresholds(d.get("tag_thresholds", {}))
        self.cat_prefix_editor.set_mapping(d.get("category_tag_prefix_mapping", {}))
        self.tag_prefix_editor.set_mapping(d.get("tag_prefix_overrides", {}))
        self.tag_replacements_editor.set_mapping(d.get("tag_replacements", {}))
        self.cat_limit_editor.set_limits(d.get("max_tags_per_category", {}))
        self.subset_editor.set_subsets(d.get("max_tags_per_subset", []))

    def _populate_section_widgets(self, group: QGroupBox, d: dict[str, Any]) -> None:
        if group is self.thresh_group:
            self.default_thresh_spin.setValue(
                float(d.get("default_threshold", self._global_filter.get("default_threshold", 0.40)))
            )
            self.prefer_tlt_chk.setChecked(
                bool(d.get("prefer_tag_level_thresholds", self._global_filter.get("prefer_tag_level_thresholds", True)))
            )
            self.tlt_offset_spin.setValue(
                float(
                    d.get(
                        "tag_level_threshold_relative_offset",
                        self._global_filter.get("tag_level_threshold_relative_offset", 0.00),
                    )
                )
            )
        elif group is self.cat_group:
            self.cat_editor.set_items(d.get("output_categories", self._global_filter.get("output_categories", [])))
        elif group is self.inc_exc_group:
            self.include_editor.set_items(d.get("include_tags", self._global_filter.get("include_tags", [])))
            self.exclude_editor.set_items(d.get("exclude_tags", self._global_filter.get("exclude_tags", [])))
        elif group is self.overrides_group:
            self.cat_thresh_editor.set_thresholds(
                d.get("category_thresholds", self._global_filter.get("category_thresholds", {}))
            )
            self.tag_thresh_editor.set_thresholds(
                d.get("tag_thresholds", self._global_filter.get("tag_thresholds", {}))
            )
        elif group is self.pfx_group:
            self.cat_prefix_editor.set_mapping(
                d.get("category_tag_prefix_mapping", self._global_filter.get("category_tag_prefix_mapping", {}))
            )
            self.tag_prefix_editor.set_mapping(
                d.get("tag_prefix_overrides", self._global_filter.get("tag_prefix_overrides", {}))
            )
            self.tag_replacements_editor.set_mapping(
                d.get("tag_replacements", self._global_filter.get("tag_replacements", {}))
            )
        elif group is self.limits_group:
            self.cat_limit_editor.set_limits(
                d.get("max_tags_per_category", self._global_filter.get("max_tags_per_category", {}))
            )
            self.subset_editor.set_subsets(
                d.get("max_tags_per_subset", self._global_filter.get("max_tags_per_subset", []))
            )

    def _save_active_scope_to_state(self) -> None:
        if self._current_scope == 0:
            # Save to global filter
            self._global_filter["default_threshold"] = float(self.default_thresh_spin.value())
            self._global_filter["prefer_tag_level_thresholds"] = self.prefer_tlt_chk.isChecked()
            self._global_filter["tag_level_threshold_relative_offset"] = float(self.tlt_offset_spin.value())

            self._global_filter["output_categories"] = self.cat_editor.get_items()
            self._global_filter["include_tags"] = self.include_editor.get_items()
            self._global_filter["exclude_tags"] = self.exclude_editor.get_items()

            self._global_filter["category_thresholds"] = self.cat_thresh_editor.get_thresholds()
            self._global_filter["tag_thresholds"] = self.tag_thresh_editor.get_thresholds()
            self._global_filter["category_tag_prefix_mapping"] = self.cat_prefix_editor.get_mapping()
            self._global_filter["tag_prefix_overrides"] = self.tag_prefix_editor.get_mapping()
            self._global_filter["tag_replacements"] = self.tag_replacements_editor.get_mapping()
            self._global_filter["max_tags_per_category"] = self.cat_limit_editor.get_limits()
            self._global_filter["max_tags_per_subset"] = self.subset_editor.get_subsets()
        else:
            # Save overrides to model
            model_idx = self._current_scope - 1
            if 0 <= model_idx < len(self._models_data):
                m = self._models_data[model_idx]
                overrides: dict[str, Any] = {}

                if self.thresh_group.isChecked():
                    overrides["default_threshold"] = float(self.default_thresh_spin.value())
                    overrides["prefer_tag_level_thresholds"] = self.prefer_tlt_chk.isChecked()
                    overrides["tag_level_threshold_relative_offset"] = float(self.tlt_offset_spin.value())

                if self.cat_group.isChecked():
                    overrides["output_categories"] = self.cat_editor.get_items()

                if self.inc_exc_group.isChecked():
                    overrides["include_tags"] = self.include_editor.get_items()
                    overrides["exclude_tags"] = self.exclude_editor.get_items()

                if self.overrides_group.isChecked():
                    overrides["category_thresholds"] = self.cat_thresh_editor.get_thresholds()
                    overrides["tag_thresholds"] = self.tag_thresh_editor.get_thresholds()

                if self.pfx_group.isChecked():
                    overrides["category_tag_prefix_mapping"] = self.cat_prefix_editor.get_mapping()
                    overrides["tag_prefix_overrides"] = self.tag_prefix_editor.get_mapping()
                    overrides["tag_replacements"] = self.tag_replacements_editor.get_mapping()

                if self.limits_group.isChecked():
                    overrides["max_tags_per_category"] = self.cat_limit_editor.get_limits()
                    overrides["max_tags_per_subset"] = self.subset_editor.get_subsets()

                m["output_filter"] = overrides if overrides else None

    # endregion

    # region User Actions & Tooltips

    def _on_section_toggled(self, group: QGroupBox, checked: bool) -> None:
        if self._current_scope == 0:
            return  # In global scope, groups are always active

        status_suffix = " (Custom Override)" if checked else " (Inheriting from Global)"
        group.setTitle(f"{self._section_titles[group]}{status_suffix}")

        # If just unchecked, reset values to current global baseline
        if not checked:
            self._populate_section_widgets(group, self._global_filter)

        self._on_field_changed()
        self._update_tooltips()

    def _on_reset_overrides_clicked(self) -> None:
        if self._current_scope == 0:
            return

        model_idx = self._current_scope - 1
        if not (0 <= model_idx < len(self._models_data)):
            return

        model_id = str(self._models_data[model_idx].get("model_id") or f"Model {model_idx + 1}")
        res = QMessageBox.question(
            self,
            "Reset Filter Overrides",
            f"Are you sure you want to reset all filter overrides for '{model_id}'?\n\n"
            "This will revert all settings for this model to inherit from the Global Output Filter.",
            QMessageBox.StandardButton.Reset | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if res == QMessageBox.StandardButton.Reset:
            self._models_data[model_idx]["output_filter"] = None
            self._load_active_scope_from_state()
            self._refresh_scope_combo()
            self._update_tooltips()
            self.changed.emit()

    def _on_field_changed(self) -> None:
        self._save_active_scope_to_state()
        self.changed.emit()

    def _update_tooltips(self) -> None:
        """Apply contextual cross-scope comparison tooltips across all controls."""
        of_fields = OutputFilterConfig.model_fields
        is_global = self._current_scope == 0
        current_model_idx = self._current_scope - 1

        def build_tip(field_key: str, base_desc: str) -> str:
            lines = [base_desc]
            if is_global:
                overridden_by = []
                for idx, m in enumerate(self._models_data):
                    m_filter = m.get("output_filter") or {}
                    if field_key in m_filter:
                        val = m_filter[field_key]
                        m_id = m.get("model_id") or f"Model {idx + 1}"
                        overridden_by.append(f"• {m_id}: {val}")
                if overridden_by:
                    lines.append("\n⚡ Overridden by models:\n" + "\n".join(overridden_by))
            else:
                global_val = self._global_filter.get(field_key)
                lines.append(f"\n🌐 Global baseline: {global_val}")

                other_overrides = []
                for idx, m in enumerate(self._models_data):
                    if idx == current_model_idx:
                        continue
                    m_filter = m.get("output_filter") or {}
                    if field_key in m_filter:
                        val = m_filter[field_key]
                        m_id = m.get("model_id") or f"Model {idx + 1}"
                        other_overrides.append(f"• {m_id}: {val}")
                if other_overrides:
                    lines.append("\n⚡ Other models:\n" + "\n".join(other_overrides))

            return "\n".join(lines)

        self.default_thresh_spin.setToolTip(
            build_tip("default_threshold", of_fields["default_threshold"].description or "")
        )
        self.prefer_tlt_chk.setToolTip(
            build_tip("prefer_tag_level_thresholds", of_fields["prefer_tag_level_thresholds"].description or "")
        )
        self.tlt_offset_spin.setToolTip(
            build_tip(
                "tag_level_threshold_relative_offset",
                of_fields["tag_level_threshold_relative_offset"].description or "",
            )
        )
        self.cat_group.setToolTip(build_tip("output_categories", of_fields["output_categories"].description or ""))

    # endregion
