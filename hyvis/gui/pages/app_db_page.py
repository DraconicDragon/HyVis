"""
app_db_page.py — Local SQLite database settings and application-level parameters.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from hyvis.config import AppConfig, DatabaseConfig, HyvisConfig
from hyvis.gui.base_widgets import FloatSpinBox
from hyvis.gui.pages.base import BaseConfigPage
from hyvis.gui.schema_binding import add_form_row, bind_field_metadata, set_combo_value
from hyvis.gui.theme import CardTheme
from hyvis.gui.widgets import PathPickerWidget, SectionCard


class AppDbPage(BaseConfigPage):
    """Configuration page for [database] and [hyvis] sections."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        app_fields = AppConfig.model_fields
        db_fields = DatabaseConfig.model_fields
        hy_fields = HyvisConfig.model_fields

        container, layout = self.create_scroll_layout()

        # 0. Informational Notice Card (Advanced Settings)
        self.notice_card = SectionCard(theme=CardTheme.INFO_VIVID, parent=container)
        notice_layout = QVBoxLayout()
        notice_layout.setContentsMargins(6, 6, 6, 6)
        notice_lbl = QLabel(
            "<b>Notice:</b> Default settings here should be fine. Aside from Log Level, most users likely do not want/need to change anything on this page.",
            self.notice_card,
        )
        notice_lbl.setStyleSheet("border: none; background: transparent;")
        notice_lbl.setWordWrap(True)
        notice_layout.addWidget(notice_lbl)
        self.notice_card.setContentLayout(notice_layout)
        layout.addWidget(self.notice_card)

        # 1. Database Settings Card
        self.db_card = SectionCard(field_info=app_fields["database"], parent=container)
        db_layout = QFormLayout()
        db_layout.setSpacing(8)

        # DB Path Row
        self.db_path_picker = PathPickerWidget(
            mode="save_file",
            dialog_title="Select Database File",
            default_filename="hyvis.db",
            file_filter="SQLite DB (*.db);;All Files (*)",
            parent=container,
        )
        self.db_path_picker.textChanged.connect(lambda _: self._on_db_path_changed())
        add_form_row(db_layout, db_fields, "path", self.db_path_picker)

        # Cache DB Path Row
        self.cache_path_picker = PathPickerWidget(
            mode="save_file",
            dialog_title="Select Cache Database File",
            default_filename="cache.db",
            file_filter="SQLite DB (*.db);;All Files (*)",
            empty_display=self._auto_cache_db_display,
            parent=container,
        )
        self.cache_path_picker.setPlaceholderText("Auto (alongside database path as cache.db)")
        self.cache_path_picker.textChanged.connect(lambda _: self.emit_changed())
        add_form_row(db_layout, db_fields, "cache_path", self.cache_path_picker, set_placeholder=False)

        # Cache raw predictions checkbox
        self.cache_raw_chk = QCheckBox(container)
        bind_field_metadata(self.cache_raw_chk, db_fields["cache_raw_predictions"], field_name="cache_raw_predictions")
        self.cache_raw_chk.toggled.connect(lambda _: self.emit_changed())
        db_layout.addRow("", self.cache_raw_chk)

        # Min score spinbox (decimals at global decimals + 1)
        self.min_score_spin = FloatSpinBox(container, extra_decimals=1, step_scale=0.5)
        self.min_score_spin.valueChanged.connect(lambda _: self.emit_changed())
        add_form_row(db_layout, db_fields, "min_cache_score", self.min_score_spin)

        self.db_card.setContentLayout(db_layout)
        layout.addWidget(self.db_card)

        # 2. HyVis Application Settings Card
        self.app_card = SectionCard(field_info=app_fields["hyvis"], parent=container)
        app_layout = QFormLayout()
        app_layout.setSpacing(8)

        self.log_level_combo = QComboBox(container)
        self.log_level_combo.currentIndexChanged.connect(lambda _: self.emit_changed())
        add_form_row(app_layout, hy_fields, "log_level", self.log_level_combo)

        self.infer_only_chk = QCheckBox(container)
        bind_field_metadata(self.infer_only_chk, hy_fields["infer_only"], field_name="infer_only")
        self.infer_only_chk.toggled.connect(lambda _: self.emit_changed())
        app_layout.addRow("", self.infer_only_chk)

        self.max_log_files_spin = QSpinBox(container)
        self.max_log_files_spin.valueChanged.connect(lambda _: self.emit_changed())
        add_form_row(app_layout, hy_fields, "max_log_files", self.max_log_files_spin)

        self.log_dir_picker = PathPickerWidget(
            mode="directory",
            dialog_title="Select Log Directory",
            parent=container,
        )
        self.log_dir_picker.textChanged.connect(lambda _: self.emit_changed())
        add_form_row(app_layout, hy_fields, "log_dir", self.log_dir_picker)

        self.app_card.setContentLayout(app_layout)
        layout.addWidget(self.app_card)

        layout.addStretch()

    def _auto_cache_db_display(self) -> str:
        db_raw = self.db_path_picker.text().strip() or "data/hyvis.db"
        db_abs = Path(os.path.abspath(os.path.expanduser(db_raw)))
        return f"Resolves to: {db_abs.with_name('cache.db')} (auto beside database.path)"

    def _on_db_path_changed(self) -> None:
        self.cache_path_picker.refresh_resolved_path()
        self.emit_changed()

    def load_config(self, cfg: AppConfig) -> None:
        with self.suppress_changes():
            self.db_path_picker.setText(cfg.database.path)
            self.cache_path_picker.setText(cfg.database.cache_path or "")
            self.cache_path_picker.refresh_resolved_path()
            self.cache_raw_chk.setChecked(cfg.database.cache_raw_predictions)
            self.min_score_spin.setValue(cfg.database.min_cache_score)

            set_combo_value(self.log_level_combo, cfg.hyvis.log_level)
            self.infer_only_chk.setChecked(cfg.hyvis.infer_only)
            self.max_log_files_spin.setValue(cfg.hyvis.max_log_files)
            self.log_dir_picker.setText(cfg.hyvis.log_dir)

    def apply_to_dict(self, data: dict[str, Any]) -> None:
        db_dict: dict[str, Any] = data.setdefault("database", {})
        db_dict["path"] = self.db_path_picker.text().strip() or "data/hyvis.db"
        db_dict["cache_path"] = self.cache_path_picker.text().strip() or None
        db_dict["cache_raw_predictions"] = self.cache_raw_chk.isChecked()
        db_dict["min_cache_score"] = float(self.min_score_spin.value())

        hy_dict: dict[str, Any] = data.setdefault("hyvis", {})
        hy_dict["log_level"] = str(self.log_level_combo.currentData() or self.log_level_combo.currentText())
        hy_dict["infer_only"] = self.infer_only_chk.isChecked()
        hy_dict["max_log_files"] = self.max_log_files_spin.value()
        hy_dict["log_dir"] = self.log_dir_picker.text().strip() or "logs"
