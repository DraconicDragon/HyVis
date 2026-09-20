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
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hyvis.config import AppConfig, DatabaseConfig, HyvisConfig
from hyvis.gui.widgets import bind_field_metadata, setup_field_tooltip


class AppDbPage(QWidget):
    """Configuration page for [database] and [hyvis] sections."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        app_fields = AppConfig.model_fields
        db_fields = DatabaseConfig.model_fields
        hy_fields = HyvisConfig.model_fields

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(16)

        # 1. Database Settings
        db_group = QGroupBox(app_fields["database"].title, self)
        setup_field_tooltip(db_group, app_fields["database"])
        db_layout = QFormLayout(db_group)
        db_layout.setSpacing(8)

        path_row = QHBoxLayout()
        self.db_path_edit = QLineEdit(self)
        bind_field_metadata(self.db_path_edit, db_fields["path"])
        self.db_path_edit.textChanged.connect(lambda _: self.changed.emit())
        path_row.addWidget(self.db_path_edit, stretch=1)

        self.browse_db_btn = QPushButton("Browse...", self)
        self.browse_db_btn.clicked.connect(self._on_browse_db)
        path_row.addWidget(self.browse_db_btn)
        db_layout.addRow(f"{db_fields['path'].title}:", path_row)

        self.cache_raw_chk = QCheckBox(db_fields["cache_raw_predictions"].title, self)
        setup_field_tooltip(self.cache_raw_chk, db_fields["cache_raw_predictions"])
        self.cache_raw_chk.setChecked(True)
        self.cache_raw_chk.toggled.connect(lambda _: self.changed.emit())
        db_layout.addRow("", self.cache_raw_chk)

        self.min_score_spin = QDoubleSpinBox(self)
        self.min_score_spin.setRange(0.0, 1.0)
        self.min_score_spin.setSingleStep(0.005)
        self.min_score_spin.setDecimals(3)
        self.min_score_spin.setValue(0.010)
        setup_field_tooltip(self.min_score_spin, db_fields["min_cache_score"])
        self.min_score_spin.valueChanged.connect(lambda _: self.changed.emit())
        db_layout.addRow(f"{db_fields['min_cache_score'].title}:", self.min_score_spin)

        layout.addWidget(db_group)

        # 2. HyVis Application Settings
        app_group = QGroupBox(app_fields["hyvis"].title, self)
        setup_field_tooltip(app_group, app_fields["hyvis"])
        app_layout = QFormLayout(app_group)
        app_layout.setSpacing(8)

        self.log_level_combo = QComboBox(self)
        self.log_level_combo.addItems(["WARNING", "INFO", "DEBUG", "ERROR"])
        setup_field_tooltip(self.log_level_combo, hy_fields["log_level"])
        self.log_level_combo.currentTextChanged.connect(lambda _: self.changed.emit())
        app_layout.addRow(f"{hy_fields['log_level'].title}:", self.log_level_combo)

        self.infer_only_chk = QCheckBox(hy_fields["infer_only"].title, self)
        setup_field_tooltip(self.infer_only_chk, hy_fields["infer_only"])
        self.infer_only_chk.setChecked(False)
        self.infer_only_chk.toggled.connect(lambda _: self.changed.emit())
        app_layout.addRow("", self.infer_only_chk)

        layout.addWidget(app_group)
        layout.addStretch()

    def load_config(self, cfg: AppConfig) -> None:
        self.blockSignals(True)

        self.db_path_edit.setText(cfg.database.path)
        self.cache_raw_chk.setChecked(cfg.database.cache_raw_predictions)
        self.min_score_spin.setValue(cfg.database.min_cache_score)

        self.log_level_combo.setCurrentText(cfg.hyvis.log_level)
        self.infer_only_chk.setChecked(cfg.hyvis.infer_only)

        self.blockSignals(False)

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
            self.changed.emit()
