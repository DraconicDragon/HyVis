"""
main_window.py — Modern sidebar-driven desktop interface for HyVis.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from hyvis.cli import get_version
from hyvis.config import AppConfig
from hyvis.gui.launcher import format_cli_command_str, launch_in_external_terminal
from hyvis.gui.pages import AppDbPage, BaseConfigPage, FiltersPage, HydrusPage, ModelsPage
from hyvis.gui.state import _DEFAULT_CONFIG_DICT, ConfigState


@dataclass(frozen=True)
class ValidationIssue:
    message: str
    page_index: int  # 0: Hydrus, 1: Models, 2: Filters, 3: AppDb
    section_title: str
    field_name: str | None = None
    model_index: int | None = None


def _parse_pydantic_error(err: dict[str, Any]) -> ValidationIssue:
    loc = err.get("loc", ())
    msg = err.get("msg", "Invalid value")

    top_section = str(loc[0]) if loc else ""
    page_idx = 0
    section_title = "General"
    field_name = None
    model_idx = None

    if top_section == "hydrus":
        page_idx = 0
        section_title = "Hydrus"
        if len(loc) > 1:
            field_name = str(loc[1])
            from hyvis.config import HydrusConfig

            field_info = HydrusConfig.model_fields.get(field_name)
            title = field_info.title if field_info and field_info.title else field_name
            msg = f"{title}: {msg}"

    elif top_section == "output_filter":
        page_idx = 2
        section_title = "Output Filter"
        if len(loc) > 1:
            field_name = str(loc[1])
            from hyvis.config import OutputFilterConfig

            field_info = OutputFilterConfig.model_fields.get(field_name)
            title = field_info.title if field_info and field_info.title else field_name
            msg = f"{title}: {msg}"

    elif top_section in ("database", "hyvis"):
        page_idx = 3
        section_title = "Database & App"
        if len(loc) > 1:
            field_name = str(loc[1])
            from hyvis.config import DatabaseConfig, HyvisConfig

            fields = DatabaseConfig.model_fields if top_section == "database" else HyvisConfig.model_fields
            field_info = fields.get(field_name)
            title = field_info.title if field_info and field_info.title else field_name
            msg = f"{title}: {msg}"

    elif top_section == "inference":
        if len(loc) >= 3 and str(loc[1]) == "models":
            try:
                model_idx = int(loc[2])
            except (ValueError, TypeError):
                model_idx = None

            if len(loc) >= 5 and str(loc[3]) == "output_filter":
                page_idx = 2  # Model-specific filter override
                section_title = f"Model #{model_idx + 1} Filter" if model_idx is not None else "Model Filter"
                field_name = str(loc[4])
                from hyvis.config import OutputFilterConfig

                field_info = OutputFilterConfig.model_fields.get(field_name)
                title = field_info.title if field_info and field_info.title else field_name
                msg = f"{title}: {msg}"
            else:
                page_idx = 1
                section_title = f"Model #{model_idx + 1}" if model_idx is not None else "Inference Models"
                if len(loc) >= 4:
                    field_name = str(loc[3])
                    from hyvis.config import ModelConfig

                    field_info = ModelConfig.model_fields.get(field_name)
                    title = field_info.title if field_info and field_info.title else field_name
                    msg = f"{title}: {msg}"
        else:
            page_idx = 1
            section_title = "Inference Models"

    return ValidationIssue(
        message=msg,
        page_index=page_idx,
        section_title=section_title,
        field_name=field_name,
        model_index=model_idx,
    )


def _parse_business_rule_issue(rule_err: str) -> ValidationIssue:
    msg = rule_err
    page_idx = 0
    section_title = "General"
    field_name = None

    if rule_err.startswith("[hydrus]"):
        page_idx = 0
        section_title = "Hydrus"
        msg = rule_err.replace("[hydrus] ", "").strip()
        if "tag_queries" in rule_err:
            field_name = "tag_queries"
        elif "output_tag_services" in rule_err:
            field_name = "output_tag_services"
    elif rule_err.startswith("[output_filter]"):
        page_idx = 2
        section_title = "Output Filter"
        msg = rule_err.replace("[output_filter] ", "").strip()
        if "output_categories" in rule_err:
            field_name = "output_categories"
    elif rule_err.startswith("[inference]"):
        page_idx = 1
        section_title = "Inference Models"
        msg = rule_err.replace("[inference] ", "").strip()

    return ValidationIssue(
        message=msg,
        page_index=page_idx,
        section_title=section_title,
        field_name=field_name,
    )


class MainWindow(QMainWindow):
    def __init__(self, state: ConfigState | None = None) -> None:
        super().__init__()
        self.state = state or ConfigState()
        self._highlighted_error_widgets: set[QWidget] = set()

        self.setWindowTitle("HyVis Configurator")
        self.setMinimumSize(540, 374)
        self.resize(1020, 750)

        self._setup_menu_bar()
        self._setup_ui()
        self._setup_signals()

        # Initial synchronization
        self._load_config_to_pages(self.state.config)
        self._update_title()
        self._on_connection_changed(self.state.connection_status, self.state.connection_info)

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
        app_fields = AppConfig.model_fields

        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(12, 10, 12, 12)
        root_layout.setSpacing(8)

        # 1. Top Global Bar (Header & Hydrus Status)
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 2)
        top_bar.setSpacing(10)

        app_title = QLabel(
            f"<span style='font-size: 24px; font-weight: 650; letter-spacing: 0.5px;'>HyVis</span> "
            f"<span style='color: #8a9ba5; font-size: 14px; font-weight: 450;'>{get_version()}</span>",
            self,
        )
        top_bar.addWidget(app_title)

        top_bar.addStretch()

        self.conn_indicator = QLabel(self)
        self.conn_indicator.setText("<span style='color: #888;'>○ Offline</span>")
        top_bar.addWidget(self.conn_indicator)

        self.sync_services_btn = QPushButton("⟳ Sync Services", self)
        self.sync_services_btn.setToolTip("Connect to Hydrus API and refresh available tag services")
        self.sync_services_btn.clicked.connect(self._on_sync_services)
        top_bar.addWidget(self.sync_services_btn)

        root_layout.addLayout(top_bar)

        # Divider line
        divider = QFrame(self)
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        divider.setStyleSheet("color: #333;")
        root_layout.addWidget(divider)

        # 2. Main Vertical Splitter (Page Body + Issues Drawer)
        self.main_splitter = QSplitter(Qt.Orientation.Vertical, central)
        self.main_splitter.setChildrenCollapsible(False)

        # Body container (Sidebar + Divider + Page Stack)
        body_container = QWidget(self.main_splitter)
        body_layout = QHBoxLayout(body_container)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)

        # Left Sidebar
        self.sidebar = QListWidget(body_container)
        self.sidebar.setFixedWidth(168)
        self.sidebar.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        palette = self.sidebar.palette()
        highlight_col = palette.color(palette.ColorRole.Highlight)
        r, g, b = highlight_col.red(), highlight_col.green(), highlight_col.blue()
        hover_rgba = f"rgba({r}, {g}, {b}, 30)"
        selected_rgba = f"rgba({r}, {g}, {b}, 65)"
        accent_hex = highlight_col.name()

        self.sidebar.setStyleSheet(
            "QListWidget {"
            "  background: transparent;"
            "  border: none;"
            "  outline: none;"
            "  padding-top: 6px;"
            "}"
            "QListWidget::item {"
            "  padding: 10px 14px;"
            "  margin: 4px 4px 4px 0px;"
            "  border-radius: 6px;"
            "  color: #b0bec5;"
            "}"
            f"QListWidget::item:hover {{"
            f"  background: {hover_rgba};"
            f"  color: #eceff1;"
            f"}}"
            f"QListWidget::item:selected {{"
            f"  background: {selected_rgba};"
            f"  color: {accent_hex};"
            f"  font-weight: 600;"
            f"}}"
        )

        sidebar_sections = [
            (app_fields["hydrus"].title, app_fields["hydrus"].description),
            (app_fields["inference"].title, app_fields["inference"].description),
            (app_fields["output_filter"].title, app_fields["output_filter"].description),
            (
                f"{app_fields['database'].title} & App",
                f"{app_fields['database'].description} {app_fields['hyvis'].description}",
            ),
        ]
        for title, desc in sidebar_sections:
            item = QListWidgetItem(title)
            item.setToolTip(desc)
            self.sidebar.addItem(item)

        body_layout.addWidget(self.sidebar)

        # Vertical Divider between sidebar and page stack
        v_divider = QFrame(body_container)
        v_divider.setFrameShape(QFrame.Shape.VLine)
        v_divider.setFrameShadow(QFrame.Shadow.Sunken)
        v_divider.setStyleSheet("color: #333;")
        body_layout.addWidget(v_divider)

        # Right Stacked Pages
        self.page_stack = QStackedWidget(body_container)

        self.hydrus_page = HydrusPage(self)
        self.models_page = ModelsPage(self)
        self.filters_page = FiltersPage(self)
        self.app_db_page = AppDbPage(self)

        # Central registry of configuration pages (inheriting BaseConfigPage)
        self.pages: list[BaseConfigPage] = [
            self.hydrus_page,
            self.models_page,
            self.filters_page,
            self.app_db_page,
        ]

        for page in self.pages:
            self.page_stack.addWidget(page)

        body_layout.addWidget(self.page_stack, stretch=1)
        self.main_splitter.addWidget(body_container)

        # 3. Resizable Issues Panel (Drawer)
        self.issues_panel = QFrame(self.main_splitter)
        self.issues_panel.setFrameShape(QFrame.Shape.StyledPanel)
        self.issues_panel.setStyleSheet(
            "QFrame {  background: rgba(255, 255, 255, 0.015);  border-top: 1px solid rgba(255, 255, 255, 0.08);}"
        )
        issues_layout = QVBoxLayout(self.issues_panel)
        issues_layout.setContentsMargins(8, 6, 8, 8)
        issues_layout.setSpacing(6)

        issues_header = QHBoxLayout()
        # Clean, unstyled label
        self.issues_title = QLabel("Issues (0)", self.issues_panel)
        issues_header.addWidget(self.issues_title)
        issues_header.addStretch(1)

        close_issues_btn = QPushButton("✕", self.issues_panel)
        close_issues_btn.setFixedWidth(24)
        close_issues_btn.setFixedHeight(24)
        close_issues_btn.setToolTip("Hide Issues Panel")
        close_issues_btn.setStyleSheet(
            "QPushButton { border: none; color: #888; font-weight: bold; border-radius: 3px; }"
            "QPushButton:hover { color: #fff; background: rgba(255, 255, 255, 0.1); }"
        )
        close_issues_btn.clicked.connect(lambda: self.issues_panel.setVisible(False))
        issues_header.addWidget(close_issues_btn)
        issues_layout.addLayout(issues_header)

        self.issues_list = QListWidget(self.issues_panel)
        self.issues_list.setStyleSheet(
            "QListWidget {"
            "  background: transparent;"
            "  border: 1px solid rgba(255, 255, 255, 0.06);"
            "  border-radius: 4px;"
            "  outline: none;"  # Removes the dotted focus rect
            "}"
            "QListWidget::item {"
            "  padding: 6px 10px;"
            "  color: #ff8585;"  # Immediately visible red error text
            "  border-bottom: 1px solid rgba(255, 255, 255, 0.03);"
            "  border-radius: 3px;"
            "}"
            "QListWidget::item:hover {"
            "  background: rgba(248, 81, 73, 0.12);"
            "  color: #ffffff;"
            "}"
            "QListWidget::item:selected {"
            "  background: rgba(248, 81, 73, 0.22);"
            "  color: #ffffff;"
            "}"
        )
        self.issues_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.issues_list.customContextMenuRequested.connect(self._on_issues_context_menu)
        self.issues_list.itemClicked.connect(self._on_issue_selected)
        issues_layout.addWidget(self.issues_list)

        self.main_splitter.addWidget(self.issues_panel)
        self.issues_panel.setVisible(False)
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 0)

        root_layout.addWidget(self.main_splitter, stretch=1)

        # 4. Bottom Action & Status Bar
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(10)

        # Native-text button without bare HTML tags
        self.status_btn = QPushButton(self)
        self.status_btn.setObjectName("status_btn")
        self.status_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status_btn.clicked.connect(self._toggle_issues_panel)
        bottom_bar.addWidget(self.status_btn, stretch=1)

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
        self.state.config_loaded.connect(self._load_config_to_pages)

        # Hydrus entity sourcing signals
        self.state.services_updated.connect(self._on_services_updated)
        self.state.pages_updated.connect(self._on_pages_updated)
        self.state.connection_changed.connect(self._on_connection_changed)

        # Cross-page synchronization for per-model filters
        self.models_page.request_filter_scope.connect(self._on_request_filter_scope)
        self.models_page.changed.connect(lambda: self.filters_page.sync_models(self.models_page._models_data))
        self.filters_page.changed.connect(
            lambda: self.models_page.update_filter_overrides(self.filters_page._models_data)
        )

        # Universal page modification tracking
        for page in self.pages:
            page.changed.connect(self._on_page_modified)

    def _load_config_to_pages(self, cfg: AppConfig) -> None:
        """Reset pages to a clean baseline before populating the incoming configuration."""
        default_cfg = AppConfig.model_validate(_DEFAULT_CONFIG_DICT)

        # 1. Baseline Reset across all pages (clears tables, lists, and draft overrides)
        for page in self.pages:
            page.load_config(default_cfg)

        # 2. Populate target configuration
        if self.state.pages:
            self._on_pages_updated(self.state.pages)

        for page in self.pages:
            page.load_config(cfg)

        # 3. Synchronize models across dependent pages
        self.filters_page.sync_models(self.models_page._models_data)

        # 4. Run a full validation pass to instantly highlight Pydantic/Schema errors from lenient loads.
        # We restore the dirty flag immediately so opening a file doesn't instantly mark it as unsaved.
        was_dirty = self.state.is_dirty
        issues = self._run_validation()
        self._update_validation_issues(issues)
        self.state.set_dirty(was_dirty)

    def _gather_config_dict(self) -> dict[str, Any]:
        data = self.state.config.model_dump(mode="json")
        for page in self.pages:
            page.apply_to_dict(data)
        return data

    def _on_request_filter_scope(self, model_index: int) -> None:
        """Navigate to the Output & Filters page and select the requested model scope."""
        # Index 2 is FiltersPage (Hydrus=0, Models=1, Filters=2, AppDb=3)
        self.sidebar.setCurrentRow(2)
        self.filters_page.set_scope_by_model_index(model_index)

    def _toggle_issues_panel(self) -> None:
        """Toggle the visibility of the issues drawer."""
        is_visible = self.issues_panel.isVisible()
        self.issues_panel.setVisible(not is_visible)
        if not is_visible:
            sizes = self.main_splitter.sizes()
            total = sum(sizes)
            self.main_splitter.setSizes([max(total - 140, 200), 140])

    def _on_page_modified(self) -> None:
        issues = self._run_validation()
        self._update_validation_issues(issues)

    def _run_validation(self) -> list[ValidationIssue]:
        from hyvis.config import construct_lenient

        issues: list[ValidationIssue] = []
        data = self._gather_config_dict()
        cfg_for_business_rules: AppConfig | None = None

        # 1. Pydantic schema validation
        try:
            cfg_for_business_rules = AppConfig.model_validate(data)
            self.state.update_config(cfg_for_business_rules)
        except ValidationError as exc:
            self.state.set_dirty(True)
            for err in exc.errors():
                issues.append(_parse_pydantic_error(err))

            # Build a lenient config so we can STILL check business rules
            # against the user's current WIP data even if schema validation failed!
            cfg_for_business_rules = construct_lenient(AppConfig, data)
        except Exception as exc:
            self.state.set_dirty(True)
            issues.append(ValidationIssue(message=str(exc), page_index=0, section_title="Configuration"))

        # 2. Business rules validation (runs on valid model or lenient fallback)
        if cfg_for_business_rules is not None:
            for err_str in cfg_for_business_rules.hyvis_validate():
                b_issue = _parse_business_rule_issue(err_str)
                # Deduplicate if already reported
                if not any(i.message == b_issue.message for i in issues):
                    issues.append(b_issue)

        return issues

    def _update_validation_issues(self, issues: list[ValidationIssue]) -> None:
        self.issues_list.clear()
        count = len(issues)
        self.issues_title.setText(f"Issues ({count})")

        # 1. Clear previous error highlights
        from hyvis.gui.widgets import set_widget_override_state

        for w in self._highlighted_error_widgets:
            set_widget_override_state(w, is_overridden=False, is_error=False)
        self._highlighted_error_widgets.clear()

        # 2. Update status button and panel
        if count == 0:
            self.status_btn.setText("● Configuration Valid")
            self.status_btn.setStyleSheet(
                "QPushButton#status_btn {"
                "  background: transparent; border: none; text-align: left;"
                "  padding: 4px 8px; border-radius: 4px; color: #2e7d32; font-weight: 600;"
                "}"
                "QPushButton#status_btn:hover { background: rgba(46, 125, 50, 0.08); }"
            )
            self.status_btn.setToolTip("No validation issues found")
            self.launch_btn.setEnabled(True)
            self.issues_panel.setVisible(False)
        else:
            self.status_btn.setText(f"▲ {count} Issue{'s' if count != 1 else ''} Found  (Click to toggle)")
            self.status_btn.setStyleSheet(
                "QPushButton#status_btn {"
                "  background: transparent; border: none; text-align: left;"
                "  padding: 4px 8px; border-radius: 4px; color: #f85149; font-weight: 600;"
                "}"
                "QPushButton#status_btn:hover { background: rgba(248, 81, 73, 0.1); }"
            )
            self.status_btn.setToolTip(f"{count} validation issues blocking launch. Click to toggle panel.")
            self.launch_btn.setEnabled(False)
            # self.issues_panel.setVisible(True)  # Auto-expand drawer on issues detected

            for issue in issues:
                item = QListWidgetItem(f"▲ [{issue.section_title}] {issue.message}")
                item.setData(Qt.ItemDataRole.UserRole, issue)
                item.setToolTip(f"Click to navigate to {issue.section_title}")
                self.issues_list.addItem(item)

                # 3. Real-time red error highlight on the source widget
                if issue.field_name and 0 <= issue.page_index < len(self.pages):
                    target_page = self.pages[issue.page_index]
                    w = target_page.findChild(QWidget, issue.field_name)
                    if w:
                        set_widget_override_state(w, is_overridden=False, is_error=True)
                        self._highlighted_error_widgets.add(w)

    def _on_issue_selected(self, item: QListWidgetItem) -> None:
        issue: ValidationIssue | None = item.data(Qt.ItemDataRole.UserRole)
        if not issue:
            return

        # 1. Switch to target page
        if 0 <= issue.page_index < len(self.pages):
            self.sidebar.setCurrentRow(issue.page_index)

        # 2. Scope navigation
        if issue.page_index == 2:  # FiltersPage
            if issue.model_index is not None:
                self.filters_page.set_scope_by_model_index(issue.model_index)
            else:
                self.filters_page.scope_combo.setCurrentIndex(0)
        elif issue.page_index == 1 and issue.model_index is not None:  # ModelsPage
            self.models_page.model_list.setCurrentRow(issue.model_index)

        # 3. Focus & scroll to widget
        if issue.field_name and 0 <= issue.page_index < len(self.pages):
            target_page = self.pages[issue.page_index]
            widget = target_page.findChild(QWidget, issue.field_name)
            if widget:
                widget.setFocus()
                scroll = target_page.findChild(QScrollArea)
                if scroll:
                    scroll.ensureWidgetVisible(widget, 50, 80)

    def _on_issues_context_menu(self, pos: QPoint) -> None:
        """Display right-click context menu to copy issue details."""
        item = self.issues_list.itemAt(pos)
        if not item:
            return

        issue: ValidationIssue | None = item.data(Qt.ItemDataRole.UserRole)
        if not issue:
            return

        menu = QMenu(self)
        copy_msg_action = menu.addAction("Copy Message")

        action = menu.exec(self.issues_list.mapToGlobal(pos))
        if action == copy_msg_action:
            clean_text = item.text().lstrip("▲ ").strip()
            QApplication.clipboard().setText(clean_text)

    def _on_sync_services(self) -> None:
        """Trigger background query to Hydrus using active credentials from the page."""
        url = self.hydrus_page.api_url_edit.text().strip()
        key = self.hydrus_page.api_key_edit.text().strip()
        self.state.sync_hydrus_services(api_url=url, api_key=key)

    def _on_services_updated(self, all_tags: dict[str, str], writable_tags: dict[str, str]) -> None:
        """Propagate updated tag services to dependent pages."""
        self.hydrus_page.update_services(all_tags, writable_tags)
        self.models_page.update_services(all_tags, writable_tags)

    def _on_pages_updated(self, pages: list[dict[str, Any]]) -> None:
        """Propagate open Hydrus media pages to the page queries editor."""
        self.hydrus_page.update_pages(pages)

    def _on_connection_changed(self, status: str, info: str) -> None:
        """Update top-bar status badge and button state based on connection health."""
        if status == "connected":
            self.conn_indicator.setText(f"<span style='color: #2e7d32; font-weight: bold;'>● {info}</span>")
            self.conn_indicator.setToolTip("Hydrus connection verified and active")
            self.sync_services_btn.setEnabled(True)
        elif status == "connecting":
            self.conn_indicator.setText(f"<span style='color: #f57c00;'>◌ {info}</span>")
            self.conn_indicator.setToolTip("Connecting to Hydrus API...")
            self.sync_services_btn.setEnabled(False)
        elif status == "error":
            short_info = info if len(info) <= 40 else f"{info[:37]}..."
            self.conn_indicator.setText(f"<span style='color: #d32f2f; font-weight: bold;'>▲ {short_info}</span>")
            self.conn_indicator.setToolTip(info)
            self.sync_services_btn.setEnabled(True)
        else:  # offline
            self.conn_indicator.setText("<span style='color: #888;'>○ Offline</span>")
            self.conn_indicator.setToolTip("Hydrus client is offline or credentials not set")
            self.sync_services_btn.setEnabled(True)

    def _update_title(self) -> None:
        path = self.state.current_path
        path_str = path.name if path else "Untitled Config"
        dirty_str = " *" if self.state.is_dirty else ""
        self.setWindowTitle(f"HyVis Configurator — {path_str}{dirty_str}")

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
            ok, err = self.state.load_from_file(file_path)
            if not ok and err:
                QMessageBox.critical(
                    self,
                    "Failed to Open Configuration",
                    f"Could not read or parse '{Path(file_path).name}':\n\n{err}",
                )

    def _on_save_config(self) -> bool:
        if self.state.current_path is None:
            return self._on_save_as_config()

        data = self._gather_config_dict()
        try:
            validated = AppConfig.model_validate(data)
            self.state.update_config(validated)
        except Exception:
            # Allow saving premature / work-in-progress TOMLs without validation hard-blocking
            pass

        try:
            return self.state.save_to_file(raw_data=data)
        except Exception as exc:
            self._show_save_error(exc)
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
        except Exception:
            pass

        try:
            return self.state.save_to_file(path=file_path, raw_data=data)
        except Exception as exc:
            self._show_save_error(exc)
            return False

    def _show_save_error(self, exc: Exception) -> None:
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setWindowTitle("Failed to Save Configuration")
        msg_box.setText("An error occurred while serializing or saving the configuration file.")
        msg_box.setDetailedText(str(exc))
        msg_box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        msg_box.exec()

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
