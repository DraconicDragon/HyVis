"""
main_window.py — Modern sidebar-driven desktop interface for HyVis.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from hyvis.config import AppConfig
from hyvis.gui.launcher import format_cli_command_str, launch_in_external_terminal
from hyvis.gui.pages import AppDbPage, FiltersPage, HydrusPage, ModelsPage
from hyvis.gui.state import ConfigState


class MainWindow(QMainWindow):
    def __init__(self, state: ConfigState | None = None) -> None:
        super().__init__()
        self.state = state or ConfigState()

        self.setWindowTitle("HyVis Configurator")
        self.resize(1020, 720)

        self._setup_menu_bar()
        self._setup_ui()
        self._setup_signals()

        # Initial synchronization
        self._load_config_to_pages(self.state.config)
        self._update_title()
        self._update_validation(self.state.validate())

    def _setup_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")

        new_action = QAction("&New Config", self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.triggered.connect(self._on_new_config)
        file_menu.addAction(new_action)

        open_action = QAction("&Open Config...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._on_open_config)
        file_menu.addAction(open_action)

        file_menu.addSeparator()

        save_action = QAction("&Save", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._on_save_config)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save &As...", self)
        save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        save_as_action.triggered.connect(self._on_save_as_config)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    def _setup_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        body_layout = QHBoxLayout()
        body_layout.setSpacing(14)

        # 1. Left Sidebar
        self.sidebar = QListWidget(self)
        self.sidebar.setFixedWidth(200)
        self.sidebar.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        sidebar_items = [
            "Hydrus Connection",
            "Models & Sessions",
            "Output & Filters",
            "Database & App",
        ]
        for name in sidebar_items:
            item = QListWidgetItem(name)
            self.sidebar.addItem(item)

        body_layout.addWidget(self.sidebar)

        # 2. Right Stacked Pages
        self.page_stack = QStackedWidget(self)

        self.hydrus_page = HydrusPage(self)
        self.models_page = ModelsPage(self)
        self.filters_page = FiltersPage(self)
        self.app_db_page = AppDbPage(self)

        self.page_stack.addWidget(self.hydrus_page)
        self.page_stack.addWidget(self.models_page)
        self.page_stack.addWidget(self.filters_page)
        self.page_stack.addWidget(self.app_db_page)

        body_layout.addWidget(self.page_stack, stretch=1)
        root_layout.addLayout(body_layout, stretch=1)

        # 3. Bottom Action & Status Bar
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(10)

        self.status_label = QLabel("Ready", self)
        bottom_bar.addWidget(self.status_label, stretch=1)

        self.copy_btn = QPushButton("Copy CLI Command", self)
        self.copy_btn.clicked.connect(self._on_copy_command)
        bottom_bar.addWidget(self.copy_btn)

        self.launch_btn = QPushButton("Launch in Terminal", self)
        self.launch_btn.clicked.connect(self._on_launch_terminal)
        bottom_bar.addWidget(self.launch_btn)

        root_layout.addLayout(bottom_bar)
        self.sidebar.setCurrentRow(0)

    def _setup_signals(self) -> None:
        self.sidebar.currentRowChanged.connect(self.page_stack.setCurrentIndex)
        self.state.dirty_changed.connect(lambda _: self._update_title())
        self.state.config_saved.connect(lambda _: self._update_title())
        self.state.validation_changed.connect(self._update_validation)
        self.state.config_loaded.connect(self._load_config_to_pages)

        # Connect page changes to central validation and dirty-tracking
        self.hydrus_page.changed.connect(self._on_page_modified)
        self.models_page.changed.connect(self._on_page_modified)
        self.filters_page.changed.connect(self._on_page_modified)
        self.app_db_page.changed.connect(self._on_page_modified)

    def _load_config_to_pages(self, cfg: AppConfig) -> None:
        self.hydrus_page.load_config(cfg)
        self.models_page.load_config(cfg)
        self.filters_page.load_config(cfg)
        self.app_db_page.load_config(cfg)

    def _gather_config_dict(self) -> dict[str, Any]:
        data = self.state.config.model_dump(mode="json")
        self.hydrus_page.apply_to_dict(data)
        self.models_page.apply_to_dict(data)
        self.filters_page.apply_to_dict(data)
        self.app_db_page.apply_to_dict(data)
        return data

    def _on_page_modified(self) -> None:
        try:
            data = self._gather_config_dict()
            updated_cfg = AppConfig.model_validate(data)
            self.state.update_config(updated_cfg)
        except Exception as exc:
            self.state.set_dirty(True)
            self._update_validation([str(exc)])

    def _update_title(self) -> None:
        path = self.state.current_path
        path_str = path.name if path else "Untitled Config"
        dirty_str = " *" if self.state.is_dirty else ""
        self.setWindowTitle(f"HyVis Configurator — {path_str}{dirty_str}")

    def _update_validation(self, errors: list[str]) -> None:
        if not errors:
            self.status_label.setText("<span style='color: #2e7d32;'>● Configuration Valid</span>")
            self.launch_btn.setEnabled(True)
        else:
            first_err = errors[0]
            count_str = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
            self.status_label.setText(f"<span style='color: #d32f2f;'>▲ {first_err}{count_str}</span>")
            self.launch_btn.setEnabled(False)

    def _on_new_config(self) -> None:
        if self._confirm_discard_changes():
            self.state.new_config()

    def _on_open_config(self) -> None:
        if not self._confirm_discard_changes():
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open HyVis TOML Configuration", "", "TOML Files (*.toml);;All Files (*)"
        )
        if file_path:
            self.state.load_from_file(file_path)

    def _on_save_config(self) -> bool:
        if self.state.current_path is None:
            return self._on_save_as_config()

        data = self._gather_config_dict()
        try:
            validated = AppConfig.model_validate(data)
            self.state.update_config(validated)
            return self.state.save_to_file()
        except Exception as exc:
            QMessageBox.critical(self, "Invalid Configuration", f"Cannot save invalid configuration:\n\n{exc}")
            return False

    def _on_save_as_config(self) -> bool:
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save HyVis TOML Configuration", "config.toml", "TOML Files (*.toml);;All Files (*)"
        )
        if not file_path:
            return False

        data = self._gather_config_dict()
        try:
            validated = AppConfig.model_validate(data)
            self.state.update_config(validated)
            return self.state.save_to_file(file_path)
        except Exception as exc:
            QMessageBox.critical(self, "Invalid Configuration", f"Cannot save invalid configuration:\n\n{exc}")
            return False

    def _confirm_discard_changes(self) -> bool:
        if not self.state.is_dirty:
            return True
        res = QMessageBox.question(
            self,
            "Unsaved Changes",
            "You have unsaved changes. Do you want to discard them?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return res == QMessageBox.StandardButton.Discard

    def _on_copy_command(self) -> None:
        if self.state.current_path is None:
            QMessageBox.information(
                self, "Save Required", "Please save your configuration file first to generate the CLI command."
            )
            return

        cmd_str = format_cli_command_str(self.state.current_path)
        QApplication.clipboard().setText(cmd_str)
        self.status_label.setText("<span>✓ Command copied to clipboard!</span>")

    def _on_launch_terminal(self) -> None:
        if self.state.current_path is None or self.state.is_dirty:
            res = QMessageBox.question(
                self,
                "Save Configuration",
                "Your configuration has unsaved changes. Save before launching?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            )
            if res == QMessageBox.StandardButton.Save:
                if not self._on_save_config():
                    return
            else:
                return

        assert self.state.current_path is not None
        ok = launch_in_external_terminal(self.state.current_path)
        if ok:
            self.status_label.setText("<span>✓ Launched HyVis in detached terminal</span>")
        else:
            cmd_str = format_cli_command_str(self.state.current_path)
            QApplication.clipboard().setText(cmd_str)
            QMessageBox.warning(
                self,
                "Terminal Launch Failed",
                "Could not detect or launch an external terminal window automatically.\n\n"
                "The execution command has been copied to your clipboard instead.",
            )

    def closeEvent(self, event) -> None:
        if self._confirm_discard_changes():
            event.accept()
        else:
            event.ignore()
