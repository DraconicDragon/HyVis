"""
models_page.py — Model session management with dynamic discovery from vibe.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
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

from hyvis.config import AppConfig
from hyvis.gui.widgets import StringListEditor


class ModelsPage(QWidget):
    """Configuration page for [[inference.models]]."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._models_data: list[dict[str, Any]] = []
        self._current_index: int = -1
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        splitter = QSplitter(self)

        # 1. Left: Models list
        left_widget = QWidget(splitter)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        left_layout.addWidget(QLabel("<b>Configured Models:</b>"))

        self.model_list = QListWidget(left_widget)
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

        # 2. Right: Active Model Settings
        right_scroll = QScrollArea(splitter)
        right_scroll.setWidgetResizable(True)
        right_container = QWidget()
        self.form_layout = QVBoxLayout(right_container)
        self.form_layout.setContentsMargins(12, 0, 0, 0)
        self.form_layout.setSpacing(14)

        # Model Parameters Group
        param_group = QGroupBox("Model Runtime Settings", right_container)
        param_layout = QFormLayout(param_group)
        param_layout.setSpacing(8)

        # Model ID dropdown (populated via vibe.list_models)
        self.model_id_combo = QComboBox(self)
        self.model_id_combo.setEditable(True)
        self._populate_available_models()
        self.model_id_combo.currentTextChanged.connect(self._on_field_changed)
        param_layout.addRow("Model ID:", self.model_id_combo)

        # Source
        source_row = QHBoxLayout()
        self.source_edit = QLineEdit(self)
        self.source_edit.setPlaceholderText("Default HuggingFace repo (leave empty)")
        self.source_edit.textChanged.connect(self._on_field_changed)
        source_row.addWidget(self.source_edit, stretch=1)

        self.browse_src_btn = QPushButton("Browse Folder...", self)
        self.browse_src_btn.clicked.connect(self._on_browse_source)
        source_row.addWidget(self.browse_src_btn)
        param_layout.addRow("Source Path / Repo:", source_row)

        # Device
        self.device_combo = QComboBox(self)
        self.device_combo.addItems(["auto", "cuda", "cpu", "mps", "xpu"])
        self.device_combo.setEditable(True)
        self.device_combo.currentTextChanged.connect(self._on_field_changed)
        param_layout.addRow("Hardware Device:", self.device_combo)

        # Backend
        self.backend_combo = QComboBox(self)
        self.backend_combo.addItems(["auto", "pytorch", "onnx"])
        self.backend_combo.currentTextChanged.connect(self._on_field_changed)
        param_layout.addRow("Execution Backend:", self.backend_combo)

        # Precision
        self.precision_combo = QComboBox(self)
        self.precision_combo.addItems(["auto", "fp16", "bf16", "fp32"])
        self.precision_combo.currentTextChanged.connect(self._on_field_changed)
        param_layout.addRow("Precision:", self.precision_combo)

        # Batch Size
        self.batch_spin = QSpinBox(self)
        self.batch_spin.setRange(1, 128)
        self.batch_spin.setValue(1)
        self.batch_spin.valueChanged.connect(self._on_field_changed)
        param_layout.addRow("Batch Size:", self.batch_spin)

        self.form_layout.addWidget(param_group)

        # Model Output Services Override
        svc_group = QGroupBox("Per-Model Destination Tag Services (Optional Override)", right_container)
        svc_layout = QVBoxLayout(svc_group)
        self.model_services_editor = StringListEditor(
            placeholder="Leave empty to use global output services...",
            parent=self,
        )
        self.model_services_editor.changed.connect(self._on_field_changed)
        svc_layout.addWidget(self.model_services_editor)
        self.form_layout.addWidget(svc_group)

        self.form_layout.addStretch()
        right_scroll.setWidget(right_container)
        splitter.addWidget(right_scroll)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

    def _populate_available_models(self) -> None:
        self.model_id_combo.blockSignals(True)
        self.model_id_combo.clear()
        try:
            import vibe

            models = sorted(vibe.list_models())
            self.model_id_combo.addItems(models)
        except Exception:
            # Fallback if vibe is not in python path during standalone UI test
            self.model_id_combo.addItems(["wd-swinv2-v3", "wd-eva02-large-v3", "jtp-3", "taggerine"])
        self.model_id_combo.blockSignals(False)

    def load_config(self, cfg: AppConfig) -> None:
        self.blockSignals(True)
        self._models_data = [m.model_dump(mode="json") for m in cfg.inference.models]

        self.model_list.blockSignals(True)
        self.model_list.clear()
        for m in self._models_data:
            self.model_list.addItem(QListWidgetItem(str(m.get("model_id", "unnamed"))))
        self.model_list.blockSignals(False)

        if self._models_data:
            self.model_list.setCurrentRow(0)
            self._load_model_to_form(0)

        self.blockSignals(False)

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

        self._current_index = index
        m = self._models_data[index]

        self.model_id_combo.blockSignals(True)
        self.source_edit.blockSignals(True)
        self.device_combo.blockSignals(True)
        self.backend_combo.blockSignals(True)
        self.precision_combo.blockSignals(True)
        self.batch_spin.blockSignals(True)

        self.model_id_combo.setCurrentText(str(m.get("model_id", "")))
        self.source_edit.setText(str(m.get("source") or ""))
        self.device_combo.setCurrentText(str(m.get("device") or "auto"))

        backend_val = m.get("backend")
        self.backend_combo.setCurrentText(str(backend_val) if backend_val else "auto")

        self.precision_combo.setCurrentText(str(m.get("precision") or "auto"))
        self.batch_spin.setValue(int(m.get("batch_size", 1)))

        svcs = m.get("output_tag_services")
        keys = svcs.get("keys", []) if svcs else []
        self.model_services_editor.set_items(keys)

        self.model_id_combo.blockSignals(False)
        self.source_edit.blockSignals(False)
        self.device_combo.blockSignals(False)
        self.backend_combo.blockSignals(False)
        self.precision_combo.blockSignals(False)
        self.batch_spin.blockSignals(False)

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

        svcs = self.model_services_editor.get_items()
        m["output_tag_services"] = {"keys": svcs} if svcs else None

        # Update sidebar list label
        item = self.model_list.item(index)
        if item and model_id:
            item.setText(model_id)

    def _on_field_changed(self) -> None:
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
