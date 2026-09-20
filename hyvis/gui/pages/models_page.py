"""
models_page.py — Model session management with dynamic discovery from vibe.
"""

from __future__ import annotations

import copy
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from hyvis.config import AppConfig, InferenceConfig, ModelConfig
from hyvis.gui.widgets import (
    SectionCard,
    SmoothScrollArea,
    TagServiceListEditor,
    add_form_row,
    bind_field_metadata,
    setup_field_tooltip,
)


class ModelsPage(QWidget):
    """Configuration page for [[inference.models]]."""

    changed = Signal()
    request_filter_scope = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._models_data: list[dict[str, Any]] = []
        self._current_index: int = -1
        self._writable_tag_services: dict[str, str] = {}
        self._is_loading_ui: bool = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        m_fields = ModelConfig.model_fields
        inf_fields = InferenceConfig.model_fields

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 10, 0, 10)

        splitter = QSplitter(self)
        splitter.setChildrenCollapsible(False)

        # 1. Left: Models list
        left_widget = QWidget(splitter)
        left_widget.setMinimumWidth(132)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        models_label = QLabel(f"<b>{inf_fields['models'].title}:</b>", left_widget)
        setup_field_tooltip(models_label, inf_fields["models"])
        left_layout.addWidget(models_label)

        self.model_list = QListWidget(left_widget)
        setup_field_tooltip(self.model_list, inf_fields["models"])
        self.model_list.currentRowChanged.connect(self._on_model_selected)
        left_layout.addWidget(self.model_list, stretch=1)

        btn_row = QHBoxLayout()
        self.add_model_btn = QPushButton("+ Add", left_widget)
        self.add_model_btn.clicked.connect(self._on_add_model)
        btn_row.addWidget(self.add_model_btn)

        self.remove_model_btn = QPushButton("- Remove", left_widget)
        self.remove_model_btn.clicked.connect(self._on_remove_model)
        btn_row.addWidget(self.remove_model_btn)
        left_layout.addLayout(btn_row)

        splitter.addWidget(left_widget)

        # 2. Right: Active Model Settings (Scrollable Container)
        right_scroll = SmoothScrollArea(splitter)
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        right_container = QWidget()
        self.form_layout = QVBoxLayout(right_container)
        self.form_layout.setContentsMargins(12, 0, 10, 0)
        self.form_layout.setSpacing(14)

        # Card 1: Model Runtime Parameters
        self.param_card = SectionCard("Model Runtime Settings", parent=right_container)
        param_layout = QFormLayout()
        param_layout.setSpacing(8)

        # Model ID
        self.model_id_combo = QComboBox(self)
        self.model_id_combo.setEditable(True)
        self._populate_available_models()
        self.model_id_combo.currentTextChanged.connect(self._on_field_changed)
        add_form_row(param_layout, m_fields["model_id"], self.model_id_combo)

        # Source
        source_box = QWidget(self)
        source_row = QHBoxLayout(source_box)
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(6)

        self.source_edit = QLineEdit(source_box)
        bind_field_metadata(self.source_edit, m_fields["source"])
        if not self.source_edit.placeholderText():
            self.source_edit.setPlaceholderText("Default HuggingFace repo (leave empty)")
        self.source_edit.textChanged.connect(self._on_field_changed)
        source_row.addWidget(self.source_edit, stretch=1)

        self.browse_src_btn = QPushButton("Browse Folder...", source_box)
        self.browse_src_btn.clicked.connect(self._on_browse_source)
        source_row.addWidget(self.browse_src_btn)

        add_form_row(param_layout, m_fields["source"], source_box)

        # Device
        self.device_combo = QComboBox(self)
        self.device_combo.addItems(["auto", "cuda", "cpu", "mps", "xpu"])
        self.device_combo.setEditable(True)
        self.device_combo.currentTextChanged.connect(self._on_field_changed)
        add_form_row(param_layout, m_fields["device"], self.device_combo)

        # Backend
        self.backend_combo = QComboBox(self)
        self.backend_combo.addItems(["auto", "pytorch", "onnx"])
        self.backend_combo.currentTextChanged.connect(self._on_field_changed)
        add_form_row(param_layout, m_fields["backend"], self.backend_combo)

        # Precision
        self.precision_combo = QComboBox(self)
        self.precision_combo.addItems(["auto", "fp16", "bf16", "fp32"])
        self.precision_combo.currentTextChanged.connect(self._on_field_changed)
        add_form_row(param_layout, m_fields["precision"], self.precision_combo)

        # Batch Size
        self.batch_spin = QSpinBox(self)
        self.batch_spin.setRange(1, 128)
        self.batch_spin.setValue(1)
        self.batch_spin.valueChanged.connect(self._on_field_changed)
        add_form_row(param_layout, m_fields["batch_size"], self.batch_spin)

        self.param_card.setContentLayout(param_layout)
        self.form_layout.addWidget(self.param_card)

        # Card 2: Model Output Services Override (Checkable)
        svc_title = m_fields["output_tag_services"].title or "Destination Tag Services"
        self.svc_card = SectionCard(
            title=svc_title,
            tooltip=m_fields["output_tag_services"].description or "",
            parent=right_container,
        )
        self.svc_card.setCheckable(True)
        self.svc_card.setChecked(False)
        self.svc_card.toggled.connect(self._on_svc_card_toggled)

        svc_layout = QVBoxLayout()
        self.model_services_editor = TagServiceListEditor(writable_only=True, parent=self)
        self.model_services_editor.changed.connect(self._on_field_changed)
        svc_layout.addWidget(self.model_services_editor)

        self.svc_card.setContentLayout(svc_layout)
        self.form_layout.addWidget(self.svc_card)

        # Card 3: Model Output Filter Status Card
        self.filter_card = SectionCard(
            title="Model Output Filter",
            tooltip="Configure model-specific output filter overrides that take precedence over the global filter.",
            parent=right_container,
        )
        filter_card_layout = QHBoxLayout()
        filter_card_layout.setContentsMargins(0, 0, 0, 0)
        filter_card_layout.setSpacing(10)

        self.filter_status_label = QLabel("Inheriting all settings from Global Filter", self)
        filter_card_layout.addWidget(self.filter_status_label, stretch=1)

        self.edit_filter_btn = QPushButton("⚙ Configure Filter Overrides...", self)
        self.edit_filter_btn.setToolTip("Switch to Filters page and edit custom overrides for this model")
        self.edit_filter_btn.clicked.connect(self._on_edit_filter_clicked)
        filter_card_layout.addWidget(self.edit_filter_btn)

        self.filter_card.setContentLayout(filter_card_layout)
        self.form_layout.addWidget(self.filter_card)

        self.form_layout.addStretch()
        right_scroll.setWidget(right_container)
        splitter.addWidget(right_scroll)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

    def update_services(self, all_tags: dict[str, str], writable_tags: dict[str, str]) -> None:
        """Update available tag services for per-model destination overrides."""
        self._writable_tag_services = dict(writable_tags)
        self.model_services_editor.set_available_services(writable_tags)

    def _populate_available_models(self) -> None:
        self.model_id_combo.blockSignals(True)
        self.model_id_combo.clear()
        try:
            import vibe

            models = sorted(vibe.list_models())
            self.model_id_combo.addItems(models)
        except Exception:
            self.model_id_combo.addItems(["wd-swinv2-v3", "wd-eva02-large-v3", "jtp-3", "taggerine"])
        self.model_id_combo.blockSignals(False)

    def load_config(self, cfg: AppConfig) -> None:
        self._is_loading_ui = True
        try:
            self._models_data = [m.model_dump(mode="json") for m in cfg.inference.models]

            self.model_list.blockSignals(True)
            self.model_list.clear()
            for m in self._models_data:
                self.model_list.addItem(QListWidgetItem(str(m.get("model_id", "unnamed"))))
            self.model_list.blockSignals(False)

            if self._models_data:
                self.model_list.setCurrentRow(0)
                self._load_model_to_form(0)
        finally:
            self._is_loading_ui = False

    def apply_to_dict(self, data: dict[str, Any]) -> None:
        self._save_form_to_model(self._current_index)
        inf_dict: dict[str, Any] = data.setdefault("inference", {})
        inf_dict["models"] = list(self._models_data)

    def _on_model_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._models_data):
            return
        self._save_form_to_model(self._current_index)
        self._load_model_to_form(row)

    def _load_model_to_form(self, index: int) -> None:
        if index < 0 or index >= len(self._models_data):
            return

        self._is_loading_ui = True
        try:
            self._current_index = index
            m = self._models_data[index]

            self.model_id_combo.setCurrentText(str(m.get("model_id", "")))
            self.source_edit.setText(str(m.get("source") or ""))
            self.device_combo.setCurrentText(str(m.get("device") or "auto"))

            backend_val = m.get("backend")
            self.backend_combo.setCurrentText(str(backend_val) if backend_val else "auto")

            self.precision_combo.setCurrentText(str(m.get("precision") or "auto"))
            self.batch_spin.setValue(int(m.get("batch_size", 1)))

            svcs = m.get("output_tag_services")
            if svcs is not None:
                self.svc_card.setChecked(True)
                keys = svcs.get("keys", []) if isinstance(svcs, dict) else getattr(svcs, "keys", [])
                self.model_services_editor.set_items(keys)
            else:
                self.svc_card.setChecked(False)
                self.model_services_editor.set_items([])

            if self._writable_tag_services:
                self.model_services_editor.set_available_services(self._writable_tag_services)

            self._update_filter_status_card(m)
        finally:
            self._is_loading_ui = False

    def _save_form_to_model(self, index: int) -> None:
        if index < 0 or index >= len(self._models_data):
            return

        m = self._models_data[index]
        model_id = self.model_id_combo.currentText().strip()
        m["model_id"] = model_id
        m["source"] = self.source_edit.text().strip() or None
        m["device"] = self.device_combo.currentText().strip()

        backend_text = self.backend_combo.currentText().strip()
        m["backend"] = None if backend_text in ("auto", "") else backend_text

        m["precision"] = self.precision_combo.currentText().strip()
        m["batch_size"] = self.batch_spin.value()

        if self.svc_card.isChecked():
            svcs = self.model_services_editor.get_items()
            m["output_tag_services"] = {"keys": svcs}
        else:
            m["output_tag_services"] = None

        # Update sidebar list label
        item = self.model_list.item(index)
        if item and model_id:
            item.setText(model_id)

    def _on_svc_card_toggled(self, checked: bool) -> None:
        del checked
        self._on_field_changed()

    def _on_field_changed(self) -> None:
        if self._is_loading_ui:
            return
        self._save_form_to_model(self._current_index)
        self.changed.emit()

    def _on_browse_source(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Local Model Directory")
        if folder:
            self.source_edit.setText(folder)
            self._on_field_changed()

    def _on_add_model(self) -> None:
        new_model = {
            "model_id": "wd-swinv2-v3",
            "source": None,
            "device": "auto",
            "backend": None,
            "precision": "auto",
            "batch_size": 1,
            "output_tag_services": None,
        }
        self._models_data.append(new_model)
        self.model_list.addItem(QListWidgetItem("wd-swinv2-v3"))
        self.model_list.setCurrentRow(len(self._models_data) - 1)
        self.changed.emit()

    def _on_remove_model(self) -> None:
        row = self.model_list.currentRow()
        if row < 0 or len(self._models_data) <= 1:
            return  # Must maintain at least one model

        self._models_data.pop(row)
        self.model_list.takeItem(row)
        next_row = max(0, row - 1)
        self.model_list.setCurrentRow(next_row)
        self.changed.emit()

    def _update_filter_status_card(self, m: dict[str, Any]) -> None:
        """Update the filter status card label based on active overrides."""
        m_filter = m.get("output_filter")
        if m_filter:
            override_count = len(m_filter)
            self.filter_status_label.setText(
                f"<span style='color: #4fc3f7;'>Custom overrides active ({override_count} section{'s' if override_count != 1 else ''})</span>"
            )
        else:
            self.filter_status_label.setText(
                "<span style='color: #888;'>Inheriting all settings from Global Filter</span>"
            )

    def update_filter_overrides(self, models_data: list[dict[str, Any]]) -> None:
        """Sync output_filter overrides modified in FiltersPage back into model data."""
        for i, m_src in enumerate(models_data):
            if i < len(self._models_data):
                self._models_data[i]["output_filter"] = (
                    copy.deepcopy(m_src.get("output_filter")) if m_src.get("output_filter") else None
                )

        if 0 <= self._current_index < len(self._models_data):
            self._update_filter_status_card(self._models_data[self._current_index])

    def _on_edit_filter_clicked(self) -> None:
        if self._current_index >= 0:
            self.request_filter_scope.emit(self._current_index)
