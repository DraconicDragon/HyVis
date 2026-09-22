"""
app_db_page.py — Local SQLite database settings and application-level parameters.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from hyvis.config import AppConfig, DatabaseConfig, HyvisConfig
from hyvis.gui.pages.base import BaseConfigPage
from hyvis.gui.widgets import SectionCard, SmoothScrollArea, add_form_row, bind_field_metadata, setup_field_tooltip


class AppDbPage(BaseConfigPage):
    """Configuration page for [database] and [hyvis] sections."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._is_loading_ui: bool = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        app_fields = AppConfig.model_fields
        db_fields = DatabaseConfig.model_fields
        hy_fields = HyvisConfig.model_fields

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 10, 10, 10)
        layout.setSpacing(14)

        # 1. Database Settings Card
        db_title = app_fields["database"].title or "Database"
        self.db_card = SectionCard(
            title=db_title,
            tooltip=app_fields["database"].description or "",
            parent=container,
        )
        db_layout = QFormLayout()
        db_layout.setSpacing(8)

        # DB Path Row
        path_box = QWidget(container)
        path_row = QHBoxLayout(path_box)
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(6)

        self.db_path_edit = QLineEdit(path_box)
        bind_field_metadata(self.db_path_edit, db_fields["path"])
        self.db_path_edit.textChanged.connect(lambda _: self._on_field_changed())
        path_row.addWidget(self.db_path_edit, stretch=1)

        self.browse_db_btn = QPushButton("Browse...", path_box)
        self.browse_db_btn.clicked.connect(self._on_browse_db)
        path_row.addWidget(self.browse_db_btn)

        add_form_row(db_layout, db_fields["path"], path_box)

        # Cache raw predictions checkbox
        self.cache_raw_chk = QCheckBox(db_fields["cache_raw_predictions"].title, container)
        setup_field_tooltip(self.cache_raw_chk, db_fields["cache_raw_predictions"])
        self.cache_raw_chk.setChecked(True)
        self.cache_raw_chk.toggled.connect(lambda _: self._on_field_changed())
        db_layout.addRow("", self.cache_raw_chk)

        # Min score spinbox
        self.min_score_spin = QDoubleSpinBox(container)
        self.min_score_spin.setRange(0.0, 1.0)
        self.min_score_spin.setSingleStep(0.005)
        self.min_score_spin.setDecimals(3)
        self.min_score_spin.setValue(0.010)
        self.min_score_spin.valueChanged.connect(lambda _: self._on_field_changed())
        add_form_row(db_layout, db_fields["min_cache_score"], self.min_score_spin)

        self.db_card.setContentLayout(db_layout)
        layout.addWidget(self.db_card)

        # 2. HyVis Application Settings Card
        hy_title = app_fields["hyvis"].title or "Application Settings"
        self.app_card = SectionCard(
            title=hy_title,
            tooltip=app_fields["hyvis"].description or "",
            parent=container,
        )
        app_layout = QFormLayout()
        app_layout.setSpacing(8)

        self.log_level_combo = QComboBox(container)
        self.log_level_combo.addItems(["WARNING", "INFO", "DEBUG", "ERROR"])
        self.log_level_combo.currentTextChanged.connect(lambda _: self._on_field_changed())
        add_form_row(app_layout, hy_fields["log_level"], self.log_level_combo)

        self.infer_only_chk = QCheckBox(hy_fields["infer_only"].title, container)
        setup_field_tooltip(self.infer_only_chk, hy_fields["infer_only"])
        self.infer_only_chk.setChecked(False)
        self.infer_only_chk.toggled.connect(lambda _: self._on_field_changed())
        app_layout.addRow("", self.infer_only_chk)

        self.app_card.setContentLayout(app_layout)
        layout.addWidget(self.app_card)

        layout.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll)

    def _on_field_changed(self) -> None:
        if self._is_loading_ui:
            return
        self.changed.emit()

    def load_config(self, cfg: AppConfig) -> None:
        self._is_loading_ui = True
        try:
            self.db_path_edit.setText(cfg.database.path)
            self.cache_raw_chk.setChecked(cfg.database.cache_raw_predictions)
            self.min_score_spin.setValue(cfg.database.min_cache_score)

            self.log_level_combo.setCurrentText(cfg.hyvis.log_level)
            self.infer_only_chk.setChecked(cfg.hyvis.infer_only)
        finally:
            self._is_loading_ui = False

    def apply_to_dict(self, data: dict[str, Any]) -> None:
        db_dict: dict[str, Any] = data.setdefault("database", {})
        db_dict["path"] = self.db_path_edit.text().strip() or "data/hyvis.db"
        db_dict["cache_raw_predictions"] = self.cache_raw_chk.isChecked()
        db_dict["min_cache_score"] = float(self.min_score_spin.value())

        hy_dict: dict[str, Any] = data.setdefault("hyvis", {})
        hy_dict["log_level"] = self.log_level_combo.currentText()
        hy_dict["infer_only"] = self.infer_only_chk.isChecked()

    def _on_browse_db(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Select Database File", "hyvis.db", "SQLite DB (*.db);;All Files (*)"
        )
        if path:
            self.db_path_edit.setText(path)
            self._on_field_changed()
