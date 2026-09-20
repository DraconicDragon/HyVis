"""
hydrus_page.py — Page for Hydrus API connection, search queries, and tag service targets.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hyvis.config import (
    AddTagConfig,
    AppConfig,
    HydrusConfig,
    PageQueryConfig,
    PreviewConfig,
    RemoveTagConfig,
    TagQueryConfig,
)
from hyvis.gui.widgets import TagServiceListEditor, bind_field_metadata, setup_field_tooltip


class HydrusPage(QWidget):
    """Configuration page for [hydrus] connection and file targeting settings."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._available_writable_services: dict[str, str] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        h_fields = HydrusConfig.model_fields
        tq_fields = TagQueryConfig.model_fields
        pq_fields = PageQueryConfig.model_fields
        prev_fields = PreviewConfig.model_fields
        add_fields = AddTagConfig.model_fields
        rem_fields = RemoveTagConfig.model_fields

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(16)

        # 1. API Connection
        conn_group = QGroupBox("Hydrus API Connection", container)
        conn_layout = QFormLayout(conn_group)
        conn_layout.setSpacing(8)

        self.api_url_edit = QLineEdit(self)
        bind_field_metadata(self.api_url_edit, h_fields["api_url"])
        self.api_url_edit.textChanged.connect(lambda _: self.changed.emit())
        conn_layout.addRow(f"{h_fields['api_url'].title}:", self.api_url_edit)

        self.api_key_edit = QLineEdit(self)
        self.api_key_edit.setPlaceholderText("Paste your Hydrus API key here")
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        setup_field_tooltip(self.api_key_edit, h_fields["api_key"])
        self.api_key_edit.textChanged.connect(lambda _: self.changed.emit())
        conn_layout.addRow(f"{h_fields['api_key'].title}:", self.api_key_edit)

        self.no_wait_chk = QCheckBox(h_fields["no_wait"].title, self)
        setup_field_tooltip(self.no_wait_chk, h_fields["no_wait"])
        self.no_wait_chk.toggled.connect(lambda _: self.changed.emit())
        conn_layout.addRow("", self.no_wait_chk)

        layout.addWidget(conn_group)

        # 2. Output Tag Services (Global)
        output_group = QGroupBox(h_fields["output_tag_services"].title, container)
        setup_field_tooltip(output_group, h_fields["output_tag_services"])
        output_layout = QVBoxLayout(output_group)

        self.output_services_editor = TagServiceListEditor(writable_only=True, parent=self)
        self.output_services_editor.changed.connect(self.changed.emit)
        output_layout.addWidget(self.output_services_editor)

        layout.addWidget(output_group)

        # 3. Tag Queries
        tag_q_group = QGroupBox(h_fields["tag_queries"].title, container)
        setup_field_tooltip(tag_q_group, h_fields["tag_queries"])
        tag_q_layout = QVBoxLayout(tag_q_group)

        self.tag_q_table = QTableWidget(0, 2, self)
        self.tag_q_table.setHorizontalHeaderLabels(
            [f"{tq_fields['tags'].title} (comma-separated)", f"{tq_fields['tag_service_keys'].title} (empty = all)"]
        )
        self.tag_q_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tag_q_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tag_q_table.itemChanged.connect(lambda _: self.changed.emit())
        tag_q_layout.addWidget(self.tag_q_table)

        tag_btn_layout = QHBoxLayout()
        self.add_tag_q_btn = QPushButton("+ Add Tag Query", self)
        self.add_tag_q_btn.clicked.connect(self._on_add_tag_q)
        tag_btn_layout.addWidget(self.add_tag_q_btn)

        self.remove_tag_q_btn = QPushButton("- Remove Selected", self)
        self.remove_tag_q_btn.clicked.connect(self._on_remove_tag_q)
        tag_btn_layout.addWidget(self.remove_tag_q_btn)
        tag_btn_layout.addStretch()
        tag_q_layout.addLayout(tag_btn_layout)

        layout.addWidget(tag_q_group)

        # 4. Page Queries
        page_q_group = QGroupBox(h_fields["page_queries"].title, container)
        setup_field_tooltip(page_q_group, h_fields["page_queries"])
        page_q_layout = QVBoxLayout(page_q_group)

        self.page_q_table = QTableWidget(0, 2, self)
        self.page_q_table.setHorizontalHeaderLabels([pq_fields["name"].title, f"{pq_fields['index'].title} (Optional)"])
        self.page_q_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.page_q_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.page_q_table.itemChanged.connect(lambda _: self.changed.emit())
        page_q_layout.addWidget(self.page_q_table)

        page_btn_layout = QHBoxLayout()
        self.add_page_q_btn = QPushButton("+ Add Page Query", self)
        self.add_page_q_btn.clicked.connect(self._on_add_page_q)
        page_btn_layout.addWidget(self.add_page_q_btn)

        self.remove_page_q_btn = QPushButton("- Remove Selected", self)
        self.remove_page_q_btn.clicked.connect(self._on_remove_page_q)
        page_btn_layout.addWidget(self.remove_page_q_btn)
        page_btn_layout.addStretch()
        page_q_layout.addLayout(page_btn_layout)

        layout.addWidget(page_q_group)

        # 5. Additional Post-Run Tags (Checkable Group)
        self.add_tags_group = QGroupBox(h_fields["add_tags"].title, container)
        self.add_tags_group.setCheckable(True)
        self.add_tags_group.setChecked(False)
        setup_field_tooltip(self.add_tags_group, h_fields["add_tags"])
        self.add_tags_group.toggled.connect(lambda _: self.changed.emit())
        add_layout = QFormLayout(self.add_tags_group)
        add_layout.setSpacing(8)

        self.add_tags_edit = QLineEdit(self)
        bind_field_metadata(self.add_tags_edit, add_fields["tags"])
        self.add_tags_edit.textChanged.connect(lambda _: self.changed.emit())
        add_layout.addRow(f"{add_fields['tags'].title}:", self.add_tags_edit)

        self.add_services_editor = TagServiceListEditor(writable_only=True, parent=self)
        setup_field_tooltip(self.add_services_editor, add_fields["tag_service_keys"])
        self.add_services_editor.changed.connect(self.changed.emit)
        add_layout.addRow(f"{add_fields['tag_service_keys'].title}:", self.add_services_editor)

        layout.addWidget(self.add_tags_group)

        # 6. Cleanup Tags (Checkable Group)
        self.rem_tags_group = QGroupBox(h_fields["remove_tags"].title, container)
        self.rem_tags_group.setCheckable(True)
        self.rem_tags_group.setChecked(False)
        setup_field_tooltip(self.rem_tags_group, h_fields["remove_tags"])
        self.rem_tags_group.toggled.connect(lambda _: self.changed.emit())
        rem_layout = QFormLayout(self.rem_tags_group)
        rem_layout.setSpacing(8)

        self.rem_tags_edit = QLineEdit(self)
        bind_field_metadata(self.rem_tags_edit, rem_fields["tags"])
        self.rem_tags_edit.textChanged.connect(lambda _: self.changed.emit())
        rem_layout.addRow(f"{rem_fields['tags'].title}:", self.rem_tags_edit)

        self.rem_services_editor = TagServiceListEditor(writable_only=True, parent=self)
        setup_field_tooltip(self.rem_services_editor, rem_fields["tag_service_keys"])
        self.rem_services_editor.changed.connect(self.changed.emit)
        rem_layout.addRow(f"{rem_fields['tag_service_keys'].title}:", self.rem_services_editor)

        layout.addWidget(self.rem_tags_group)

        # 7. Preview Settings (Checkable Group)
        self.prev_group = QGroupBox(h_fields["preview"].title, container)
        self.prev_group.setCheckable(True)
        self.prev_group.setChecked(False)
        setup_field_tooltip(self.prev_group, h_fields["preview"])
        self.prev_group.toggled.connect(lambda _: self.changed.emit())
        prev_layout = QFormLayout(self.prev_group)
        prev_layout.setSpacing(8)

        self.prev_name_edit = QLineEdit(self)
        bind_field_metadata(self.prev_name_edit, prev_fields["page_name"])
        self.prev_name_edit.textChanged.connect(lambda _: self.changed.emit())
        prev_layout.addRow(f"{prev_fields['page_name'].title}:", self.prev_name_edit)

        self.prev_index_spin = QSpinBox(self)
        self.prev_index_spin.setRange(0, 99)
        self.prev_index_spin.setSpecialValueText("None (Auto)")
        setup_field_tooltip(self.prev_index_spin, prev_fields["page_index"])
        self.prev_index_spin.valueChanged.connect(lambda _: self.changed.emit())
        prev_layout.addRow(f"{prev_fields['page_index'].title}:", self.prev_index_spin)

        self.prev_rej_edit = QLineEdit(self)
        bind_field_metadata(self.prev_rej_edit, prev_fields["rejected_page_name"])
        self.prev_rej_edit.textChanged.connect(lambda _: self.changed.emit())
        prev_layout.addRow(f"{prev_fields['rejected_page_name'].title}:", self.prev_rej_edit)

        self.prev_rej_index_spin = QSpinBox(self)
        self.prev_rej_index_spin.setRange(0, 99)
        self.prev_rej_index_spin.setSpecialValueText("None (Auto)")
        setup_field_tooltip(self.prev_rej_index_spin, prev_fields["rejected_page_index"])
        self.prev_rej_index_spin.valueChanged.connect(lambda _: self.changed.emit())
        prev_layout.addRow(f"{prev_fields['rejected_page_index'].title}:", self.prev_rej_index_spin)

        layout.addWidget(self.prev_group)

        # Set scroll root
        scroll.setWidget(container)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def update_services(self, all_tags: dict[str, str], writable_tags: dict[str, str]) -> None:
        """Update active services across all tag service editors on this page."""
        self._available_writable_services = dict(writable_tags)
        self.output_services_editor.set_available_services(writable_tags)
        self.add_services_editor.set_available_services(writable_tags)
        self.rem_services_editor.set_available_services(writable_tags)

    def load_config(self, cfg: AppConfig) -> None:
        """Populate widgets from AppConfig without emitting changed signals."""
        self.blockSignals(True)
        h = cfg.hydrus

        self.api_url_edit.setText(h.api_url)
        self.api_key_edit.setText(h.api_key)
        self.no_wait_chk.setChecked(h.no_wait)

        self.output_services_editor.set_items(h.output_tag_services.keys)

        # Tag Queries
        self.tag_q_table.blockSignals(True)
        self.tag_q_table.setRowCount(0)
        for row, q in enumerate(h.tag_queries):
            self.tag_q_table.insertRow(row)
            tags_str = ", ".join(str(t) for t in q.tags)
            keys_str = ", ".join(q.tag_service_keys)
            self.tag_q_table.setItem(row, 0, QTableWidgetItem(tags_str))
            self.tag_q_table.setItem(row, 1, QTableWidgetItem(keys_str))
        self.tag_q_table.blockSignals(False)

        # Page Queries
        self.page_q_table.blockSignals(True)
        self.page_q_table.setRowCount(0)
        for row, pq in enumerate(h.page_queries):
            self.page_q_table.insertRow(row)
            self.page_q_table.setItem(row, 0, QTableWidgetItem(pq.name))
            idx_str = str(pq.index) if pq.index is not None else ""
            self.page_q_table.setItem(row, 1, QTableWidgetItem(idx_str))
        self.page_q_table.blockSignals(False)

        # Add Tags
        if h.add_tags:
            self.add_tags_group.blockSignals(True)
            self.add_tags_group.setChecked(True)
            self.add_tags_group.blockSignals(False)
            self.add_tags_edit.setText(", ".join(h.add_tags.tags))
            self.add_services_editor.set_items(h.add_tags.tag_service_keys)
        else:
            self.add_tags_group.blockSignals(True)
            self.add_tags_group.setChecked(False)
            self.add_tags_group.blockSignals(False)
            self.add_tags_edit.clear()
            self.add_services_editor.set_items([])

        # Remove Tags
        if h.remove_tags:
            self.rem_tags_group.blockSignals(True)
            self.rem_tags_group.setChecked(True)
            self.rem_tags_group.blockSignals(False)
            self.rem_tags_edit.setText(", ".join(h.remove_tags.tags))
            self.rem_services_editor.set_items(h.remove_tags.tag_service_keys)
        else:
            self.rem_tags_group.blockSignals(True)
            self.rem_tags_group.setChecked(False)
            self.rem_tags_group.blockSignals(False)
            self.rem_tags_edit.clear()
            self.rem_services_editor.set_items([])

        # Preview
        if h.preview:
            self.prev_group.blockSignals(True)
            self.prev_group.setChecked(True)
            self.prev_group.blockSignals(False)
            self.prev_name_edit.setText(h.preview.page_name or "")
            self.prev_index_spin.setValue(h.preview.page_index if h.preview.page_index is not None else 0)
            self.prev_rej_edit.setText(h.preview.rejected_page_name or "")
            self.prev_rej_index_spin.setValue(
                h.preview.rejected_page_index if h.preview.rejected_page_index is not None else 0
            )
        else:
            self.prev_group.blockSignals(True)
            self.prev_group.setChecked(False)
            self.prev_group.blockSignals(False)
            self.prev_name_edit.clear()
            self.prev_index_spin.setValue(0)
            self.prev_rej_edit.clear()
            self.prev_rej_index_spin.setValue(0)

        self.blockSignals(False)

    def apply_to_dict(self, data: dict[str, Any]) -> None:
        """Serialize widget states into the raw dictionary representation for AppConfig."""
        hydrus_dict: dict[str, Any] = data.setdefault("hydrus", {})

        hydrus_dict["api_url"] = self.api_url_edit.text().strip()
        hydrus_dict["api_key"] = self.api_key_edit.text().strip()
        hydrus_dict["no_wait"] = self.no_wait_chk.isChecked()
        hydrus_dict["output_tag_services"] = {"keys": self.output_services_editor.get_items()}

        # Tag queries
        tag_queries: list[dict[str, Any]] = []
        for r in range(self.tag_q_table.rowCount()):
            tag_item = self.tag_q_table.item(r, 0)
            key_item = self.tag_q_table.item(r, 1)
            raw_tags = tag_item.text().strip() if tag_item else ""
            raw_keys = key_item.text().strip() if key_item else ""

            if raw_tags:
                tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
                keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
                tag_queries.append({"tags": tags, "tag_service_keys": keys})
        hydrus_dict["tag_queries"] = tag_queries

        # Page queries
        page_queries: list[dict[str, Any]] = []
        for r in range(self.page_q_table.rowCount()):
            name_item = self.page_q_table.item(r, 0)
            idx_item = self.page_q_table.item(r, 1)
            name = name_item.text().strip() if name_item else ""
            idx_str = idx_item.text().strip() if idx_item else ""

            if name:
                pq_data: dict[str, Any] = {"name": name}
                if idx_str.isdigit():
                    pq_data["index"] = int(idx_str)
                page_queries.append(pq_data)
        hydrus_dict["page_queries"] = page_queries

        # Add Tags (omitted as None if disabled/unchecked)
        if self.add_tags_group.isChecked():
            add_tags = [t.strip() for t in self.add_tags_edit.text().split(",") if t.strip()]
            add_keys = self.add_services_editor.get_items()
            if add_tags or add_keys:
                hydrus_dict["add_tags"] = {"tags": add_tags, "tag_service_keys": add_keys}
            else:
                hydrus_dict["add_tags"] = None
        else:
            hydrus_dict["add_tags"] = None

        # Remove Tags (omitted as None if disabled/unchecked)
        if self.rem_tags_group.isChecked():
            rem_tags = [t.strip() for t in self.rem_tags_edit.text().split(",") if t.strip()]
            rem_keys = self.rem_services_editor.get_items()
            if rem_tags or rem_keys:
                hydrus_dict["remove_tags"] = {"tags": rem_tags, "tag_service_keys": rem_keys}
            else:
                hydrus_dict["remove_tags"] = None
        else:
            hydrus_dict["remove_tags"] = None

        # Preview (omitted as None if disabled/unchecked)
        if self.prev_group.isChecked():
            prev_name = self.prev_name_edit.text().strip() or None
            prev_rej = self.prev_rej_edit.text().strip() or None
            idx_val = self.prev_index_spin.value() if self.prev_index_spin.value() > 0 else None
            rej_idx_val = self.prev_rej_index_spin.value() if self.prev_rej_index_spin.value() > 0 else None

            if prev_name or prev_rej:
                hydrus_dict["preview"] = {
                    "page_name": prev_name,
                    "page_index": idx_val,
                    "rejected_page_name": prev_rej,
                    "rejected_page_index": rej_idx_val,
                }
            else:
                hydrus_dict["preview"] = None
        else:
            hydrus_dict["preview"] = None

    def _on_add_tag_q(self) -> None:
        row = self.tag_q_table.rowCount()
        self.tag_q_table.blockSignals(True)
        self.tag_q_table.insertRow(row)
        self.tag_q_table.setItem(row, 0, QTableWidgetItem("system:untagged"))
        self.tag_q_table.setItem(row, 1, QTableWidgetItem(""))
        self.tag_q_table.blockSignals(False)
        self.changed.emit()

    def _on_remove_tag_q(self) -> None:
        rows = sorted({idx.row() for idx in self.tag_q_table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        self.tag_q_table.blockSignals(True)
        for r in rows:
            self.tag_q_table.removeRow(r)
        self.tag_q_table.blockSignals(False)
        self.changed.emit()

    def _on_add_page_q(self) -> None:
        row = self.page_q_table.rowCount()
        self.page_q_table.blockSignals(True)
        self.page_q_table.insertRow(row)
        self.page_q_table.setItem(row, 0, QTableWidgetItem("target_page"))
        self.page_q_table.setItem(row, 1, QTableWidgetItem(""))
        self.page_q_table.blockSignals(False)
        self.changed.emit()

    def _on_remove_page_q(self) -> None:
        rows = sorted({idx.row() for idx in self.page_q_table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        self.page_q_table.blockSignals(True)
        for r in rows:
            self.page_q_table.removeRow(r)
        self.page_q_table.blockSignals(False)
        self.changed.emit()
