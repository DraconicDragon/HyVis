"""
app_db_page.py — Local SQLite database settings and application-level parameters.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from hyvis.config import AppConfig, DatabaseConfig, HyvisConfig
from hyvis.gui.base_widgets import FloatSpinBox
from hyvis.gui.pages.base import BaseConfigPage
from hyvis.gui.theme import CardTheme
from hyvis.gui.widgets import SectionCard, add_form_row, bind_field_metadata, set_combo_value


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
        path_box = QWidget(container)
        path_row = QHBoxLayout(path_box)
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(6)

        self.db_path_edit = QLineEdit(path_box)
        bind_field_metadata(self.db_path_edit, db_fields["path"])
        self.db_path_edit.textChanged.connect(lambda _: self.emit_changed())
        path_row.addWidget(self.db_path_edit, stretch=1)

        self.browse_db_btn = QPushButton("Browse...", path_box)
        self.browse_db_btn.clicked.connect(self._on_browse_db)
        path_row.addWidget(self.browse_db_btn)

        add_form_row(db_layout, db_fields, "path", path_box)

        # Cache DB Path Row
        cache_path_box = QWidget(container)
        cache_path_row = QHBoxLayout(cache_path_box)
        cache_path_row.setContentsMargins(0, 0, 0, 0)
        cache_path_row.setSpacing(6)

        self.cache_path_edit = QLineEdit(cache_path_box)
        bind_field_metadata(self.cache_path_edit, db_fields["cache_path"], set_placeholder=False)
        self.cache_path_edit.setPlaceholderText("Auto (alongside database path as cache.db)")
        self.cache_path_edit.textChanged.connect(lambda _: self.emit_changed())
        cache_path_row.addWidget(self.cache_path_edit, stretch=1)

        self.browse_cache_db_btn = QPushButton("Browse...", cache_path_box)
        self.browse_cache_db_btn.clicked.connect(self._on_browse_cache_db)
        cache_path_row.addWidget(self.browse_cache_db_btn)

        add_form_row(db_layout, db_fields, "cache_path", cache_path_box, set_placeholder=False)

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

        # Log Directory Row with Folder Browser
        log_dir_box = QWidget(container)
        log_dir_row = QHBoxLayout(log_dir_box)
        log_dir_row.setContentsMargins(0, 0, 0, 0)
        log_dir_row.setSpacing(6)

        self.log_dir_edit = QLineEdit(log_dir_box)
        bind_field_metadata(self.log_dir_edit, hy_fields["log_dir"])
        self.log_dir_edit.textChanged.connect(lambda _: self.emit_changed())
        log_dir_row.addWidget(self.log_dir_edit, stretch=1)

        self.browse_log_dir_btn = QPushButton("Browse Folder...", log_dir_box)
        self.browse_log_dir_btn.clicked.connect(self._on_browse_log_dir)
        log_dir_row.addWidget(self.browse_log_dir_btn)

        add_form_row(app_layout, hy_fields, "log_dir", log_dir_box)

        self.app_card.setContentLayout(app_layout)
        layout.addWidget(self.app_card)

        layout.addStretch()

    def load_config(self, cfg: AppConfig) -> None:
        with self.suppress_changes():
            self.db_path_edit.setText(cfg.database.path)
            self.cache_path_edit.setText(cfg.database.cache_path or "")
            self.cache_raw_chk.setChecked(cfg.database.cache_raw_predictions)
            self.min_score_spin.setValue(cfg.database.min_cache_score)

            set_combo_value(self.log_level_combo, cfg.hyvis.log_level)
            self.infer_only_chk.setChecked(cfg.hyvis.infer_only)
            self.max_log_files_spin.setValue(cfg.hyvis.max_log_files)
            self.log_dir_edit.setText(cfg.hyvis.log_dir)

    def apply_to_dict(self, data: dict[str, Any]) -> None:
        db_dict: dict[str, Any] = data.setdefault("database", {})
        db_dict["path"] = self.db_path_edit.text().strip() or "data/hyvis.db"
        db_dict["cache_path"] = self.cache_path_edit.text().strip() or None
        db_dict["cache_raw_predictions"] = self.cache_raw_chk.isChecked()
        db_dict["min_cache_score"] = float(self.min_score_spin.value())

        hy_dict: dict[str, Any] = data.setdefault("hyvis", {})
        hy_dict["log_level"] = str(self.log_level_combo.currentData() or self.log_level_combo.currentText())
        hy_dict["infer_only"] = self.infer_only_chk.isChecked()
        hy_dict["max_log_files"] = self.max_log_files_spin.value()
        hy_dict["log_dir"] = self.log_dir_edit.text().strip() or "logs"

    def _on_browse_db(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Select Database File", "hyvis.db", "SQLite DB (*.db);;All Files (*)"
        )
        if path:
            self.db_path_edit.setText(path)
            self.emit_changed()

    def _on_browse_cache_db(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Select Cache Database File", "cache.db", "SQLite DB (*.db);;All Files (*)"
        )
        if path:
            self.cache_path_edit.setText(path)
            self.emit_changed()

    def _on_browse_log_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Log Directory")
        if folder:
            self.log_dir_edit.setText(folder)
            self.emit_changed()
