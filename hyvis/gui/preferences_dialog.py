"""
preferences_dialog.py — Desktop preferences dialog for persistent GUI settings.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from hyvis.gui.base_widgets import SmoothScrollArea
from hyvis.gui.settings import (
    GuiSettings,
    StartupBehavior,
    get_gui_settings,
    save_gui_settings,
)
from hyvis.gui.widgets import SectionCard


class PreferencesDialog(QDialog):
    """Modal preferences configuration dialog."""

    def __init__(self, settings: GuiSettings | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings: GuiSettings = settings or get_gui_settings()

        self.setWindowTitle("Preferences")
        self.resize(600, 480)
        self.setMinimumWidth(590)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._setup_ui()
        self._load_from_settings()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 7, 12)
        root.setSpacing(12)

        scroll = SmoothScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(2, 2, 8, 2)
        layout.setSpacing(14)

        # 1. Startup & Presets Card
        self.startup_card = SectionCard("Startup & Loading", parent=container)
        startup_layout = QFormLayout()
        startup_layout.setSpacing(8)

        self.startup_combo = QComboBox(container)
        self.startup_combo.addItem("Built-in Default Template", StartupBehavior.TEMPLATE)
        self.startup_combo.addItem("Restore Where I Left Off (Last Session)", StartupBehavior.LAST_SESSION)
        self.startup_combo.addItem("Custom Configuration Preset", StartupBehavior.CUSTOM_PRESET)
        self.startup_combo.currentIndexChanged.connect(self._on_startup_behavior_changed)
        startup_layout.addRow("Startup Behavior:", self.startup_combo)

        # Custom Preset File Row
        preset_box = QWidget(container)
        preset_row = QHBoxLayout(preset_box)
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.setSpacing(6)

        self.preset_path_edit = QLineEdit(preset_box)
        self.preset_path_edit.setPlaceholderText("Path to default .toml configuration...")
        preset_row.addWidget(self.preset_path_edit, stretch=1)

        self.browse_preset_btn = QPushButton("Browse...", preset_box)
        self.browse_preset_btn.clicked.connect(self._on_browse_preset)
        preset_row.addWidget(self.browse_preset_btn)

        startup_layout.addRow("Default Preset:", preset_box)

        self.auto_connect_chk = QCheckBox("Automatically connect to Hydrus when credentials exist", container)
        startup_layout.addRow("", self.auto_connect_chk)

        self.startup_card.setContentLayout(startup_layout)
        layout.addWidget(self.startup_card)

        # 2. Number Inputs & Precision Card
        self.spinbox_card = SectionCard("Threshold Precision & Increments", parent=container)
        spin_layout = QFormLayout()
        spin_layout.setSpacing(8)

        self.decimals_spin = QSpinBox(container)
        self.decimals_spin.setRange(2, 6)
        self.decimals_spin.setValue(2)
        self.decimals_spin.setToolTip("Decimal places displayed on threshold inputs (e.g. 2 = 0.00).")
        self.decimals_spin.valueChanged.connect(self._on_decimals_changed)
        spin_layout.addRow("Decimal Precision:", self.decimals_spin)

        self.step_spin = QDoubleSpinBox(container)
        self.step_spin.setRange(0.0001, 0.5)
        self.step_spin.setDecimals(2)
        self.step_spin.setSingleStep(0.01)
        self.step_spin.setValue(0.01)
        self.step_spin.setToolTip("Step increment when adjusting threshold values with arrow buttons.")
        spin_layout.addRow("Step Increment:", self.step_spin)

        self.spinbox_card.setContentLayout(spin_layout)
        layout.addWidget(self.spinbox_card)

        layout.addStretch(1)
        scroll.setWidget(container)
        root.addWidget(scroll, stretch=1)

        # Bottom Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_row.addStretch(1)

        self.cancel_btn = QPushButton("Cancel", self)
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("Save Preferences", self)
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._on_save_clicked)
        btn_row.addWidget(self.save_btn)

        root.addLayout(btn_row)

    def _load_from_settings(self) -> None:
        # 1. Startup behavior
        idx = self.startup_combo.findData(self.settings.startup_behavior)
        if idx >= 0:
            self.startup_combo.setCurrentIndex(idx)
        else:
            self.startup_combo.setCurrentIndex(0)

        self.preset_path_edit.setText(self.settings.custom_preset_path or "")
        self.auto_connect_chk.setChecked(self.settings.auto_connect_hydrus)
        self._on_startup_behavior_changed()

        # 2. Spinboxes
        self.decimals_spin.setValue(self.settings.spinbox_decimals)
        self.step_spin.setValue(self.settings.spinbox_step)

    def _on_startup_behavior_changed(self) -> None:
        behavior = self.startup_combo.currentData()
        is_custom = behavior == StartupBehavior.CUSTOM_PRESET
        self.preset_path_edit.setEnabled(is_custom)
        self.browse_preset_btn.setEnabled(is_custom)

    def _on_browse_preset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Default Preset Configuration", "", "TOML Files (*.toml);;All Files (*)"
        )
        if path:
            self.preset_path_edit.setText(path)

    def _on_decimals_changed(self, decimals: int) -> None:
        """Keep step increment decimal resolution and minimum value synchronized without jagged residuals."""
        min_step = 10 ** (-decimals)
        self.step_spin.setDecimals(decimals)
        self.step_spin.setMinimum(min_step)
        self.step_spin.setSingleStep(min_step)
        if self.step_spin.value() < min_step:
            self.step_spin.setValue(min_step)

    def _on_save_clicked(self) -> None:
        behavior = self.startup_combo.currentData() or StartupBehavior.TEMPLATE
        self.settings.startup_behavior = behavior
        self.settings.custom_preset_path = self.preset_path_edit.text().strip() or None
        self.settings.auto_connect_hydrus = self.auto_connect_chk.isChecked()

        self.settings.spinbox_decimals = self.decimals_spin.value()
        self.settings.spinbox_step = float(self.step_spin.value())

        save_gui_settings(self.settings)
        self.accept()
