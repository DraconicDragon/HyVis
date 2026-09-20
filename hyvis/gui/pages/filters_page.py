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
    CategoryTagEditor,
    KeyValueEditor,
    SectionCard,
    StringListEditor,
    SubsetListEditor,
    ThresholdTableEditor,
    add_form_row,
    set_widget_override_state,
)


def _values_differ(val1: Any, val2: Any) -> bool:
    """Return True if two configuration values genuinely diverge."""
    if isinstance(val1, float) and isinstance(val2, (int, float)):
        return abs(val1 - float(val2)) > 1e-4
    return val1 != val2


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


class FiltersPage(QWidget):
    """Configuration page for [output_filter] and per-model filter overrides."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._global_filter: dict[str, Any] = {}
        self._models_data: list[dict[str, Any]] = []
        self._current_scope: int = 0  # 0 = Global, 1..N = Model index + 1
        self._model_draft_overrides: dict[int, dict[str, Any]] = {}
        self._is_loading_ui: bool = False  # Signal-leak protection guard

        self._card_meta: dict[SectionCard, tuple[str, list[str]]] = {}
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
            "QFrame { background-color: rgba(33, 150, 243, 0.08); "
            "border: 1px solid rgba(33, 150, 243, 0.3); border-radius: 4px; padding: 6px; }"
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

        # 3. Main Scroll Area for Filter Cards
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Card 1: Thresholds & TLT
        self.thresh_card = SectionCard("Threshold Settings", parent=container)
        self._card_meta[self.thresh_card] = (
            "Threshold Settings",
            ["default_threshold", "prefer_tag_level_thresholds", "tag_level_threshold_relative_offset"],
        )
        self.thresh_card.toggled.connect(lambda chk: self._on_card_toggled(self.thresh_card, chk))
        thresh_layout = QFormLayout()
        thresh_layout.setSpacing(8)

        self.default_thresh_spin = QDoubleSpinBox(self)
        self.default_thresh_spin.setRange(0.0, 1.0)
        self.default_thresh_spin.setSingleStep(0.05)
        self.default_thresh_spin.setDecimals(2)
        self.default_thresh_spin.setValue(0.40)
        self.default_thresh_spin.valueChanged.connect(lambda _: self._on_field_changed())
        add_form_row(thresh_layout, of_fields["default_threshold"], self.default_thresh_spin)

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
        add_form_row(thresh_layout, of_fields["tag_level_threshold_relative_offset"], self.tlt_offset_spin)

        self.thresh_card.setContentLayout(thresh_layout)
        layout.addWidget(self.thresh_card)

        # Card 2: Output Categories (Dynamic)
        cat_title = of_fields["output_categories"].title or "Output Categories"
        self.cat_card = SectionCard(cat_title, parent=container)
        self._card_meta[self.cat_card] = (cat_title, ["output_categories"])
        self.cat_card.toggled.connect(lambda chk: self._on_card_toggled(self.cat_card, chk))
        cat_layout = QVBoxLayout()
        self.cat_editor = CategoryTagEditor(self)
        self.cat_editor.changed.connect(self._on_field_changed)
        cat_layout.addWidget(self.cat_editor)
        self.cat_card.setContentLayout(cat_layout)
        layout.addWidget(self.cat_card)

        # Side-by-Side Row: Inclusions & Exclusions
        inc_exc_row = QHBoxLayout()
        inc_exc_row.setSpacing(10)

        # Card 3: Include Tags
        inc_title = of_fields["include_tags"].title or "Include Tags"
        self.inc_card = SectionCard(inc_title, parent=container)
        self._card_meta[self.inc_card] = (inc_title, ["include_tags"])
        self.inc_card.toggled.connect(lambda chk: self._on_card_toggled(self.inc_card, chk))
        inc_layout = QVBoxLayout()
        self.include_editor = StringListEditor(placeholder="Add always-included tag...", parent=self)
        self.include_editor.changed.connect(self._on_field_changed)
        inc_layout.addWidget(self.include_editor)
        self.inc_card.setContentLayout(inc_layout)
        inc_exc_row.addWidget(self.inc_card, stretch=1)

        # Card 4: Exclude Tags
        exc_title = of_fields["exclude_tags"].title or "Exclude Tags"
        self.exc_card = SectionCard(exc_title, parent=container)
        self._card_meta[self.exc_card] = (exc_title, ["exclude_tags"])
        self.exc_card.toggled.connect(lambda chk: self._on_card_toggled(self.exc_card, chk))
        exc_layout = QVBoxLayout()
        self.exclude_editor = StringListEditor(placeholder="Add always-excluded tag...", parent=self)
        self.exclude_editor.changed.connect(self._on_field_changed)
        exc_layout.addWidget(self.exclude_editor)
        self.exc_card.setContentLayout(exc_layout)
        inc_exc_row.addWidget(self.exc_card, stretch=1)

        layout.addLayout(inc_exc_row)

        # Card 5: Category Threshold Overrides
        cat_thresh_title = of_fields["category_thresholds"].title or "Category Threshold Overrides"
        self.cat_thresh_card = SectionCard(cat_thresh_title, parent=container)
        self._card_meta[self.cat_thresh_card] = (cat_thresh_title, ["category_thresholds"])
        self.cat_thresh_card.toggled.connect(lambda chk: self._on_card_toggled(self.cat_thresh_card, chk))
        cat_thresh_layout = QVBoxLayout()
        self.cat_thresh_editor = ThresholdTableEditor(target_header="Category Name", parent=self)
        self.cat_thresh_editor.changed.connect(self._on_field_changed)
        cat_thresh_layout.addWidget(self.cat_thresh_editor)
        self.cat_thresh_card.setContentLayout(cat_thresh_layout)
        layout.addWidget(self.cat_thresh_card)

        # Card 6: Tag Threshold Overrides
        tag_thresh_title = of_fields["tag_thresholds"].title or "Tag Threshold Overrides"
        self.tag_thresh_card = SectionCard(tag_thresh_title, parent=container)
        self._card_meta[self.tag_thresh_card] = (tag_thresh_title, ["tag_thresholds"])
        self.tag_thresh_card.toggled.connect(lambda chk: self._on_card_toggled(self.tag_thresh_card, chk))
        tag_thresh_layout = QVBoxLayout()
        self.tag_thresh_editor = ThresholdTableEditor(target_header="Raw Tag Name", parent=self)
        self.tag_thresh_editor.changed.connect(self._on_field_changed)
        tag_thresh_layout.addWidget(self.tag_thresh_editor)
        self.tag_thresh_card.setContentLayout(tag_thresh_layout)
        layout.addWidget(self.tag_thresh_card)

        # Card 7: Category Tag Prefix Mapping
        cat_pfx_title = of_fields["category_tag_prefix_mapping"].title or "Category Tag Prefix Mapping"
        self.cat_pfx_card = SectionCard(cat_pfx_title, parent=container)
        self._card_meta[self.cat_pfx_card] = (cat_pfx_title, ["category_tag_prefix_mapping"])
        self.cat_pfx_card.toggled.connect(lambda chk: self._on_card_toggled(self.cat_pfx_card, chk))
        cat_pfx_layout = QVBoxLayout()
        self.cat_prefix_editor = KeyValueEditor(key_header="Category", val_header="Prefix", parent=self)
        self.cat_prefix_editor.changed.connect(self._on_field_changed)
        cat_pfx_layout.addWidget(self.cat_prefix_editor)
        self.cat_pfx_card.setContentLayout(cat_pfx_layout)
        layout.addWidget(self.cat_pfx_card)

        # Card 8: Tag Prefix Overrides
        tag_pfx_title = of_fields["tag_prefix_overrides"].title or "Tag Prefix Overrides"
        self.tag_pfx_card = SectionCard(tag_pfx_title, parent=container)
        self._card_meta[self.tag_pfx_card] = (tag_pfx_title, ["tag_prefix_overrides"])
        self.tag_pfx_card.toggled.connect(lambda chk: self._on_card_toggled(self.tag_pfx_card, chk))
        tag_pfx_layout = QVBoxLayout()
        self.tag_prefix_editor = KeyValueEditor(key_header="Raw Tag", val_header="Prefix", parent=self)
        self.tag_prefix_editor.changed.connect(self._on_field_changed)
        tag_pfx_layout.addWidget(self.tag_prefix_editor)
        self.tag_pfx_card.setContentLayout(tag_pfx_layout)
        layout.addWidget(self.tag_pfx_card)

        # Card 9: Tag Replacements
        tag_rep_title = of_fields["tag_replacements"].title or "Tag Replacements"
        self.tag_rep_card = SectionCard(tag_rep_title, parent=container)
        self._card_meta[self.tag_rep_card] = (tag_rep_title, ["tag_replacements"])
        self.tag_rep_card.toggled.connect(lambda chk: self._on_card_toggled(self.tag_rep_card, chk))
        rep_layout = QVBoxLayout()
        self.tag_replacements_editor = KeyValueEditor(key_header="Original Tag", val_header="Replacement", parent=self)
        self.tag_replacements_editor.changed.connect(self._on_field_changed)
        rep_layout.addWidget(self.tag_replacements_editor)
        self.tag_rep_card.setContentLayout(rep_layout)
        layout.addWidget(self.tag_rep_card)

        # Card 10: Max Tags Per Category
        cat_lim_title = of_fields["max_tags_per_category"].title or "Max Tags Per Category"
        self.cat_limit_card = SectionCard(cat_lim_title, parent=container)
        self._card_meta[self.cat_limit_card] = (cat_lim_title, ["max_tags_per_category"])
        self.cat_limit_card.toggled.connect(lambda chk: self._on_card_toggled(self.cat_limit_card, chk))
        cat_limit_layout = QVBoxLayout()
        self.cat_limit_editor = CategoryLimitEditor(self)
        self.cat_limit_editor.changed.connect(self._on_field_changed)
        cat_limit_layout.addWidget(self.cat_limit_editor)
        self.cat_limit_card.setContentLayout(cat_limit_layout)
        layout.addWidget(self.cat_limit_card)

        # Card 11: Joint Subset Limits
        subset_title = of_fields["max_tags_per_subset"].title or "Joint Subset Limits"
        self.subset_card = SectionCard(subset_title, parent=container)
        self._card_meta[self.subset_card] = (subset_title, ["max_tags_per_subset"])
        self.subset_card.toggled.connect(lambda chk: self._on_card_toggled(self.subset_card, chk))
        subset_layout = QVBoxLayout()
        self.subset_editor = SubsetListEditor(self)
        self.subset_editor.changed.connect(self._on_field_changed)
        subset_layout.addWidget(self.subset_editor)
        self.subset_card.setContentLayout(subset_layout)
        layout.addWidget(self.subset_card)

        scroll.setWidget(container)
        root.addWidget(scroll, stretch=1)

    # region Scope & Synchronization

    def sync_models(self, models_data: list[dict[str, Any]]) -> None:
        """Update active models list and refresh scope dropdown options."""
        self._models_data = list(models_data)
        self._refresh_scope_combo()
        self._update_category_suggestions()
        self._update_badges_and_tooltips()

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
            ovr = m.get("output_filter")
            if ovr and isinstance(ovr, dict):
                # Count ONLY settings that genuinely differ from global
                divergent_keys = [
                    k for k, v in ovr.items() if v is not None and _values_differ(v, self._global_filter.get(k))
                ]
                if divergent_keys:
                    status = f"Custom Overrides ({len(divergent_keys)})"
                else:
                    status = "Inheriting Global"
            else:
                status = "Inheriting Global"
            self.scope_combo.addItem(f"Model {i + 1}: {model_id} ({status})")

        new_idx = min(prev_idx, self.scope_combo.count() - 1) if prev_idx >= 0 else 0
        self.scope_combo.setCurrentIndex(new_idx)
        self.scope_combo.blockSignals(False)

    def _update_category_suggestions(self) -> None:
        """Collect category suggestions strictly from loaded models via vibe."""
        suggestions: set[str] = set()
        try:
            import vibe

            for m in self._models_data:
                m_id = m.get("model_id")
                if m_id:
                    try:
                        desc = vibe.describe(m_id)
                        if hasattr(desc, "tagger") and hasattr(desc.tagger, "catalog") and desc.tagger.catalog:
                            for lbl in desc.tagger.catalog.labels:
                                if getattr(lbl, "category", None):
                                    suggestions.add(lbl.category)
                    except Exception:
                        pass
        except Exception:
            pass

        sorted_suggs = sorted(suggestions)
        self.cat_editor.set_suggestions(sorted_suggs)
        self.cat_limit_editor.set_suggestions(sorted_suggs)

    def _on_scope_changed(self, index: int) -> None:
        if self._is_loading_ui or index < 0:
            return

        # 1. Save UI state from previous scope
        self._save_active_scope_to_state()

        # 2. Switch to requested scope
        self._current_scope = index

        # 3. Load target scope into UI
        self._load_active_scope_from_state()

    # endregion

    # region Data Loading & Saving

    def load_config(self, cfg: AppConfig) -> None:
        """Populate baseline global filter and all per-model overrides from AppConfig."""
        self._is_loading_ui = True
        try:
            self._global_filter = cfg.output_filter.model_dump(mode="json")
            self._models_data = [m.model_dump(mode="json") for m in cfg.inference.models]
            self._model_draft_overrides.clear()

            # Pre-seed draft cache with active model overrides
            for i, m in enumerate(self._models_data):
                if m.get("output_filter"):
                    self._model_draft_overrides[i] = copy.deepcopy(m["output_filter"])

            self._refresh_scope_combo()
            self._current_scope = 0
            self.scope_combo.setCurrentIndex(0)

            self._load_active_scope_from_state()
            self._update_category_suggestions()
        finally:
            self._is_loading_ui = False

        self._update_badges_and_tooltips()

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
        """Load the active scope (Global or Model) into the form widgets under signal guard."""
        self._is_loading_ui = True
        try:
            is_global = self._current_scope == 0
            self.banner_frame.setVisible(not is_global)

            if is_global:
                for card, (base_title, _) in self._card_meta.items():
                    card.setCheckable(False)
                    card.setChecked(True)
                    card.setTitle(base_title)

                self._populate_all_widgets(self._global_filter)
            else:
                model_idx = self._current_scope - 1
                m = self._models_data[model_idx]
                model_id = str(m.get("model_id") or f"Model {model_idx + 1}")
                self.banner_label.setText(
                    f"<b>Configuring overrides for: <span style='color: #38bdf8;'>{model_id}</span></b><br>"
                    "<span style='color: #aaa;'>Unchecked cards inherit live baseline settings from Global Output Filter.</span>"
                )

                m_filter = m.get("output_filter") or {}

                for card, (base_title, keys) in self._card_meta.items():
                    is_card_overridden = any(k in m_filter for k in keys)
                    card.setCheckable(True)
                    card.setChecked(is_card_overridden)
                    card.setTitle(base_title)

                    # Populate from model override if active, else from global baseline
                    src = m_filter if is_card_overridden else self._global_filter
                    self._populate_card_widgets(card, src)

        finally:
            self._is_loading_ui = False

        self._update_badges_and_tooltips()

    def _populate_all_widgets(self, d: dict[str, Any]) -> None:
        for card in self._card_meta:
            self._populate_card_widgets(card, d)

    def _populate_card_widgets(self, card: SectionCard, d: dict[str, Any]) -> None:
        if card is self.thresh_card:
            self.default_thresh_spin.setValue(float(d.get("default_threshold", 0.40)))
            self.prefer_tlt_chk.setChecked(bool(d.get("prefer_tag_level_thresholds", True)))
            self.tlt_offset_spin.setValue(float(d.get("tag_level_threshold_relative_offset", 0.00)))
        elif card is self.cat_card:
            self.cat_editor.set_items(d.get("output_categories", []))
        elif card is self.inc_card:
            self.include_editor.set_items(d.get("include_tags", []))
        elif card is self.exc_card:
            self.exclude_editor.set_items(d.get("exclude_tags", []))
        elif card is self.cat_thresh_card:
            self.cat_thresh_editor.set_thresholds(d.get("category_thresholds", {}))
        elif card is self.tag_thresh_card:
            self.tag_thresh_editor.set_thresholds(d.get("tag_thresholds", {}))
        elif card is self.cat_pfx_card:
            self.cat_prefix_editor.set_mapping(d.get("category_tag_prefix_mapping", {}))
        elif card is self.tag_pfx_card:
            self.tag_prefix_editor.set_mapping(d.get("tag_prefix_overrides", {}))
        elif card is self.tag_rep_card:
            self.tag_replacements_editor.set_mapping(d.get("tag_replacements", {}))
        elif card is self.cat_limit_card:
            self.cat_limit_editor.set_limits(d.get("max_tags_per_category", {}))
        elif card is self.subset_card:
            self.subset_editor.set_subsets(d.get("max_tags_per_subset", []))

    def _save_active_scope_to_state(self) -> None:
        """Capture active UI form values into state without leaking between scopes."""
        if self._current_scope == 0:
            # 1. Global Scope: collect all widget values into _global_filter
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
            # 2. Model Scope: update draft cache and persist only checked cards
            model_idx = self._current_scope - 1
            if 0 <= model_idx < len(self._models_data):
                m = self._models_data[model_idx]
                draft = self._model_draft_overrides.setdefault(model_idx, {})

                # Update draft cache for checked cards
                if self.thresh_card.isChecked():
                    draft["default_threshold"] = float(self.default_thresh_spin.value())
                    draft["prefer_tag_level_thresholds"] = self.prefer_tlt_chk.isChecked()
                    draft["tag_level_threshold_relative_offset"] = float(self.tlt_offset_spin.value())
                if self.cat_card.isChecked():
                    draft["output_categories"] = self.cat_editor.get_items()
                if self.inc_card.isChecked():
                    draft["include_tags"] = self.include_editor.get_items()
                if self.exc_card.isChecked():
                    draft["exclude_tags"] = self.exclude_editor.get_items()
                if self.cat_thresh_card.isChecked():
                    draft["category_thresholds"] = self.cat_thresh_editor.get_thresholds()
                if self.tag_thresh_card.isChecked():
                    draft["tag_thresholds"] = self.tag_thresh_editor.get_thresholds()
                if self.cat_pfx_card.isChecked():
                    draft["category_tag_prefix_mapping"] = self.cat_prefix_editor.get_mapping()
                if self.tag_pfx_card.isChecked():
                    draft["tag_prefix_overrides"] = self.tag_prefix_editor.get_mapping()
                if self.tag_rep_card.isChecked():
                    draft["tag_replacements"] = self.tag_replacements_editor.get_mapping()
                if self.cat_limit_card.isChecked():
                    draft["max_tags_per_category"] = self.cat_limit_editor.get_limits()
                if self.subset_card.isChecked():
                    draft["max_tags_per_subset"] = self.subset_editor.get_subsets()

                # Build final overrides dict containing only checked keys
                active_overrides: dict[str, Any] = {}
                for card, (_, keys) in self._card_meta.items():
                    if card.isChecked():
                        for k in keys:
                            if k in draft:
                                active_overrides[k] = draft[k]

                m["output_filter"] = active_overrides if active_overrides else None

    # endregion

    # region User Actions & Live Diff Tooltips

    def _on_card_toggled(self, card: SectionCard, checked: bool) -> None:
        if self._is_loading_ui or self._current_scope == 0:
            return

        model_idx = self._current_scope - 1
        _, keys = self._card_meta[card]
        draft = self._model_draft_overrides.get(model_idx, {})

        self._is_loading_ui = True
        try:
            if checked:
                # Re-checked: restore previously customized values if drafted, else baseline
                src = {k: draft[k] for k in keys if k in draft} or self._global_filter
                self._populate_card_widgets(card, src)
            else:
                # Unchecked: save current values into draft memory, then populate global baseline
                self._save_active_scope_to_state()
                self._populate_card_widgets(card, self._global_filter)
        finally:
            self._is_loading_ui = False

        self._save_active_scope_to_state()
        self._refresh_scope_combo()
        self._update_badges_and_tooltips()
        self.changed.emit()

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
            if model_idx in self._model_draft_overrides:
                self._model_draft_overrides[model_idx].clear()

            self._load_active_scope_from_state()
            self._refresh_scope_combo()
            self._update_badges_and_tooltips()
            self.changed.emit()

    def _on_field_changed(self) -> None:
        if self._is_loading_ui:
            return

        self._save_active_scope_to_state()
        self._refresh_scope_combo()
        self._update_badges_and_tooltips()
        self.changed.emit()

    def _update_badges_and_tooltips(self) -> None:
        """Update card badges, cyan border highlighting, and input diff tooltips."""
        of_fields = OutputFilterConfig.model_fields
        is_global = self._current_scope == 0
        current_model_idx = self._current_scope - 1

        # 1. Update Card Badges & Title Tooltips
        for card, (base_title, keys) in self._card_meta.items():
            primary_key = keys[0]
            field_info = of_fields.get(primary_key)
            base_desc = field_info.description if field_info and field_info.description else ""

            if is_global:
                # Global Scope: detect if any model overrides any key in this card with a differing value
                has_divergence = False
                for m in self._models_data:
                    m_filter = m.get("output_filter") or {}
                    if any(k in m_filter and _values_differ(m_filter[k], self._global_filter.get(k)) for k in keys):
                        has_divergence = True
                        break

                if has_divergence:
                    card.setBadge("⚡ Overridden", color="#38bdf8")
                    card.set_info_tooltip(f"{base_desc}\n\n⚡ Overridden in one or more models.")
                else:
                    card.setBadge("")
                    card.set_info_tooltip(base_desc)
            else:
                # Model Scope: indicate custom override vs inherited
                if card.isChecked():
                    card.setBadge("Custom Override", color="#38bdf8")
                    card.set_info_tooltip(f"{base_desc}\n\nCustom override active for this model.")
                else:
                    card.setBadge("Inherited from Global", color="#888")
                    card.set_info_tooltip(f"{base_desc}\n\nInherits live baseline values from Global Output Filter.")

        # 2. Update Input Widget Highlighting & Tooltips across all cards
        self._update_widget_diff(
            self.default_thresh_spin,
            "default_threshold",
            of_fields["default_threshold"].description or "",
            is_global,
            current_model_idx,
        )
        self._update_widget_diff(
            self.prefer_tlt_chk,
            "prefer_tag_level_thresholds",
            of_fields["prefer_tag_level_thresholds"].description or "",
            is_global,
            current_model_idx,
        )
        self._update_widget_diff(
            self.tlt_offset_spin,
            "tag_level_threshold_relative_offset",
            of_fields["tag_level_threshold_relative_offset"].description or "",
            is_global,
            current_model_idx,
        )
        self._update_widget_diff(
            self.cat_editor,
            "output_categories",
            of_fields["output_categories"].description or "",
            is_global,
            current_model_idx,
        )
        self._update_widget_diff(
            self.include_editor,
            "include_tags",
            of_fields["include_tags"].description or "",
            is_global,
            current_model_idx,
        )
        self._update_widget_diff(
            self.exclude_editor,
            "exclude_tags",
            of_fields["exclude_tags"].description or "",
            is_global,
            current_model_idx,
        )
        self._update_widget_diff(
            self.cat_thresh_editor,
            "category_thresholds",
            of_fields["category_thresholds"].description or "",
            is_global,
            current_model_idx,
        )
        self._update_widget_diff(
            self.tag_thresh_editor,
            "tag_thresholds",
            of_fields["tag_thresholds"].description or "",
            is_global,
            current_model_idx,
        )
        self._update_widget_diff(
            self.cat_prefix_editor,
            "category_tag_prefix_mapping",
            of_fields["category_tag_prefix_mapping"].description or "",
            is_global,
            current_model_idx,
        )
        self._update_widget_diff(
            self.tag_prefix_editor,
            "tag_prefix_overrides",
            of_fields["tag_prefix_overrides"].description or "",
            is_global,
            current_model_idx,
        )
        self._update_widget_diff(
            self.tag_replacements_editor,
            "tag_replacements",
            of_fields["tag_replacements"].description or "",
            is_global,
            current_model_idx,
        )
        self._update_widget_diff(
            self.cat_limit_editor,
            "max_tags_per_category",
            of_fields["max_tags_per_category"].description or "",
            is_global,
            current_model_idx,
        )
        self._update_widget_diff(
            self.subset_editor,
            "max_tags_per_subset",
            of_fields["max_tags_per_subset"].description or "",
            is_global,
            current_model_idx,
        )

    def _update_widget_diff(
        self,
        widget: QWidget,
        field_name: str,
        base_desc: str,
        is_global: bool,
        current_model_idx: int,
    ) -> None:
        global_val = self._global_filter.get(field_name)

        if is_global:
            # Check which models have a value that genuinely diverges from global
            divergent_models: list[str] = []
            for idx, m in enumerate(self._models_data):
                m_filter = m.get("output_filter") or {}
                if field_name in m_filter and _values_differ(m_filter[field_name], global_val):
                    m_id = m.get("model_id") or f"Model {idx + 1}"
                    divergent_models.append(f"• {m_id}: {m_filter[field_name]}")

            if divergent_models:
                set_widget_override_state(widget, is_overridden=True)
                widget.setToolTip(f"{base_desc}\n\n⚡ Overridden by models:\n" + "\n".join(divergent_models))
            else:
                set_widget_override_state(widget, is_overridden=False)
                widget.setToolTip(base_desc)
        else:
            set_widget_override_state(widget, is_overridden=False)
            tip = f"{base_desc}\n\n🌐 Global baseline: {global_val}"

            other_models: list[str] = []
            for idx, m in enumerate(self._models_data):
                if idx == current_model_idx:
                    continue
                m_filter = m.get("output_filter") or {}
                if field_name in m_filter and _values_differ(m_filter[field_name], global_val):
                    m_id = m.get("model_id") or f"Model {idx + 1}"
                    other_models.append(f"• {m_id}: {m_filter[field_name]}")

            if other_models:
                tip += "\n\n⚡ Other model overrides:\n" + "\n".join(other_models)

            widget.setToolTip(tip)

    # endregion
