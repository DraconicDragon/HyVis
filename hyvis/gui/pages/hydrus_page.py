"""
hydrus_page.py — Page for Hydrus API connection, search queries, and tag service targets.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from hyvis.config import (
    AppConfig,
    HydrusConfig,
    PreviewConfig,
)
from hyvis.gui.pages.base import BaseConfigPage
from hyvis.gui.schema_binding import add_form_row, bind_field_metadata
from hyvis.gui.theme import CardTheme
from hyvis.gui.widgets import (
    PageQueryListEditor,
    SectionCard,
    TagQueryListEditor,
    TagRuleListEditor,
    TagServiceListEditor,
)


class HydrusPage(BaseConfigPage):
    """Configuration page for [hydrus] connection and file targeting settings."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._available_writable_services: dict[str, str] = {}
        self._available_all_services: dict[str, str] = {}
        self._available_pages: list[dict[str, Any]] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        h_fields = HydrusConfig.model_fields
        prev_fields = PreviewConfig.model_fields

        container, layout = self.create_scroll_layout()

        # 1. API Connection Card
        self.conn_card = SectionCard(
            title="Hydrus API Connection",
            tooltip="Connection parameters for the local Hydrus Client API.",
            parent=container,
        )
        conn_layout = QFormLayout()
        conn_layout.setSpacing(8)

        self.api_url_edit = QLineEdit(self)
        self.api_url_edit.textChanged.connect(lambda _: self.emit_changed())
        add_form_row(conn_layout, h_fields, "api_url", self.api_url_edit)

        self.api_key_edit = QLineEdit(self)
        self.api_key_edit.setPlaceholderText("Paste your Hydrus API key here")
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self.api_key_edit.textChanged.connect(lambda _: self.emit_changed())
        add_form_row(conn_layout, h_fields, "api_key", self.api_key_edit)

        self.no_wait_chk = QCheckBox(self)
        bind_field_metadata(self.no_wait_chk, h_fields["no_wait"], field_name="no_wait")
        self.no_wait_chk.toggled.connect(lambda _: self.emit_changed())
        conn_layout.addRow("", self.no_wait_chk)

        self.conn_card.setContentLayout(conn_layout)
        layout.addWidget(self.conn_card)

        # 2. Output Tag Services (Global) Card
        self.output_card = SectionCard(
            field_info=h_fields["output_tag_services"],
            field_name="output_tag_services",
            parent=container,
        )
        output_layout = QVBoxLayout()
        self.output_services_editor = TagServiceListEditor(writable_only=True, parent=self)
        self.output_services_editor.changed.connect(self.emit_changed)
        output_layout.addWidget(self.output_services_editor)

        self.output_card.setContentLayout(output_layout)
        layout.addWidget(self.output_card)

        # 3. Tag Queries Card (Stacked Query Cards)
        self.tag_q_card = SectionCard(
            field_info=h_fields["tag_queries"],
            field_name="tag_queries",
            parent=container,
        )
        tag_q_layout = QVBoxLayout()
        self.tag_queries_editor = TagQueryListEditor(parent=self)
        self.tag_queries_editor.changed.connect(self.emit_changed)
        tag_q_layout.addWidget(self.tag_queries_editor)

        self.tag_q_card.setContentLayout(tag_q_layout)
        layout.addWidget(self.tag_q_card)

        # 4. Page Queries Card (Stacked Live Page Selector)
        self.page_q_card = SectionCard(
            field_info=h_fields["page_queries"],
            field_name="page_queries",
            parent=container,
        )
        page_q_layout = QVBoxLayout()
        self.page_queries_editor = PageQueryListEditor(parent=self)
        self.page_queries_editor.changed.connect(self.emit_changed)
        page_q_layout.addWidget(self.page_queries_editor)

        self.page_q_card.setContentLayout(page_q_layout)
        layout.addWidget(self.page_q_card)

        # 5. Additional Post-Run Tags Card (Stacked Multi-Rule Editor)
        self.add_tags_card = SectionCard(
            field_info=h_fields["add_tags"],
            field_name="add_tags",
            parent=container,
        )
        add_layout = QVBoxLayout()
        self.add_tags_editor = TagRuleListEditor(
            title_prefix="Add Rule",
            tags_label="Tags to Add:",
            placeholder="Add tag to apply after processing...",
            allow_search_all=False,
            show_condition=True,
            theme=CardTheme.SUCCESS,
            add_btn_text="+ Add Additional Tags Rule",
            empty_text="(No additional tags configured — click '+ Add Additional Tags Rule' below)",
            parent=self,
        )
        self.add_tags_editor.changed.connect(self.emit_changed)
        add_layout.addWidget(self.add_tags_editor)
        self.add_tags_card.setContentLayout(add_layout)
        layout.addWidget(self.add_tags_card)

        # 6. Cleanup Tags Card (Stacked Multi-Rule Editor)
        self.rem_tags_card = SectionCard(
            field_info=h_fields["remove_tags"],
            field_name="remove_tags",
            parent=container,
        )
        rem_layout = QVBoxLayout()
        self.rem_tags_editor = TagRuleListEditor(
            title_prefix="Remove Rule",
            tags_label="Tags to Remove:",
            placeholder="Add tag to remove after processing...",
            allow_search_all=False,
            show_condition=True,
            theme=CardTheme.DANGER,
            add_btn_text="+ Add Cleanup Tags Rule",
            empty_text="(No cleanup tags configured — click '+ Add Cleanup Tags Rule' below)",
            parent=self,
        )
        self.rem_tags_editor.changed.connect(self.emit_changed)
        rem_layout.addWidget(self.rem_tags_editor)
        self.rem_tags_card.setContentLayout(rem_layout)
        layout.addWidget(self.rem_tags_card)

        # 7. Preview Settings Card (Checkable)
        self.prev_card = SectionCard(field_info=h_fields["preview"], parent=container)
        self.prev_card.setCheckable(True)
        self.prev_card.setChecked(False)
        self.prev_card.toggled.connect(lambda _: self.emit_changed())
        prev_layout = QFormLayout()
        prev_layout.setSpacing(8)

        self.prev_name_edit = QLineEdit(self)
        self.prev_name_edit.textChanged.connect(lambda _: self.emit_changed())
        add_form_row(prev_layout, prev_fields, "page_name", self.prev_name_edit)

        self.prev_index_spin = QSpinBox(self)
        self.prev_index_spin.setSpecialValueText("None (0)")
        self.prev_index_spin.valueChanged.connect(lambda _: self.emit_changed())
        add_form_row(prev_layout, prev_fields, "page_index", self.prev_index_spin)

        self.prev_rej_edit = QLineEdit(self)
        self.prev_rej_edit.textChanged.connect(lambda _: self.emit_changed())
        add_form_row(prev_layout, prev_fields, "rejected_page_name", self.prev_rej_edit)

        self.prev_rej_index_spin = QSpinBox(self)
        self.prev_rej_index_spin.setSpecialValueText("None (0)")
        self.prev_rej_index_spin.valueChanged.connect(lambda _: self.emit_changed())
        add_form_row(prev_layout, prev_fields, "rejected_page_index", self.prev_rej_index_spin)

        self.prev_card.setContentLayout(prev_layout)
        layout.addWidget(self.prev_card)

    def update_services(self, all_tags: dict[str, Any], writable_tags: dict[str, str]) -> None:
        """Update active services across all tag service editors on this page."""
        self._available_writable_services = dict(writable_tags)
        self._available_all_services = dict(all_tags)

        self.output_services_editor.set_available_services(writable_tags)
        self.add_tags_editor.set_available_services(writable_tags)
        self.rem_tags_editor.set_available_services(writable_tags)
        self.tag_queries_editor.set_available_services(all_tags)

    def update_pages(self, pages: list[dict[str, Any]]) -> None:
        """Update open Hydrus media pages in the page query editor."""
        self._available_pages = list(pages)
        self.page_queries_editor.set_available_pages(pages)

    def load_config(self, cfg: AppConfig) -> None:
        """Populate widgets from AppConfig under signal guard."""
        with self.suppress_changes():
            h = cfg.hydrus

            self.api_url_edit.setText(h.api_url)
            self.api_key_edit.setText(h.api_key)
            self.no_wait_chk.setChecked(h.no_wait)

            self.output_services_editor.set_items(h.output_tag_services.keys)
            self.tag_queries_editor.set_queries(h.tag_queries)
            self.page_queries_editor.set_queries(h.page_queries)

            self.add_tags_editor.set_rules(h.add_tags)
            self.rem_tags_editor.set_rules(h.remove_tags)

            # Preview (disabled by default, with friendly default names pre-filled)
            if h.preview:
                self.prev_card.setChecked(True)
                self.prev_name_edit.setText(h.preview.page_name or "hyvis preview")
                self.prev_index_spin.setValue(h.preview.page_index if h.preview.page_index is not None else 0)
                self.prev_rej_edit.setText(h.preview.rejected_page_name or "hyvis rejected")
                self.prev_rej_index_spin.setValue(
                    h.preview.rejected_page_index if h.preview.rejected_page_index is not None else 0
                )
            else:
                self.prev_card.setChecked(False)
                self.prev_name_edit.setText("hyvis preview")
                self.prev_index_spin.setValue(0)
                self.prev_rej_edit.setText("hyvis rejected")
                self.prev_rej_index_spin.setValue(0)

    def apply_to_dict(self, data: dict[str, Any]) -> None:
        """Serialize widget states into the raw dictionary representation for AppConfig."""
        hydrus_dict: dict[str, Any] = data.setdefault("hydrus", {})

        hydrus_dict["api_url"] = self.api_url_edit.text().strip()
        hydrus_dict["api_key"] = self.api_key_edit.text().strip()
        hydrus_dict["no_wait"] = self.no_wait_chk.isChecked()
        hydrus_dict["output_tag_services"] = {"keys": self.output_services_editor.get_items()}

        hydrus_dict["tag_queries"] = self.tag_queries_editor.get_queries()
        hydrus_dict["page_queries"] = self.page_queries_editor.get_queries()

        hydrus_dict["add_tags"] = self.add_tags_editor.get_rules()
        hydrus_dict["remove_tags"] = self.rem_tags_editor.get_rules()

        # Preview (omitted as None if unchecked)
        if self.prev_card.isChecked():
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
