"""
models_page.py — Model session management with dynamic discovery from vibe.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hyvis.config import AppConfig, InferenceConfig, ModelConfig
from hyvis.gui.base_widgets import AutoResizingTableWidget, SmoothScrollArea
from hyvis.gui.pages.base import BaseConfigPage
from hyvis.gui.theme import COLOR_STATUS_READY, COLOR_STATUS_WARNING, CardTheme
from hyvis.gui.widgets import (
    SectionCard,
    TagServiceListEditor,
    add_form_row,
    bind_field_metadata,
    setup_field_tooltip,
)


def _format_bytes(num_bytes: float) -> str:
    """Format byte counts into clean human-readable strings."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024**2:
        return f"{num_bytes / 1024:.1f} KB"
    elif num_bytes < 1024**3:
        return f"{num_bytes / (1024**2):.1f} MB"
    else:
        return f"{num_bytes / (1024**3):.2f} GB"


class _HfSizeWorkerSignals(QObject):
    success = Signal(dict)  # {filename: size_in_bytes}
    error = Signal(str)


class _HfSizeWorker(QRunnable):
    """Background worker querying Hugging Face API for file sizes without downloading."""

    def __init__(self, repo_id: str) -> None:
        super().__init__()
        self.repo_id = repo_id
        self.signals = _HfSizeWorkerSignals()

    def run(self) -> None:
        try:
            from huggingface_hub import HfApi

            api = HfApi()
            info = api.model_info(self.repo_id, files_metadata=True)
            sizes = {s.rfilename: s.size for s in (info.siblings or []) if s.rfilename and s.size is not None}
            self.signals.success.emit(sizes)
        except Exception as exc:
            self.signals.error.emit(str(exc))


class ModelsPage(BaseConfigPage):
    """Configuration page for [[inference.models]]."""

    request_filter_scope = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._models_data: list[dict[str, Any]] = []
        self._current_index: int = -1
        self._writable_tag_services: dict[str, str] = {}
        self._remote_sizes_cache: dict[str, dict[str, int]] = {}
        self._active_size_worker: _HfSizeWorker | None = None
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
        left_widget.setMinimumWidth(145)
        left_widget.setMaximumWidth(380)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        models_label = QLabel(f"<b>{inf_fields['models'].title}:</b>", left_widget)
        setup_field_tooltip(models_label, inf_fields["models"])
        left_layout.addWidget(models_label)

        self.model_list = QListWidget(left_widget)
        setup_field_tooltip(self.model_list, inf_fields["models"])
        self.model_list.currentRowChanged.connect(self._on_model_selected)
        self.model_list.setStyleSheet("QListWidget::item { padding: 8px; }")
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

        # Card 1: Model & Artifacts
        self.artifacts_card = SectionCard("Model & Artifacts", parent=right_container)
        self.form_layout.addWidget(self.artifacts_card)

        artifacts_form = QFormLayout()
        artifacts_form.setSpacing(8)

        # Apply standard padding to the form rows so they match the other cards in the UI
        pad = self.style().pixelMetric(QStyle.PixelMetric.PM_LayoutLeftMargin)
        artifacts_form.setContentsMargins(pad, 0, pad, 0)

        # Model ID
        self.model_id_combo = QComboBox()
        self.model_id_combo.setEditable(True)
        self._populate_available_models()
        self.model_id_combo.currentTextChanged.connect(self._on_model_id_changed)
        add_form_row(artifacts_form, m_fields, "model_id", self.model_id_combo)

        # Execution Backend
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["auto", "pytorch", "onnx"])
        self.backend_combo.currentTextChanged.connect(self._on_backend_changed)
        add_form_row(artifacts_form, m_fields, "backend", self.backend_combo)

        # Model Variant (variant_id)
        self.variant_combo = QComboBox()
        self.variant_combo.currentIndexChanged.connect(self._on_variant_changed)
        add_form_row(artifacts_form, m_fields, "variant_id", self.variant_combo)

        # Source Path / Repo
        source_box = QWidget()
        source_row = QHBoxLayout(source_box)
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(6)

        self.source_edit = QLineEdit(source_box)
        bind_field_metadata(self.source_edit, m_fields["source"])
        if not self.source_edit.placeholderText():
            self.source_edit.setPlaceholderText("Default HF repo (or 'local:/path' or 'user/repo')")
        self.source_edit.textChanged.connect(self._on_field_changed)
        source_row.addWidget(self.source_edit, stretch=1)

        self.browse_src_btn = QPushButton("Browse Folder...", source_box)
        self.browse_src_btn.clicked.connect(self._on_browse_source)
        source_row.addWidget(self.browse_src_btn)

        add_form_row(artifacts_form, m_fields, "source", source_box)
        self.artifacts_card.content_layout.addLayout(artifacts_form)

        # Model Details Nested SectionCard
        self.info_card = SectionCard(title="Model Details")
        self.info_layout = QFormLayout()
        self.info_layout.setContentsMargins(4, 4, 4, 6)
        self.info_layout.setHorizontalSpacing(14)
        self.info_layout.setVerticalSpacing(6)

        self.info_name_lbl = QLabel()
        self.info_name_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.info_layout.addRow(self._make_detail_label("Name:"), self.info_name_lbl)

        self.info_desc_lbl = self._make_wrapping_label()
        self.info_layout.addRow(self._make_detail_label("Description:"), self.info_desc_lbl)

        self.info_variant_lbl = self._make_wrapping_label()
        self.info_layout.addRow(self._make_detail_label("Variant:"), self.info_variant_lbl)

        self.info_cats_lbl = self._make_wrapping_label()
        self.info_layout.addRow(self._make_detail_label("Categories:"), self.info_cats_lbl)

        self.info_repo_lbl = QLabel()
        self.info_repo_lbl.setOpenExternalLinks(True)
        self.info_layout.addRow(self._make_detail_label("Repository:"), self.info_repo_lbl)

        # Availability row: Status on left, Show/Hide Files toggle on right
        avail_box = QWidget()
        avail_layout = QHBoxLayout(avail_box)
        avail_layout.setContentsMargins(0, 0, 0, 0)
        avail_layout.setSpacing(8)

        self.info_avail_lbl = QLabel(avail_box)
        self.info_avail_lbl.setText("<span style='color: #888;'>Checking local files...</span>")
        avail_layout.addWidget(self.info_avail_lbl, stretch=1)

        self.toggle_files_btn = QPushButton("Show Files", avail_box)
        self.toggle_files_btn.setFixedHeight(24)
        self.toggle_files_btn.clicked.connect(self._on_toggle_files)
        avail_layout.addWidget(self.toggle_files_btn)

        self.info_layout.addRow(self._make_detail_label("Availability:"), avail_box)
        self.info_card.content_layout.addLayout(self.info_layout)

        # Collapsible Artifact Files Container
        self.files_container = QWidget()
        files_vbox = QVBoxLayout(self.files_container)
        files_vbox.setContentsMargins(0, 4, 0, 0)
        files_vbox.setSpacing(6)

        # 4-Column Table: File | Status | Local Size | Remote Size
        self.files_table = AutoResizingTableWidget(
            0, 4, parent=self.files_container
        )
        self.files_table.setHorizontalHeaderLabels(["File", "Status", "Local Size", "Remote Size"])
        self.files_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.files_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.files_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.files_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.files_table.setColumnWidth(1, 95)
        self.files_table.setColumnWidth(2, 90)
        self.files_table.setColumnWidth(3, 95)
        self.files_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.files_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        files_vbox.addWidget(self.files_table)

        # File Size Mismatch Warning Card (Hidden by default)
        self.mismatch_card = SectionCard(theme=CardTheme.WARNING_VIVID, parent=self.files_container)
        mismatch_layout = QVBoxLayout()
        mismatch_layout.setContentsMargins(6, 4, 6, 4)
        mismatch_layout.setSpacing(4)
        self.mismatch_lbl = QLabel(self.mismatch_card)
        self.mismatch_lbl.setWordWrap(True)
        self.mismatch_lbl.setStyleSheet("border: none; background: transparent;")
        mismatch_layout.addWidget(self.mismatch_lbl)
        self.mismatch_card.setContentLayout(mismatch_layout)
        self.mismatch_card.setVisible(False)
        files_vbox.addWidget(self.mismatch_card)

        # Bottom summary & Right-aligned check download size button
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)

        self.download_summary_lbl = QLabel(self.files_container)
        self.download_summary_lbl.setStyleSheet("color: #8a9ba5;")
        action_row.addWidget(self.download_summary_lbl, stretch=1)

        self.check_size_btn = QPushButton("Check Remote Sizes", self.files_container)
        self.check_size_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.check_size_btn.clicked.connect(self._on_check_download_size)
        action_row.addWidget(self.check_size_btn)

        files_vbox.addLayout(action_row)
        self.files_container.setVisible(False)
        self.info_card.content_layout.addWidget(self.files_container)

        self.artifacts_card.content_layout.addWidget(self.info_card)

        # Card 2: Hardware & Execution
        self.runtime_card = SectionCard("Hardware & Execution", parent=right_container)
        runtime_layout = QFormLayout()
        runtime_layout.setSpacing(8)

        # Device
        self.device_combo = QComboBox()
        self.device_combo.addItems(["auto", "cuda", "cpu", "mps", "xpu"])
        self.device_combo.setEditable(True)
        self.device_combo.currentTextChanged.connect(self._on_device_changed)
        add_form_row(runtime_layout, m_fields, "device", self.device_combo)

        # Precision
        self.precision_combo = QComboBox()
        self.precision_combo.addItems(["auto", "fp16", "bf16", "fp32"])
        self.precision_combo.currentTextChanged.connect(self._on_field_changed)
        add_form_row(runtime_layout, m_fields, "precision", self.precision_combo)

        # Batch Size
        self.batch_spin = QSpinBox()
        self.batch_spin.setRange(1, 128)
        self.batch_spin.setValue(1)
        self.batch_spin.valueChanged.connect(self._on_field_changed)
        add_form_row(runtime_layout, m_fields, "batch_size", self.batch_spin)

        self.runtime_card.setContentLayout(runtime_layout)
        self.form_layout.addWidget(self.runtime_card)

        # Card 3: Model Output Services Override
        svc_title = m_fields["output_tag_services"].title or "Destination Tag Services"
        self.svc_card = SectionCard(title=svc_title, field_name="output_tag_services", parent=right_container)
        self.svc_card.setCheckable(True)
        self.svc_card.setChecked(False)
        self.svc_card.toggled.connect(self._on_svc_card_toggled)

        svc_layout = QVBoxLayout()
        self.model_services_editor = TagServiceListEditor(writable_only=True, parent=self)
        self.model_services_editor.changed.connect(self._on_field_changed)
        svc_layout.addWidget(self.model_services_editor)

        self.svc_card.setContentLayout(svc_layout)
        self.form_layout.addWidget(self.svc_card)

        # Card 4: Model Output Filter Status Card
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
        """Populate model_id combo strictly with models that produce tag outputs."""
        self.model_id_combo.blockSignals(True)
        self.model_id_combo.clear()
        try:
            import vibe
            from vibe.metadata import OutputKind

            taggers: list[str] = []
            for mid in vibe.list_models():
                # Hide generic timm models from the UI
                if mid.startswith("generic-timm-"):
                    continue

                try:
                    desc = vibe.describe(mid)
                    kind = desc.output.kind
                    is_tagger = (kind == OutputKind.TAGS) or (getattr(kind, "value", str(kind)) == "tags")
                    if is_tagger:
                        taggers.append(mid)
                except Exception:
                    taggers.append(mid)

            self.model_id_combo.addItems(sorted(taggers))
        except Exception:
            self.model_id_combo.addItems(["wd-swinv2-v3", "wd-eva02-large-v3", "jtp-3", "taggerine"])
        self.model_id_combo.blockSignals(False)

    def _update_backend_and_variants(
        self,
        model_id: str,
        preferred_backend: str | None = None,
        preferred_variant: str | None = None,
    ) -> None:
        """Query vibe variants for the model, update backends and variants, and refresh details."""
        try:
            import vibe

            desc = vibe.describe(model_id)
            variants = desc.variants
        except Exception:
            desc = None
            variants = ()

        # 1. Inspect supported backends across variants
        found_backends: list[str] = []
        for v in variants:
            b_val = (v.backend.value if hasattr(v.backend, "value") else str(v.backend)).lower()
            if b_val not in found_backends:
                found_backends.append(b_val)

        # 2. Update Backend ComboBox
        self.backend_combo.blockSignals(True)
        self.backend_combo.clear()

        if len(found_backends) <= 1:
            # Single-backend model: Lock to that backend and disable
            only_backend = found_backends[0] if found_backends else "pytorch"
            self.backend_combo.addItem(only_backend)
            self.backend_combo.setCurrentText(only_backend)
            self.backend_combo.setEnabled(False)
            self.backend_combo.setToolTip(
                f"This model only has variant(s) available for the {only_backend} execution backend."
            )
            effective_backend = only_backend
        else:
            # Multi-backend model: Keep 'auto' and available backends enabled
            options = ["auto"] + sorted(found_backends)
            self.backend_combo.addItems(options)
            target_b = preferred_backend if preferred_backend in options else "auto"
            self.backend_combo.setCurrentText(target_b)
            self.backend_combo.setEnabled(True)
            setup_field_tooltip(self.backend_combo, ModelConfig.model_fields["backend"])
            effective_backend = target_b

        self.backend_combo.blockSignals(False)

        # 3. Filter variants matching the effective backend
        if effective_backend == "auto":
            filtered_variants = list(variants)
        else:
            filtered_variants = [
                v
                for v in variants
                if (v.backend.value if hasattr(v.backend, "value") else str(v.backend)).lower()
                == effective_backend.lower()
            ]

        # 4. Check for named variants (variant_id is not None)
        named_variants = [v for v in filtered_variants if v.variant_id]

        self.variant_combo.blockSignals(True)
        self.variant_combo.clear()

        if not named_variants or len(filtered_variants) <= 1:
            # Single variant or no named variants: Lock to (Default) and disable
            self.variant_combo.addItem("(Default)", userData=None)
            self.variant_combo.setCurrentIndex(0)
            self.variant_combo.setEnabled(False)

            if len(found_backends) > 1 and not named_variants:
                self.variant_combo.setToolTip(
                    "This model's variants are effectively selected via the Execution Backend setting above because there is only one variant for each backend available."
                )
            elif len(filtered_variants) == 1 and filtered_variants[0].variant_id:
                v_obj = filtered_variants[0]
                b_str = (v_obj.backend.value if hasattr(v_obj.backend, "value") else str(v_obj.backend)).lower()
                self.variant_combo.clear()
                self.variant_combo.addItem(f"{v_obj.variant_id} [{b_str}] (Default)", userData=None)
                self.variant_combo.setToolTip(f"No alternative variants available for the {effective_backend} backend.")
            else:
                self.variant_combo.setToolTip("This model does not define alternative named variants.")
        else:
            # Multiple named variants exist: List each once, marking index 0 as (Default)
            self.variant_combo.setEnabled(True)
            setup_field_tooltip(self.variant_combo, ModelConfig.model_fields["variant_id"])

            for idx, v in enumerate(named_variants):
                b_str = (v.backend.value if hasattr(v.backend, "value") else str(v.backend)).lower()
                disp_text = f"{v.variant_id} [{b_str}] (Default)" if idx == 0 else f"{v.variant_id} [{b_str}]"
                # Index 0 maps to None so TOML stays minimal unless an explicit non-default is chosen
                user_val = None if idx == 0 else v.variant_id
                self.variant_combo.addItem(disp_text, userData=user_val)
                if v.description:
                    self.variant_combo.setItemData(idx, v.description, Qt.ItemDataRole.ToolTipRole)

            # Restore preferred selection
            match_idx = 0
            if preferred_variant is not None:
                for i in range(self.variant_combo.count()):
                    data = self.variant_combo.itemData(i)
                    # Match explicit userData or the raw variant_id of item 0
                    if data == preferred_variant or (i == 0 and named_variants[0].variant_id == preferred_variant):
                        match_idx = i
                        break
            self.variant_combo.setCurrentIndex(match_idx)

        self.variant_combo.blockSignals(False)

        # 5. Refresh Model Details Box
        self._update_model_details(model_id)

    @staticmethod
    def _make_detail_label(text: str) -> QLabel:
        """Create a clean, subtle key label for the details form."""
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #8a9ba5; font-weight: 500;")
        return lbl

    def _make_wrapping_label(self) -> QLabel:
        """Create a selectable text label that expands vertically when wrapped."""
        lbl = QLabel(self.info_card)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        return lbl

    def _update_model_details(self, model_id: str) -> None:
        """Render model details including selected variant description and clickable HF repo link."""
        try:
            from enum import Enum

            import vibe
            from vibe.registry import model_registry

            model_registry.ensure_discovered()
            plugin_cls = model_registry.get(model_id)
            desc = plugin_cls.describe()

            self.info_name_lbl.setText(
                f"{desc.identity.display_name} <span style='color: #8a9ba5;'>({desc.family_name})</span>"
            )
            self.info_desc_lbl.setText(desc.identity.description or "No description available.")

            # Resolve active variant for this model
            selected_var_data = self.variant_combo.currentData()
            backend_val = self.backend_combo.currentText().strip()
            device_val = self.device_combo.currentText().strip()

            active_v = vibe.resolve_variant(
                plugin_cls,
                backend=backend_val if backend_val != "auto" else None,
                variant=selected_var_data,
                device=device_val,
            )

            # Check if this model defines explicit named variants
            has_named_variants = any(bool(v.variant_id) for v in desc.variants)

            if not has_named_variants:
                # Models without named variants (e.g. WD taggers, PixAI)
                desc_text = ""
                for v in desc.variants:
                    if v.description:
                        desc_text = f" - {v.description}"
                        break
                self.info_variant_lbl.setText(f"(Default){desc_text}")
            else:
                is_default = selected_var_data is None
                if active_v and active_v.variant_id:
                    v_name = active_v.variant_id
                    b_name = (
                        active_v.backend.value if hasattr(active_v.backend, "value") else str(active_v.backend)
                    ).lower()
                    default_tag = " (Default)" if is_default else ""
                    v_desc = f" - {active_v.description}" if active_v.description else ""
                    self.info_variant_lbl.setText(
                        f"<b>{v_name}</b> <span style='color: #8a9ba5;'>[{b_name}]</span>{default_tag}{v_desc}"
                    )
                else:
                    self.info_variant_lbl.setText("(Default)")

            cats = [c.value if isinstance(c, Enum) else str(c) for c in desc.output.categories]
            self.info_cats_lbl.setText(", ".join(cats) if cats else "(None)")

            # Resolve effective repository without extra text or fluff
            repo = self._get_effective_repo_id(model_id, selected_var_data)
            if repo:
                self.info_repo_lbl.setText(f'<a href="https://huggingface.co/{repo}">{repo}</a>')
            else:
                self.info_repo_lbl.setText("<span style='color: #888;'>(None / Custom)</span>")

            self.info_card.setVisible(True)
        except Exception:
            self.info_name_lbl.setText(f"{model_id}")
            self.info_desc_lbl.setText("Custom or uncataloged model ID.")
            self.info_variant_lbl.setText("(None)")
            self.info_cats_lbl.setText("(Unknown)")
            self.info_repo_lbl.setText("<span style='color: #888;'>(Custom)</span>")
            self.info_card.setVisible(True)

        # Force layout reflow so multi-line wrapped text is never clipped
        self.info_desc_lbl.updateGeometry()
        self.info_variant_lbl.updateGeometry()
        self.info_cats_lbl.updateGeometry()
        self.info_card.updateGeometry()
        self.artifacts_card.updateGeometry()

        # Refresh local availability and artifact table
        self._update_availability(model_id)

    def _on_toggle_files(self) -> None:
        """Toggle the collapsible artifact file inspection table."""
        is_visible = not self.files_container.isVisible()
        self.files_container.setVisible(is_visible)
        self.toggle_files_btn.setText("Hide Files" if is_visible else "Show Files")

    def _update_availability(self, model_id: str) -> None:
        """Inspect local availability offline and populate the 4-column artifact table."""
        if not model_id:
            self.info_avail_lbl.setText("<span style='color: #888;'>No model selected</span>")
            self.files_table.setRowCount(0)
            self.mismatch_card.setVisible(False)
            return

        source = self.source_edit.text().strip() or None
        variant_id = self.variant_combo.currentData()
        backend_val = self.backend_combo.currentText().strip()
        device_val = self.device_combo.currentText().strip()

        try:
            import vibe

            avail = vibe.check_availability(
                model_id,
                backend=backend_val if backend_val != "auto" else None,
                device=device_val,
                source=source,
                variant=str(variant_id) if variant_id else None,
            )
        except Exception:
            avail = None

        if not avail or not avail.variants:
            self.info_avail_lbl.setText("<span style='color: #888;'>Unknown model or custom plugin</span>")
            self.files_table.setRowCount(0)
            self.download_summary_lbl.setText("")
            self.check_size_btn.setEnabled(False)
            self.mismatch_card.setVisible(False)
            return

        target_var = avail.variants[0]
        # Strictly show and count required artifacts; ignore optional files
        artifacts = [a for a in target_var.artifacts if getattr(a, "required", True)]

        total_req = len(artifacts)
        cached_req = sum(1 for a in artifacts if a.is_available)
        is_fully_cached = cached_req == total_req

        # 1. Update 1-Line Status Label and Local Source Notice
        is_local_source = bool(source and source.startswith("local:"))

        if is_fully_cached:
            if is_local_source:
                self.info_avail_lbl.setText(
                    f"<span style='color: #34d399;'>● Ready in local folder</span> (All {cached_req} files present)"
                )
                self.info_avail_lbl.setToolTip("All required model files are present in your local folder.")
            else:
                self.info_avail_lbl.setText(
                    f"<span style='color: #34d399;'>● Ready locally</span> (All {cached_req} files cached)"
                )
                self.info_avail_lbl.setToolTip("All required model files are present on disk.")
        else:
            missing_count = total_req - cached_req
            if is_local_source:
                self.info_avail_lbl.setText(
                    f"<span style='color: #fbbf24;'>○ Incomplete local folder</span> ({missing_count} required file{'s' if missing_count != 1 else ''} missing)"
                )
                self.info_avail_lbl.setToolTip(
                    f"Files are missing from your local folder:\n{source[6:]}\n"
                    "Because you are using a local folder, HyVis will not download missing files from the internet."
                )
            else:
                self.info_avail_lbl.setText(
                    f"<span style='color: #fbbf24;'>○ Needs download</span> ({missing_count} file{'s' if missing_count != 1 else ''} will be downloaded)"
                )
                self.info_avail_lbl.setToolTip(
                    "Missing files will be downloaded automatically from Hugging Face on first inference run."
                )

        # 2. Populate 4-Column Table: File | Status | Local Size | Remote Size
        self.files_table.blockSignals(True)
        self.files_table.setRowCount(len(artifacts))

        repo_id = self._get_effective_repo_id(model_id, target_var.variant_id)
        remote_sizes = self._remote_sizes_cache.get(repo_id, {})
        total_missing_bytes = 0
        has_remote_estimates = False
        mismatched_files: list[tuple[str, int, int]] = []

        for row, art in enumerate(artifacts):
            # Col 0: File Name
            f_item = QTableWidgetItem(art.name)
            if art.is_available and art.path:
                f_item.setToolTip(f"Located at:\n{art.path}")
            elif is_local_source:
                f_item.setToolTip(f"Missing from local directory: {source[6:]}")
            else:
                f_item.setToolTip(f"Missing from local cache.\nWill be downloaded from HF: {repo_id}")
            self.files_table.setItem(row, 0, f_item)

            # Col 1: Status
            if art.is_available:
                status_item = QTableWidgetItem("● Cached")
                status_item.setForeground(QColor(COLOR_STATUS_READY))
                if art.path:
                    status_item.setToolTip(f"Located at:\n{art.path}")
            else:
                status_item = QTableWidgetItem("○ Missing")
                status_item.setForeground(QColor(COLOR_STATUS_WARNING))
                status_item.setToolTip("Not found in local cache or folder.")
            self.files_table.setItem(row, 1, status_item)

            # Col 2: Local Size
            local_bytes: int | None = None
            if art.is_available and art.path and Path(art.path).is_file():
                local_bytes = Path(art.path).stat().st_size
                local_str = _format_bytes(local_bytes)
            else:
                local_str = "—"
            local_item = QTableWidgetItem(local_str)
            local_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            # Col 3: Remote Size
            if art.name in remote_sizes:
                rem_bytes = remote_sizes[art.name]
                rem_str = _format_bytes(rem_bytes)
                has_remote_estimates = True
                if not art.is_available:
                    total_missing_bytes += rem_bytes

                # Compare local bytes against expected remote bytes (tolerate minor header diffs <= 1KB)
                if local_bytes is not None and abs(local_bytes - rem_bytes) > 1024:
                    mismatched_files.append((art.name, local_bytes, rem_bytes))
                    local_item.setText(f"{local_str} ⚠")
                    local_item.setForeground(QColor(COLOR_STATUS_WARNING))
                    local_item.setToolTip(f"Size differs from expected repository ({rem_str})")
            else:
                rem_str = "—"

            remote_item = QTableWidgetItem(rem_str)
            remote_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.files_table.setItem(row, 2, local_item)
            self.files_table.setItem(row, 3, remote_item)

        self.files_table.blockSignals(False)

        # 3. File Size Mismatch Warning Card
        if mismatched_files:
            lines = [
                "<b>File size mismatch detected</b><br>The following files differ in size from the expected repository:"
            ]
            for fname, loc_sz, rem_sz in mismatched_files:
                lines.append(f"• <b>{fname}</b>: {_format_bytes(loc_sz)} local vs {_format_bytes(rem_sz)} expected")
            lines.append(
                "<span style='color: #aaa;'>This may indicate that the file in your source path is a different version, precision format, or checkpoint. If this is intentional, you can safely ignore this.</span>"
            )
            self.mismatch_lbl.setText("<br>".join(lines))
            self.mismatch_card.setVisible(True)
        else:
            self.mismatch_card.setVisible(False)

        # 4. Action Button & Summary
        if not repo_id:
            self.check_size_btn.setEnabled(False)
            self.check_size_btn.setToolTip("No Hugging Face repository defined for this model.")
            self.download_summary_lbl.setText("No remote repository.")
        else:
            self.check_size_btn.setEnabled(True)
            self.check_size_btn.setToolTip(f"Query Hugging Face API to check sizes from '{repo_id}'.")
            if is_fully_cached:
                if has_remote_estimates:
                    self.download_summary_lbl.setText("All files cached (sizes verified).")
                else:
                    self.download_summary_lbl.setText("All files cached.")
            else:
                if has_remote_estimates:
                    self.download_summary_lbl.setText(f"Total to download: ~{_format_bytes(total_missing_bytes)}")
                else:
                    missing_cnt = total_req - cached_req
                    self.download_summary_lbl.setText(f"{missing_cnt} file(s) missing.")

    def _get_effective_repo_id(self, model_id: str, variant_id: str | None = None) -> str:
        """Resolve effective Hugging Face repo ID for size checking and metadata display."""
        src = self.source_edit.text().strip()
        # If user entered an online custom repo (e.g. username/repo), query that custom repo
        if src and not src.startswith("local:") and not any(src.startswith(p) for p in ("/", "./", "../", "~", "\\")):
            return src

        # Otherwise (empty or local: folder), return the expected variant repo from vibe
        try:
            import vibe
            from vibe.registry import model_registry

            model_registry.ensure_discovered()
            plugin_cls = model_registry.get(model_id)
            desc = plugin_cls.describe()

            backend_val = self.backend_combo.currentText().strip()
            device_val = self.device_combo.currentText().strip()

            active_v = vibe.resolve_variant(
                plugin_cls,
                backend=backend_val if backend_val != "auto" else None,
                variant=variant_id,
                device=device_val,
            )
            return active_v.repo_id or desc.default_repo_id or ""
        except Exception:
            return ""

    def _on_check_download_size(self) -> None:
        """Trigger background query to Hugging Face API for file sizes."""
        model_id = self.model_id_combo.currentText().strip()
        variant_id = self.variant_combo.currentData()
        repo_id = self._get_effective_repo_id(model_id, variant_id)

        if not repo_id:
            return

        # Clear focus before disabling so Qt doesn't jump focus or trigger scrolls
        self.check_size_btn.clearFocus()
        self.check_size_btn.setEnabled(False)
        self.check_size_btn.setText("Checking...")

        # Store on self to prevent Python GC from deallocating the worker/signals mid-execution (SIGSEGV fix)
        self._active_size_worker = _HfSizeWorker(repo_id)
        self._active_size_worker.signals.success.connect(lambda sizes: self._on_hf_sizes_loaded(repo_id, sizes))
        self._active_size_worker.signals.error.connect(self._on_hf_sizes_error)
        QThreadPool.globalInstance().start(self._active_size_worker)

    def _on_check_download_size(self) -> None:
        """Trigger background query to Hugging Face API for file sizes."""
        model_id = self.model_id_combo.currentText().strip()
        variant_id = self.variant_combo.currentData()
        repo_id = self._get_effective_repo_id(model_id, variant_id)

        if not repo_id:
            return

        self.check_size_btn.clearFocus()
        self.check_size_btn.setEnabled(False)
        self.check_size_btn.setText("Checking...")

        self._active_size_worker = _HfSizeWorker(repo_id)
        self._active_size_worker.signals.success.connect(lambda sizes: self._on_hf_sizes_loaded(repo_id, sizes))
        self._active_size_worker.signals.error.connect(self._on_hf_sizes_error)
        QThreadPool.globalInstance().start(self._active_size_worker)

    def _on_hf_sizes_loaded(self, repo_id: str, sizes: dict[str, int]) -> None:
        self._active_size_worker = None
        self._remote_sizes_cache[repo_id] = sizes
        self.check_size_btn.setText("Check Remote Sizes")
        self.check_size_btn.setEnabled(True)
        model_id = self.model_id_combo.currentText().strip()
        self._update_availability(model_id)

    def _on_hf_sizes_error(self, err_msg: str) -> None:
        self._active_size_worker = None
        self.check_size_btn.setText("Check Remote Sizes")
        self.check_size_btn.setEnabled(True)
        self.download_summary_lbl.setText(f"<span style='color: #f85149;'>Failed to check size: {err_msg[:60]}</span>")

    def _update_device_constraints(self) -> None:
        """Enforce batch_size=1 when hardware device is CPU."""
        dev = self.device_combo.currentText().strip().lower()
        is_cpu = dev == "cpu"
        m_fields = ModelConfig.model_fields

        if is_cpu:
            self.batch_spin.blockSignals(True)
            self.batch_spin.setValue(1)
            self.batch_spin.blockSignals(False)
            self.batch_spin.setEnabled(False)
            self.batch_spin.setToolTip("Batch size is fixed to 1 when hardware device is CPU.")
        else:
            self.batch_spin.setEnabled(True)
            setup_field_tooltip(self.batch_spin, m_fields["batch_size"])

    def _update_backend_constraints(self) -> None:
        """Disable precision selection when backend is ONNX."""
        backend = self.backend_combo.currentText().strip().lower()
        is_onnx = backend == "onnx"
        m_fields = ModelConfig.model_fields

        if is_onnx:
            self.precision_combo.setEnabled(False)
            self.precision_combo.setToolTip("Precision setting has no effect on ONNX models.")
        else:
            self.precision_combo.setEnabled(True)
            setup_field_tooltip(self.precision_combo, m_fields["precision"])

    def _on_model_id_changed(self, text: str) -> None:
        if self._is_loading_ui:
            return

        # If backend was disabled (forcibly locked by a single-backend model),
        # reset preference to 'auto' so multi-backend models default to auto
        if not self.backend_combo.isEnabled():
            current_backend = "auto"
        else:
            current_backend = self.backend_combo.currentText().strip()

        self._update_backend_and_variants(text.strip(), preferred_backend=current_backend)
        self._update_backend_constraints()
        self._on_field_changed()

    def _on_backend_changed(self, text: str) -> None:
        del text
        if self._is_loading_ui:
            return
        model_id = self.model_id_combo.currentText().strip()
        current_variant = self.variant_combo.currentData()
        self._update_backend_and_variants(
            model_id,
            preferred_backend=self.backend_combo.currentText().strip(),
            preferred_variant=current_variant,
        )
        self._update_backend_constraints()
        self._on_field_changed()

    def _on_variant_changed(self, idx: int) -> None:
        del idx
        if self._is_loading_ui:
            return
        model_id = self.model_id_combo.currentText().strip()
        self._update_model_details(model_id)
        self._on_field_changed()

    def _on_device_changed(self, text: str) -> None:
        del text
        if self._is_loading_ui:
            return
        self._update_device_constraints()
        self._on_field_changed()

    def load_config(self, cfg: AppConfig) -> None:
        self._is_loading_ui = True
        try:
            # 1. Reset current selection index so the first model cleanly loads
            self._current_index = -1

            # 2. Extract models while strictly preserving explicitly declared overrides
            cleaned_models: list[dict[str, Any]] = []
            for m in cfg.inference.models:
                m_dict = m.model_dump(mode="json")
                if m.output_filter is not None:
                    m_dict["output_filter"] = {
                        k: v for k, v in m_dict["output_filter"].items() if k in m.output_filter.model_fields_set
                    }
                else:
                    m_dict["output_filter"] = None
                cleaned_models.append(m_dict)

            self._models_data = cleaned_models

            # 3. Repopulate sidebar model list under signal block
            self.model_list.blockSignals(True)
            self.model_list.clear()
            for m in self._models_data:
                self.model_list.addItem(QListWidgetItem(str(m.get("model_id", "unnamed"))))
            self.model_list.blockSignals(False)

            # 4. Load the first model
            if self._models_data:
                self.model_list.blockSignals(True)
                self.model_list.setCurrentRow(0)
                self.model_list.blockSignals(False)
                self._load_model_to_form(0)
        finally:
            self._is_loading_ui = False

    def apply_to_dict(self, data: dict[str, Any]) -> None:
        self._save_form_to_model(self._current_index)
        inf_dict: dict[str, Any] = data.setdefault("inference", {})
        inf_dict["models"] = list(self._models_data)

    def _on_model_selected(self, row: int) -> None:
        if self._is_loading_ui or row < 0 or row >= len(self._models_data):
            return
        if self._current_index == row:
            return

        # Save previous model form state before switching
        if 0 <= self._current_index < len(self._models_data):
            self._save_form_to_model(self._current_index)

        self._load_model_to_form(row)

    def _load_model_to_form(self, index: int) -> None:
        if index < 0 or index >= len(self._models_data):
            return

        was_loading = self._is_loading_ui
        self._is_loading_ui = True
        try:
            self._current_index = index
            m = self._models_data[index]

            model_id = str(m.get("model_id", ""))
            self.model_id_combo.setCurrentText(model_id)

            backend_val = m.get("backend")
            variant_val = m.get("variant_id")
            self._update_backend_and_variants(
                model_id,
                preferred_backend=str(backend_val) if backend_val else "auto",
                preferred_variant=str(variant_val) if variant_val else None,
            )

            self.source_edit.setText(str(m.get("source") or ""))
            self.device_combo.setCurrentText(str(m.get("device") or "auto"))
            self.precision_combo.setCurrentText(str(m.get("precision") or "auto"))
            self.batch_spin.setValue(int(m.get("batch_size", 1)))

            # Enforce dynamic reactive constraints
            self._update_device_constraints()
            self._update_backend_constraints()

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

            # Evaluate availability AFTER source_edit and variant_combo are loaded
            self._update_availability(model_id)
        finally:
            self._is_loading_ui = was_loading

    def _save_form_to_model(self, index: int) -> None:
        if index < 0 or index >= len(self._models_data):
            return

        m = self._models_data[index]
        model_id = self.model_id_combo.currentText().strip()
        m["model_id"] = model_id

        # If backend is disabled (single-backend model) or set to 'auto', keep m["backend"] as None
        if not self.backend_combo.isEnabled():
            m["backend"] = None
        else:
            backend_text = self.backend_combo.currentText().strip().lower()
            m["backend"] = None if backend_text in ("auto", "") else backend_text

        m["variant_id"] = self.variant_combo.currentData()
        m["source"] = self.source_edit.text().strip() or None

        device_val = self.device_combo.currentText().strip()
        m["device"] = device_val

        m["precision"] = self.precision_combo.currentText().strip()

        if device_val.lower() == "cpu":
            m["batch_size"] = 1
        else:
            m["batch_size"] = self.batch_spin.value()

        if self.svc_card.isChecked():
            svcs = self.model_services_editor.get_items()
            m["output_tag_services"] = {"keys": svcs}
        else:
            m["output_tag_services"] = None

        item = self.model_list.item(index)
        if item and model_id:
            item.setText(model_id)

    def _on_add_model(self) -> None:
        # Save current active form state first
        if 0 <= self._current_index < len(self._models_data):
            self._save_form_to_model(self._current_index)

        new_model = {
            "model_id": "wd-swinv2-v3",
            "variant_id": None,
            "source": None,
            "backend": None,
            "device": "auto",
            "precision": "auto",
            "batch_size": 1,
            "output_tag_services": None,
        }
        self._models_data.append(new_model)

        self._is_loading_ui = True
        new_row = len(self._models_data) - 1
        try:
            self.model_list.blockSignals(True)
            self.model_list.addItem(QListWidgetItem("wd-swinv2-v3"))
            self.model_list.setCurrentRow(new_row)
            self.model_list.blockSignals(False)

            self._load_model_to_form(new_row)
        finally:
            self._is_loading_ui = False

        self.changed.emit()

    def _on_remove_model(self) -> None:
        row = self.model_list.currentRow()
        if row < 0 or len(self._models_data) <= 1:
            return  # Must maintain at least one model

        self._is_loading_ui = True
        try:
            # 1. Remove model from internal data list without saving the form
            self._models_data.pop(row)

            # 2. Block list widget signals while adjusting the UI row
            self.model_list.blockSignals(True)
            self.model_list.takeItem(row)

            # 3. Select the next available model
            next_row = min(row, len(self._models_data) - 1)
            self.model_list.setCurrentRow(next_row)
            self.model_list.blockSignals(False)

            # 4. Safely load the newly selected model into the form
            self._load_model_to_form(next_row)
        finally:
            self._is_loading_ui = False

        self.changed.emit()

    def _on_svc_card_toggled(self, checked: bool) -> None:
        del checked
        self._on_field_changed()

    def _on_field_changed(self) -> None:
        if self._is_loading_ui:
            return
        self._save_form_to_model(self._current_index)
        model_id = self.model_id_combo.currentText().strip()
        self._update_availability(model_id)
        self.changed.emit()

    def _on_browse_source(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Local Model Directory")
        if folder:
            # Automatically prepend 'local:' so vibe treats it as offline local storage
            formatted_source = f"local:{folder}"
            self.source_edit.setText(formatted_source)
            self._on_field_changed()

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
