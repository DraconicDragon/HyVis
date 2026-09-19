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
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hyvis.config import AppConfig
from hyvis.gui.widgets import StringListEditor


class HydrusPage(QWidget):
    """Configuration page for [hydrus] connection and file targeting settings."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
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
        self.api_url_edit.setPlaceholderText("http://127.0.0.1:45869")
        self.api_url_edit.textChanged.connect(lambda _: self.changed.emit())
        conn_layout.addRow("API URL:", self.api_url_edit)

        self.api_key_edit = QLineEdit(self)
        self.api_key_edit.setPlaceholderText("Paste your Hydrus API key here")
        self.api_key_edit.textChanged.connect(lambda _: self.changed.emit())
        conn_layout.addRow("API Key:", self.api_key_edit)

        self.no_wait_chk = QCheckBox("Do not wait for Hydrus if offline (fail fast)", self)
        self.no_wait_chk.toggled.connect(lambda _: self.changed.emit())
        conn_layout.addRow("", self.no_wait_chk)

        layout.addWidget(conn_group)

        # 2. Output Tag Services
        output_group = QGroupBox("Destination Tag Services (Global)", container)
        output_layout = QVBoxLayout(output_group)

        self.output_services_editor = StringListEditor(
            placeholder="Enter Hydrus tag service key...",
            parent=self,
        )
        self.output_services_editor.changed.connect(self.changed.emit)
        output_layout.addWidget(self.output_services_editor)

        layout.addWidget(output_group)

        # 3. Tag Queries
        tag_q_group = QGroupBox("Tag Queries (Search Parameters)", container)
        tag_q_layout = QVBoxLayout(tag_q_group)

        self.tag_q_table = QTableWidget(0, 2, self)
        self.tag_q_table.setHorizontalHeaderLabels(["Tags (comma-separated)", "Tag Service Keys (empty = all)"])
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
        page_q_group = QGroupBox("Page Queries (Open Tab Retrieval)", container)
        page_q_layout = QVBoxLayout(page_q_group)

        self.page_q_table = QTableWidget(0, 2, self)
        self.page_q_table.setHorizontalHeaderLabels(["Page Tab Name", "Index (Optional Disambiguation)"])
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

        # 5. Additional / Cleanup Tags
        clean_group = QGroupBox("Post-Run Tag Actions (Add / Remove)", container)
        clean_layout = QFormLayout(clean_group)

        self.add_tags_edit = QLineEdit(self)
        self.add_tags_edit.setPlaceholderText("e.g. ai:tagged, processed")
        self.add_tags_edit.textChanged.connect(lambda _: self.changed.emit())
        clean_layout.addRow("Tags to Add:", self.add_tags_edit)

        self.add_keys_edit = QLineEdit(self)
        self.add_keys_edit.setPlaceholderText("e.g. my_tag_service_key")
        self.add_keys_edit.textChanged.connect(lambda _: self.changed.emit())
        clean_layout.addRow("Add To Service Keys:", self.add_keys_edit)

        self.rem_tags_edit = QLineEdit(self)
        self.rem_tags_edit.setPlaceholderText("e.g. temp:tagme, queue:ai")
        self.rem_tags_edit.textChanged.connect(lambda _: self.changed.emit())
        clean_layout.addRow("Tags to Remove:", self.rem_tags_edit)

        self.rem_keys_edit = QLineEdit(self)
        self.rem_keys_edit.setPlaceholderText("e.g. my_tag_service_key")
        self.rem_keys_edit.textChanged.connect(lambda _: self.changed.emit())
        clean_layout.addRow("Remove From Service Keys:", self.rem_keys_edit)

        layout.addWidget(clean_group)

        # 6. Preview Settings
        prev_group = QGroupBox("Client Previews", container)
        prev_layout = QFormLayout(prev_group)

        self.prev_name_edit = QLineEdit(self)
        self.prev_name_edit.setPlaceholderText("Optional target page name for candidates")
        self.prev_name_edit.textChanged.connect(lambda _: self.changed.emit())
        prev_layout.addRow("Candidate Preview Page:", self.prev_name_edit)

        self.prev_rej_edit = QLineEdit(self)
        self.prev_rej_edit.setPlaceholderText("Optional target page name for MIME rejections")
        self.prev_rej_edit.textChanged.connect(lambda _: self.changed.emit())
        prev_layout.addRow("Rejected Preview Page:", self.prev_rej_edit)

        layout.addWidget(prev_group)

        # Set scroll root
        scroll.setWidget(container)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

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
            # Flatten query tags
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

        # Add/Remove tags
        if h.add_tags:
            self.add_tags_edit.setText(", ".join(h.add_tags.tags))
            self.add_keys_edit.setText(", ".join(h.add_tags.tag_service_keys))
        else:
            self.add_tags_edit.clear()
            self.add_keys_edit.clear()

        if h.remove_tags:
            self.rem_tags_edit.setText(", ".join(h.remove_tags.tags))
            self.rem_keys_edit.setText(", ".join(h.remove_tags.tag_service_keys))
        else:
            self.rem_tags_edit.clear()
            self.rem_keys_edit.clear()

        # Preview
        if h.preview:
            self.prev_name_edit.setText(h.preview.page_name or "")
            self.prev_rej_edit.setText(h.preview.rejected_page_name or "")
        else:
            self.prev_name_edit.clear()
            self.prev_rej_edit.clear()

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

        # Add / Remove Tags
        add_tags = [t.strip() for t in self.add_tags_edit.text().split(",") if t.strip()]
        add_keys = [k.strip() for k in self.add_keys_edit.text().split(",") if k.strip()]
        if add_tags and add_keys:
            hydrus_dict["add_tags"] = {"tags": add_tags, "tag_service_keys": add_keys}
        else:
            hydrus_dict["add_tags"] = None

        rem_tags = [t.strip() for t in self.rem_tags_edit.text().split(",") if t.strip()]
        rem_keys = [k.strip() for k in self.rem_keys_edit.text().split(",") if k.strip()]
        if rem_tags and rem_keys:
            hydrus_dict["remove_tags"] = {"tags": rem_tags, "tag_service_keys": rem_keys}
        else:
            hydrus_dict["remove_tags"] = None

        # Preview
        prev_name = self.prev_name_edit.text().strip() or None
        prev_rej = self.prev_rej_edit.text().strip() or None
        if prev_name or prev_rej:
            hydrus_dict["preview"] = {"page_name": prev_name, "rejected_page_name": prev_rej}
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
