"""
launch_dialog.py — Preflight verification and launch dialog.
Checks for unfinished runs, Hydrus connectivity, candidate & rejected counts,
and dual preview pages before spawning terminal.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hyvis.config import AppConfig, is_video_mime
from hyvis.db import (
    ActiveRunInspection,
    ClientBindingInspection,
    RunMode,
    hash_api_key,
    inspect_active_run_readonly,
    inspect_client_binding_readonly,
)
from hyvis.gui.base_widgets import Divider
from hyvis.gui.launcher import format_cli_command_str, launch_in_external_terminal
from hyvis.gui.theme import CardTheme, ThemeSubscriberMixin
from hyvis.gui.widgets import SectionCard
from hyvis.hydrus import HydrusClient, HydrusError
from hyvis.preflight import PreflightResult, inspect_preview_page, run_preflight

logger = logging.getLogger(__name__)


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        self.clicked.emit()
        super().mousePressEvent(ev)


class _PreflightSignals(QObject):
    progress = Signal(str, object, object)  # (stage_message, current, total)
    success = Signal(object)  # PreflightResult
    error = Signal(str)


class _PreflightWorker(QRunnable):
    """Executes unified preflight collection and MIME filtering in a background thread."""

    def __init__(self, cfg: AppConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self.signals = _PreflightSignals()

    def run(self) -> None:
        try:
            result = run_preflight(
                self.cfg,
                progress_callback=lambda stage, cur, tot: self.signals.progress.emit(stage, cur, tot),
            )
            self.signals.success.emit(result)
        except Exception as exc:
            self.signals.error.emit(str(exc))


class _ResumeCheckSignals(QObject):
    success = Signal(object)  # PreflightResult (connection + preview check only)
    error = Signal(str)


class _ResumeCheckWorker(QRunnable):
    """Lightweight worker for resume mode: verifies Hydrus, services, & preview pages without running search queries."""

    def __init__(self, cfg: AppConfig, active: ActiveRunInspection) -> None:
        super().__init__()
        self.cfg = cfg
        self.active = active
        self.signals = _ResumeCheckSignals()

    def run(self) -> None:
        try:
            client = HydrusClient(self.cfg.hydrus.api_url, self.cfg.hydrus.api_key)
            client.verify_connection()
            v_info = client.get_version_info()
            v_str = str(
                v_info.get("hydrus_version") or v_info.get("client_version") or v_info.get("version") or "unknown"
            )
            api_str = str(v_info.get("api_version") or v_info.get("version") or "unknown")

            service_name_by_key: dict[str, str] = {}
            try:
                raw_services = client.get_services().get("services", {})
                service_name_by_key = {k: str(v.get("name", k)) for k, v in raw_services.items()}
            except Exception:
                pass

            p = self.cfg.hydrus.preview
            cp = inspect_preview_page(client, p.page_name, p.page_index) if p and p.page_name else None
            rp = (
                inspect_preview_page(client, p.rejected_page_name, p.rejected_page_index)
                if p and p.rejected_page_name
                else None
            )

            res = PreflightResult(
                hydrus_version=v_str,
                api_version=api_str,
                service_name_by_key=service_name_by_key,
                candidate_hashes=list(self.active.pending_hashes),
                rejected_hashes=list(self.active.rejected_hashes),
                rejected_mimes=set(self.active.rejected_mimes),
                candidate_preview=cp,
                rejected_preview=rp,
            )
            self.signals.success.emit(res)
        except Exception as exc:
            self.signals.error.emit(str(exc))


def _parse_hydrus_version_num(v_str: str) -> int | None:
    """Extract numeric version from strings like '676', 'v676', or '676.1'."""
    clean = str(v_str).strip().lstrip("vV")
    parts = clean.split(".")
    try:
        return int(parts[0])
    except (ValueError, IndexError):
        return None


class _PreviewCheckSignals(QObject):
    success = Signal(object, object)  # (candidate_preview, rejected_preview)
    error = Signal(str)


class _PreviewCheckWorker(QRunnable):
    """Fast background worker that strictly checks preview page status without re-fetching candidate files."""

    def __init__(self, cfg: AppConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self.signals = _PreviewCheckSignals()

    def run(self) -> None:
        try:
            client = HydrusClient(self.cfg.hydrus.api_url, self.cfg.hydrus.api_key)
            p = self.cfg.hydrus.preview
            cp = inspect_preview_page(client, p.page_name, p.page_index) if p and p.page_name else None
            rp = (
                inspect_preview_page(client, p.rejected_page_name, p.rejected_page_index)
                if p and p.rejected_page_name
                else None
            )
            self.signals.success.emit(cp, rp)
        except Exception as exc:
            self.signals.error.emit(str(exc))


class LaunchDialog(ThemeSubscriberMixin, QDialog):
    """Modal preflight review and terminal launcher."""

    def __init__(
        self,
        config: AppConfig,
        config_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent=parent)
        self.cfg = config
        self.config_path = config_path

        raw_db = Path(self.cfg.database.path)
        self._db_path = raw_db if raw_db.is_absolute() else (Path.cwd() / raw_db)
        self._active_run: ActiveRunInspection | None = inspect_active_run_readonly(self._db_path)
        self._client_binding: ClientBindingInspection | None = inspect_client_binding_readonly(self._db_path)
        self._has_client_mismatch: bool = False

        self._fresh_result: PreflightResult | None = None
        self._resume_result: PreflightResult | None = None
        self._result: PreflightResult | None = None
        self._conn_error_msg: str | None = None
        self._candidate_preview_sent = False
        self._rejected_preview_sent = False

        self.setWindowTitle("Launch HyVis")
        self.resize(570, 650 if self._active_run else 610)
        self.setMinimumWidth(505)
        self.setModal(True)

        self._setup_ui()

        if self._active_run is not None:
            self._run_resume_check()
        else:
            self._run_preflight()

    def refresh_theme(self) -> None:
        """Automatically called by ThemeSubscriberMixin when theme changes."""
        self._sync_theme_labels()
        if self._result:
            self._update_preview_ui()

    def _active_run_action(self) -> str:
        if self._active_run is None or not hasattr(self, "active_run_action_combo"):
            return "fresh"
        return str(self.active_run_action_combo.currentData() or "resume")

    def _effective_cfg_for_preview(self) -> AppConfig:
        if self._active_run is not None and self._active_run_action() == "resume":
            try:
                return AppConfig.from_toml_string(self._active_run.run.config_toml)
            except Exception:
                pass
        return self.cfg

    def _setup_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. Connection Header & Progress Status
        self.conn_label = QLabel("◌ Connecting to Hydrus...", self)
        self.conn_label.setContentsMargins(12, 12, 12, 0)
        self.conn_label.setStyleSheet(f"font-weight: 600; color: {self.tokens.status_warning};")
        root_layout.addWidget(self.conn_label)

        # 2. Main Scroll Area for Cards and Options
        from hyvis.gui.base_widgets import AutoResizingTextEdit, SmoothScrollArea

        scroll = SmoothScrollArea(self, scroll_margin=(8, 4))
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        scroll_container = QWidget(self)
        scroll_layout = QVBoxLayout(scroll_container)
        scroll_layout.setContentsMargins(12, 0, 8, 0)
        scroll_layout.setSpacing(0)
        scroll_layout.addWidget(scroll)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 12, 0, 12)
        layout.setSpacing(12)

        # 0. Unfinished Active Run Card (Shown only when hyvis.db contains active_run)
        if self._active_run is not None:
            self.active_run_card = SectionCard(
                "⚠ Unfinished Run Detected",
                theme=CardTheme.WARNING_VIVID,
                parent=container,
            )
            ar_layout = QVBoxLayout()
            ar_layout.setContentsMargins(0, 0, 0, 0)
            ar_layout.setSpacing(8)

            run_info = self._active_run.run
            orig_cfg_str = Path(run_info.config_path).name if run_info.config_path else "(embedded snapshot)"
            pending_n = len(self._active_run.pending_hashes)
            done_n = self._active_run.done_count
            rej_n = len(self._active_run.rejected_hashes)

            ar_details = QLabel(
                f"<b>Database:</b> {self.cfg.database.path}<br>"
                f"<b>Started:</b> {run_info.started_at} &nbsp;|&nbsp; <b>Config:</b> {orig_cfg_str}<br>"
                f"<b>Inference:</b> {done_n} queued, <b>{pending_n} remaining</b>, {rej_n} rejected<br>"
                f"<span style='color: {self.tokens.text_secondary};'>"
                "If HyVis is currently running in another terminal, wait for it to finish before launching here."
                "</span>",
                self.active_run_card,
            )
            ar_details.setWordWrap(True)
            ar_details.setStyleSheet("border: none; background: transparent;")
            ar_layout.addWidget(ar_details)

            action_row = QHBoxLayout()
            action_row.setSpacing(8)
            action_lbl = QLabel("<b>Action:</b>", self.active_run_card)
            action_lbl.setStyleSheet("border: none; background: transparent;")
            action_row.addWidget(action_lbl)

            self.active_run_action_combo = QComboBox(self.active_run_card)
            self.active_run_action_combo.addItem("▶ Resume Unfinished Run (--resume)", userData="resume")
            self.active_run_action_combo.addItem(
                "🗑 Discard Unfinished Run & Start New (--discard-run)", userData="discard"
            )
            self.active_run_action_combo.currentIndexChanged.connect(self._on_active_run_action_changed)
            action_row.addWidget(self.active_run_action_combo, stretch=1)

            ar_layout.addLayout(action_row)
            self.active_run_card.setContentLayout(ar_layout)
            layout.addWidget(self.active_run_card)

        # 0b. Hydrus Client Binding Mismatch Card (Shown when connected client != bound client in hyvis.db)
        self.client_mismatch_card = SectionCard(
            "⚠ Hydrus Client Mismatch",
            theme=CardTheme.WARNING_VIVID,
            parent=container,
        )
        cm_layout = QVBoxLayout()
        cm_layout.setContentsMargins(0, 0, 0, 0)
        cm_layout.setSpacing(8)

        self.client_mismatch_label = QLabel(self.client_mismatch_card)
        self.client_mismatch_label.setWordWrap(True)
        self.client_mismatch_label.setStyleSheet("border: none; background: transparent;")
        cm_layout.addWidget(self.client_mismatch_label)

        # Word-wrapped checkbox row (prevents long label from forcing horizontal scrollbar)
        chk_row = QHBoxLayout()
        chk_row.setContentsMargins(0, 0, 0, 0)
        chk_row.setSpacing(8)

        self.update_binding_chk = QCheckBox(self.client_mismatch_card)
        self.update_binding_chk.setChecked(False)
        self.update_binding_chk.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.update_binding_chk.toggled.connect(self._on_update_binding_toggled)
        chk_row.addWidget(self.update_binding_chk, 0, Qt.AlignmentFlag.AlignTop)

        # checkbox with qlabel attached so word wrapping happens
        self.update_binding_lbl = _ClickableLabel(
            "Update stored client binding to this Hydrus client and proceed (--update-client-binding)",
            self.client_mismatch_card,
        )
        self.update_binding_lbl.setWordWrap(True)
        self.update_binding_lbl.setStyleSheet("border: none; background: transparent;")
        self.update_binding_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_binding_lbl.clicked.connect(self.update_binding_chk.toggle)
        chk_row.addWidget(self.update_binding_lbl, 1)

        cm_layout.addLayout(chk_row)

        self.client_mismatch_card.setContentLayout(cm_layout)
        self.client_mismatch_card.setVisible(False)
        layout.addWidget(self.client_mismatch_card)

        # Candidate Files SectionCard
        self.files_card = SectionCard("Candidate Files", parent=container)
        files_layout = QVBoxLayout()
        files_layout.setContentsMargins(0, 0, 0, 0)
        self.files_label = QLabel("Checking queries...", self.files_card)
        self.files_label.setWordWrap(True)
        files_layout.addWidget(self.files_label)
        self.files_card.setContentLayout(files_layout)
        layout.addWidget(self.files_card)

        # Client Preview SectionCard (Dual preview targets)
        p = self.cfg.hydrus.preview
        self.has_candidate_preview = bool(p and p.page_name)
        self.has_rejected_preview = bool(p and p.rejected_page_name)
        self.has_any_preview = self.has_candidate_preview or self.has_rejected_preview

        self.preview_card = SectionCard("Client Previews", parent=container)

        # Top-right header re-check button
        self.recheck_btn = QPushButton("⟳ Re-check", self.preview_card)
        self.recheck_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.recheck_btn.setToolTip("Re-query Hydrus to check preview page status and file counts")
        self.recheck_btn.clicked.connect(self._recheck_preview_pages)
        self.preview_card.addHeaderWidget(self.recheck_btn)

        prev_layout = QVBoxLayout()
        prev_layout.setContentsMargins(0, 0, 0, 0)
        prev_layout.setSpacing(10)

        # Row A: Candidate Preview
        if self.has_candidate_preview:
            cand_box = QWidget(self.preview_card)
            cand_box_layout = QVBoxLayout(cand_box)
            cand_box_layout.setContentsMargins(0, 0, 0, 0)
            cand_box_layout.setSpacing(4)

            self.cand_status_label = QLabel("Checking candidate preview page...", cand_box)
            cand_box_layout.addWidget(self.cand_status_label)

            self.send_cand_btn = QPushButton("👁 Send Candidate Preview", cand_box)
            self.send_cand_btn.setEnabled(False)
            self.send_cand_btn.clicked.connect(self._on_send_candidate_clicked)
            cand_box_layout.addWidget(self.send_cand_btn)

            prev_layout.addWidget(cand_box)

        # Divider between previews if both configured
        if self.has_candidate_preview and self.has_rejected_preview:
            p_div = Divider(self.preview_card, QFrame.Shape.HLine)
            prev_layout.addWidget(p_div)

        # Row B: Rejected Preview
        if self.has_rejected_preview:
            rej_box = QWidget(self.preview_card)
            rej_box_layout = QVBoxLayout(rej_box)
            rej_box_layout.setContentsMargins(0, 0, 0, 0)
            rej_box_layout.setSpacing(4)

            self.rej_status_label = QLabel("Checking rejected preview page...", rej_box)
            rej_box_layout.addWidget(self.rej_status_label)

            self.send_rej_btn = QPushButton("👁 Send Rejected Preview", rej_box)
            self.send_rej_btn.setEnabled(False)
            self.send_rej_btn.clicked.connect(self._on_send_rejected_clicked)
            rej_box_layout.addWidget(self.send_rej_btn)

            prev_layout.addWidget(rej_box)

        self.preview_card.setContentLayout(prev_layout)
        self.preview_card.setVisible(self.has_any_preview)
        layout.addWidget(self.preview_card)

        # Execution Option Checkbox
        self.prompt_chk = QCheckBox("Prompt for confirmation before inference", container)
        self.prompt_chk.setChecked(False)  # Unchecked by default -> runs --yes
        self.prompt_chk.setToolTip(
            "When enabled, the terminal displays the full operation summary and pauses for an ENTER keypress "
            "after resolving file paths, giving you a manual breakpoint before model inference begins.\n\n"
            "When disabled (default), the CLI runs with '--yes' and proceeds immediately to model execution."
        )
        self.prompt_chk.toggled.connect(self._on_prompt_chk_toggled)
        layout.addWidget(self.prompt_chk)

        # Informational Tip
        tip_label = QLabel(
            "Tip: Once local file paths are resolved after launching, Hydrus can be closed during inference if you need to free system resources.",
            container,
        )
        tip_label.setObjectName("dialog_tip")
        tip_label.setWordWrap(True)
        layout.addWidget(tip_label)

        # 3. Execution Command SectionCard
        self.cmd_card = SectionCard("Execution Command", parent=container)

        self.manual_cmd_chk = QCheckBox("Edit Directly (Advanced)", self.cmd_card)
        self.manual_cmd_chk.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.manual_cmd_chk.setToolTip("Unlock the command text box to customize paths or pass arbitrary CLI flags.")
        self.manual_cmd_chk.toggled.connect(self._on_manual_cmd_toggled)
        self.cmd_card.addHeaderWidget(self.manual_cmd_chk)

        cmd_layout = QVBoxLayout()
        cmd_layout.setContentsMargins(0, 0, 0, 0)
        cmd_layout.setSpacing(6)

        # Vivid Warning Card (Shown only when manually editing)
        self.manual_cmd_warning = SectionCard(theme=CardTheme.WARNING_VIVID, parent=self.cmd_card)
        warn_layout = QVBoxLayout()
        warn_layout.setContentsMargins(6, 4, 6, 4)
        warn_lbl = QLabel(
            "<b>Editing command:</b> Please verify arguments carefully.",
            self.manual_cmd_warning,
        )
        warn_lbl.setStyleSheet("border: none; background: transparent;")
        warn_lbl.setWordWrap(True)
        warn_layout.addWidget(warn_lbl)
        self.manual_cmd_warning.setContentLayout(warn_layout)
        self.manual_cmd_warning.setVisible(False)
        cmd_layout.addWidget(self.manual_cmd_warning)

        # Auto-resizing Word-wrapped Command Text Box
        from PySide6.QtGui import QFontDatabase

        self.cmd_edit = AutoResizingTextEdit(self.cmd_card)
        self.cmd_edit.setObjectName("cmd_edit")
        self.cmd_edit.setReadOnly(True)

        fixed_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        fixed_font.setPointSize(10)
        self.cmd_edit.setFont(fixed_font)
        cmd_layout.addWidget(self.cmd_edit)

        self.cmd_card.setContentLayout(cmd_layout)
        layout.addWidget(self.cmd_card)

        layout.addStretch(1)
        scroll.setWidget(container)
        root_layout.addWidget(scroll_container, stretch=1)

        # Divider separating the preflight details from the action buttons
        actions_divider = Divider(self, QFrame.Shape.HLine)
        root_layout.addWidget(actions_divider)

        # 5. Bottom Action Bar
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(12, 12, 12, 12)
        btn_row.setSpacing(10)

        self.copy_cmd_btn = QPushButton("Copy Command", self)
        self.copy_cmd_btn.clicked.connect(self._on_copy_command)
        btn_row.addWidget(self.copy_cmd_btn)

        btn_row.addStretch(1)

        self.cancel_btn = QPushButton("Cancel", self)
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)

        self.launch_btn = QPushButton("Launch in Terminal", self)
        self.launch_btn.setStyleSheet("font-weight: 600;")
        self.launch_btn.setEnabled(False)
        self.launch_btn.setDefault(True)
        self.launch_btn.clicked.connect(self._on_launch_clicked)
        btn_row.addWidget(self.launch_btn)

        root_layout.addLayout(btn_row)

        # Initial command text population
        self._sync_command_text()

    def _sync_theme_labels(self) -> None:
        if self._conn_error_msg is not None:
            self.conn_label.setStyleSheet(f"font-weight: 600; color: {self.tokens.status_danger};")
        elif not self._result:
            self.conn_label.setStyleSheet(f"font-weight: 600; color: {self.tokens.status_warning};")
        else:
            self.conn_label.setStyleSheet(f"font-weight: 600; color: {self.tokens.status_success};")

    def _build_launch_target(self) -> tuple[Path | None, list[str]]:
        args = self._build_effective_args()
        if self._has_client_mismatch and self.update_binding_chk.isChecked():
            args.append("--update-client-binding")
        if self._active_run is not None:
            action = self._active_run_action()
            if action == "resume":
                return None, ["--resume", "--db", str(self.cfg.database.path), *args]
            if action == "discard":
                return self.config_path, ["--discard-run", *args]
        return self.config_path, args

    def _sync_command_text(self) -> None:
        """Update command text from current settings when not in manual edit mode."""
        if not self.manual_cmd_chk.isChecked():
            target_cfg, extra_args = self._build_launch_target()
            cmd_str = format_cli_command_str(target_cfg, extra_args)
            self.cmd_edit.setPlainText(cmd_str)

    def _on_active_run_action_changed(self) -> None:
        self.update_binding_chk.blockSignals(True)
        self.update_binding_chk.setChecked(False)
        self.update_binding_chk.blockSignals(False)
        self._has_client_mismatch = False
        self.client_mismatch_card.setVisible(False)

        self._sync_command_text()
        action = self._active_run_action()

        if action == "resume":
            if self._resume_result is not None:
                self._apply_preflight_result(self._resume_result, is_resume=True)
            else:
                self._run_resume_check()
        else:
            if self._fresh_result is not None:
                self._apply_preflight_result(self._fresh_result, is_resume=False)
            else:
                self._run_preflight()

    def _on_update_binding_toggled(self, checked: bool) -> None:
        del checked
        self._sync_command_text()
        cand_count = len(self._result.candidate_hashes) if self._result else 0
        mismatch_blocking = self._has_client_mismatch and not self.update_binding_chk.isChecked()
        self.launch_btn.setEnabled(cand_count > 0 and not mismatch_blocking)

    def _update_client_mismatch_ui(self) -> bool:
        """
        Evaluate whether the connected Hydrus client differs from the binding in hyvis.db.
        Returns True if mismatch is currently blocking launch.
        """
        if self._client_binding is None or self._result is None:
            self._has_client_mismatch = False
            self.client_mismatch_card.setVisible(False)
            return False

        eff_cfg = self._effective_cfg_for_preview()
        cur_url = eff_cfg.hydrus.api_url.strip().rstrip("/")
        cur_key_hash = hash_api_key(eff_cfg.hydrus.api_key)
        cur_services = frozenset(self._result.service_name_by_key.keys())

        saved = self._client_binding.binding
        url_changed = saved.api_url != cur_url
        key_changed = saved.api_key_hash != cur_key_hash
        removed_services = saved.service_keys - cur_services
        added_services = cur_services - saved.service_keys

        # Only trigger warning if URL, key, or existing services went missing
        if not url_changed and not key_changed and not removed_services:
            self._has_client_mismatch = False
            self.client_mismatch_card.setVisible(False)
            return False

        self._has_client_mismatch = True
        lines = [f"Database <b>{self.cfg.database.path}</b> was previously used with a different Hydrus client:"]
        if url_changed:
            lines.append(f"• <b>API URL:</b> {saved.api_url} → {cur_url}")
        if key_changed:
            lines.append("• <b>API Key:</b> changed (different Hydrus client or rotated key)")
        if removed_services or added_services:
            lines.append(f"• <b>Services:</b> {len(removed_services)} missing, {len(added_services)} new")

        if self._client_binding.pending_push_count > 0:
            lines.append(
                f"<span style='color: {self.tokens.status_danger}; font-weight: 600;'>"
                f"⚠ push_queue currently holds {self._client_binding.pending_push_count} unpushed file(s) from the previous client!"
                "</span>"
            )

        lines.append(
            f"<span style='color: {self.tokens.text_secondary};'>"
            "If this is a separate Hydrus client (e.g. a test client), change <b>Database File Path</b> "
            "(keeping the same Cache Database File Path) instead of overwriting the binding."
            "</span>"
        )

        self.client_mismatch_label.setText("<br>".join(lines))
        self.client_mismatch_card.setVisible(True)
        self._sync_command_text()
        return not self.update_binding_chk.isChecked()

    def _on_prompt_chk_toggled(self, checked: bool) -> None:
        del checked
        self._sync_command_text()

    def _on_manual_cmd_toggled(self, checked: bool) -> None:
        self.manual_cmd_warning.setVisible(checked)
        self.cmd_edit.setReadOnly(not checked)
        if not checked:
            self._sync_command_text()
        else:
            self.cmd_edit.setFocus()

    def _run_preflight(self) -> None:
        self._conn_error_msg = None
        self.conn_label.setText("◌ Connecting to Hydrus...")
        self.conn_label.setStyleSheet(f"font-weight: 600; color: {self.tokens.status_warning};")
        self.files_label.setText("Checking queries...")
        self.launch_btn.setEnabled(False)
        self.recheck_btn.setEnabled(False)

        if hasattr(self, "send_cand_btn"):
            self.send_cand_btn.setEnabled(False)
        if hasattr(self, "send_rej_btn"):
            self.send_rej_btn.setEnabled(False)

        worker = _PreflightWorker(self.cfg)
        worker.signals.progress.connect(self._on_preflight_progress)
        worker.signals.success.connect(self._on_preflight_success)
        worker.signals.error.connect(self._on_preflight_error)
        QThreadPool.globalInstance().start(worker)

    def _run_resume_check(self) -> None:
        assert self._active_run is not None
        self._conn_error_msg = None
        self.conn_label.setText("◌ Checking Hydrus connection for resume...")
        self.conn_label.setStyleSheet(f"font-weight: 600; color: {self.tokens.status_warning};")
        self._render_resume_files_summary()
        self.launch_btn.setEnabled(False)
        self.recheck_btn.setEnabled(False)

        if hasattr(self, "send_cand_btn"):
            self.send_cand_btn.setEnabled(False)
        if hasattr(self, "send_rej_btn"):
            self.send_rej_btn.setEnabled(False)

        worker = _ResumeCheckWorker(self._effective_cfg_for_preview(), self._active_run)
        worker.signals.success.connect(self._on_resume_check_success)
        worker.signals.error.connect(self._on_resume_check_error)
        QThreadPool.globalInstance().start(worker)

    def _render_resume_files_summary(self) -> None:
        if self._active_run is None:
            return
        cand_count = len(self._active_run.pending_hashes)
        done_count = self._active_run.done_count
        rej_count = len(self._active_run.rejected_hashes)
        msg_lines = [
            (
                f"Resuming unfinished run: <b>{cand_count}</b> file{'s' if cand_count != 1 else ''} remaining "
                f"({done_count} already inferred & queued)."
            )
        ]
        if rej_count > 0:
            mimes_str = (
                ", ".join(sorted(self._active_run.rejected_mimes))
                if self._active_run.rejected_mimes
                else "unsupported MIME"
            )
            msg_lines.append(
                f"<span style='color: {self.tokens.text_secondary};'>{rej_count} rejected file(s) recorded ({mimes_str}).</span>"
            )
        self.files_label.setText("<br>".join(msg_lines))

    def _on_preflight_progress(self, msg: str, cur: Any, tot: Any) -> None:
        if self._active_run_action() == "resume":
            return
        if tot is not None and cur is not None:
            self.conn_label.setText(f"◌ {msg} ({cur}/{tot})")
        else:
            self.conn_label.setText(f"◌ {msg}")
        self.conn_label.setStyleSheet(f"font-weight: 600; color: {self.tokens.status_warning};")

    def _on_preflight_success(self, result: PreflightResult) -> None:
        self._fresh_result = result
        if self._active_run_action() != "resume":
            self._apply_preflight_result(result, is_resume=False)

    def _on_resume_check_success(self, result: PreflightResult) -> None:
        self._resume_result = result
        if self._active_run_action() == "resume":
            self._apply_preflight_result(result, is_resume=True)

    def _apply_preflight_result(self, result: PreflightResult, *, is_resume: bool) -> None:
        self._result = result
        self._conn_error_msg = None
        v_str = result.hydrus_version
        url = self._effective_cfg_for_preview().hydrus.api_url
        cand_count = len(result.candidate_hashes)
        rej_count = len(result.rejected_hashes)

        # Connection header
        self.conn_label.setText(f"● Connected to Hydrus v{v_str} at {url}")
        self.conn_label.setStyleSheet(f"font-weight: 600; color: {self.tokens.status_success};")
        self.recheck_btn.setEnabled(True)

        mismatch_blocking = self._update_client_mismatch_ui()

        if is_resume:
            self._render_resume_files_summary()
            self._update_preview_ui()
            self.launch_btn.setEnabled(cand_count > 0 and not mismatch_blocking)
            return

        # Candidate and Rejected Files summary (fresh run)
        if cand_count == 0 and rej_count == 0:
            self.files_label.setText(
                f"<span style='color: {self.tokens.status_warning};'>No candidate files matched your queries. Nothing to process.</span>"
            )
            self.launch_btn.setEnabled(False)
            return

        multiframe_count = sum(
            1
            for fi in result.candidate_file_infos
            if is_video_mime(fi.mime) or (fi.num_frames is not None and fi.num_frames > 1)
        )
        if self.cfg.video.enabled and multiframe_count > 0:
            static_count = cand_count - multiframe_count
            breakdown = f" ({static_count} static, {multiframe_count} video/animated)"
        else:
            breakdown = ""

        msg_lines = [
            f"<b>{cand_count}</b> candidate file{'s' if cand_count != 1 else ''} will be processed{breakdown}."
        ]
        if rej_count > 0:
            mimes_str = ", ".join(sorted(result.rejected_mimes)) if result.rejected_mimes else "unknown"
            msg_lines.append(
                f"<span style='color: {self.tokens.text_secondary};'>{rej_count} file(s) rejected with unsupported MIME: {mimes_str}</span>"
            )
            if not self.cfg.video.enabled and any(is_video_mime(m) or "(animated)" in m for m in result.rejected_mimes):
                msg_lines.append(
                    f"<span style='color: {self.tokens.text_muted};'>"
                    "Tip: Enable Video &amp; Animation Processing on the Video &amp; Animation page to process these."
                    "</span>"
                )
        self.files_label.setText("<br>".join(msg_lines))

        self._update_preview_ui()
        self.launch_btn.setEnabled(cand_count > 0 and not mismatch_blocking)

    def _on_preflight_error(self, err_msg: str) -> None:
        if self._active_run_action() == "resume":
            return
        self._conn_error_msg = err_msg
        self.conn_label.setText("▲ Could not reach Hydrus Client API")
        self.conn_label.setStyleSheet(f"font-weight: 600; color: {self.tokens.status_danger};")
        self.files_label.setText(f"<span style='color: {self.tokens.status_danger};'>{err_msg}</span>")
        self.launch_btn.setEnabled(False)
        self.recheck_btn.setEnabled(True)

    def _on_resume_check_error(self, err_msg: str) -> None:
        if self._active_run_action() != "resume":
            return
        self._conn_error_msg = err_msg
        is_infer_only = self._active_run is not None and self._active_run.run.mode == RunMode.INFER_ONLY
        self.conn_label.setText("▲ Could not reach Hydrus Client API")
        self.conn_label.setStyleSheet(f"font-weight: 600; color: {self.tokens.status_danger};")
        self.recheck_btn.setEnabled(True)

        # Resuming an --infer-only run can proceed offline using cached file paths
        if is_infer_only and self._active_run and self._active_run.pending_hashes:
            self.launch_btn.setEnabled(True)
        else:
            self.launch_btn.setEnabled(False)

    def _update_preview_ui(self) -> None:
        """Render candidate and rejected preview status labels and buttons."""
        if not self._result:
            return

        cand_count = len(self._result.candidate_hashes)
        rej_count = len(self._result.rejected_hashes)
        v_num = _parse_hydrus_version_num(self._result.hydrus_version)

        # 1. Candidate Preview
        if self.has_candidate_preview and self._result.candidate_preview:
            cp = self._result.candidate_preview
            btn_prefix = "👁 Re-send" if self._candidate_preview_sent else "👁 Send"

            if cp.status == "dirty":
                self.cand_status_label.setText(
                    f"<span style='color: {self.tokens.status_warning};'>⚠ Page '{cp.page_name}' contains {cp.num_files} files. Clear it before sending.</span>"
                )
                self.send_cand_btn.setText(f"{btn_prefix} Candidate Preview ({cand_count} files)")
                self.send_cand_btn.setEnabled(cand_count > 0)

            elif cp.status == "ready":
                self.cand_status_label.setText(
                    f"<span style='color: {self.tokens.status_success};'>Page '{cp.page_name}' is empty and ready.</span>"
                )
                self.send_cand_btn.setText(f"{btn_prefix} Candidate Preview ({cand_count} files)")
                self.send_cand_btn.setEnabled(cand_count > 0)

            elif cp.status == "will_create":
                if v_num is not None and v_num < 676:
                    self.cand_status_label.setText(
                        f"<span style='color: {self.tokens.status_danger};'>⚠ Page '{cp.page_name}' does not exist. Automatic page creation requires Hydrus v676+ (connected: v{self._result.hydrus_version}). Please create this empty page manually in Hydrus.</span>"
                    )
                    self.send_cand_btn.setText(f"👁 Send Candidate Preview ({cand_count} files)")
                    self.send_cand_btn.setEnabled(False)
                else:
                    self.cand_status_label.setText(
                        f"<span style='color: {self.tokens.text_muted};'>Page '{cp.page_name}' will be created.</span>"
                    )
                    self.send_cand_btn.setText(f"{btn_prefix} Candidate Preview ({cand_count} files)")
                    self.send_cand_btn.setEnabled(cand_count > 0)

        # 2. Rejected Preview
        if self.has_rejected_preview and self._result.rejected_preview:
            rp = self._result.rejected_preview
            btn_prefix = "👁 Re-send" if self._rejected_preview_sent else "👁 Send"

            if rp.status == "dirty":
                self.rej_status_label.setText(
                    f"<span style='color: {self.tokens.status_warning};'>⚠ Page '{rp.page_name}' contains {rp.num_files} files. Clear it before sending.</span>"
                )
            elif rp.status == "ready":
                self.rej_status_label.setText(
                    f"<span style='color: {self.tokens.status_success};'>Page '{rp.page_name}' is empty and ready.</span>"
                )
            elif rp.status == "will_create":
                if v_num is not None and v_num < 676:
                    self.rej_status_label.setText(
                        f"<span style='color: {self.tokens.status_danger};'>⚠ Page '{rp.page_name}' does not exist. Automatic page creation requires Hydrus v676+ (connected: v{self._result.hydrus_version}). Please create this empty page manually in Hydrus.</span>"
                    )
                else:
                    self.rej_status_label.setText(
                        f"<span style='color: {self.tokens.text_muted};'>Page '{rp.page_name}' will be created.</span>"
                    )

            if rej_count > 0:
                is_blocked = rp.status == "will_create" and (v_num is not None and v_num < 676)
                self.send_rej_btn.setText(f"{btn_prefix} Rejected Preview ({rej_count} files)")
                self.send_rej_btn.setEnabled(not is_blocked)
            else:
                self.send_rej_btn.setText("No rejected files to preview")
                self.send_rej_btn.setEnabled(False)

    def _recheck_preview_pages(self) -> None:
        """Fast re-query of preview page file counts without re-running candidate collection."""
        if not self._result or not self.has_any_preview:
            return

        self.recheck_btn.setEnabled(False)
        self.recheck_btn.setText("Checking...")

        worker = _PreviewCheckWorker(self._effective_cfg_for_preview())
        worker.signals.success.connect(self._on_preview_check_success)
        worker.signals.error.connect(self._on_preview_check_error)
        QThreadPool.globalInstance().start(worker)

    def _on_preview_check_success(self, cp: Any, rp: Any) -> None:
        if self._result:
            self._result.candidate_preview = cp
            self._result.rejected_preview = rp

            if cp and cp.status == "ready":
                self._candidate_preview_sent = False
            if rp and rp.status == "ready":
                self._rejected_preview_sent = False

            self._update_preview_ui()

        self.recheck_btn.setText("⟳ Re-check")
        self.recheck_btn.setEnabled(True)

    def _on_preview_check_error(self, err_msg: str) -> None:
        self.recheck_btn.setText("⟳ Re-check")
        self.recheck_btn.setEnabled(True)
        QMessageBox.warning(self, "Preview Check Failed", f"Could not check preview pages:\n\n{err_msg}")

    def _on_send_candidate_clicked(self) -> None:
        eff_cfg = self._effective_cfg_for_preview()
        p = eff_cfg.hydrus.preview
        if not p or not p.page_name or not self._result or not self._result.candidate_hashes:
            return

        cand_count = len(self._result.candidate_hashes)
        self.send_cand_btn.setEnabled(False)
        self.send_cand_btn.setText("Sending...")

        try:
            hydrus = HydrusClient(eff_cfg.hydrus.api_url, eff_cfg.hydrus.api_key)
            hydrus.setup_preview_page(p.page_name, self._result.candidate_hashes, p.page_index, focus=True)
            self._candidate_preview_sent = True
            self.cand_status_label.setText(
                f"<span style='color: {self.tokens.status_success};'>✓ {cand_count} files sent to '{p.page_name}'.</span>"
            )
            self.send_cand_btn.setText(f"👁 Re-send Candidate Preview ({cand_count} files)")
            self.send_cand_btn.setEnabled(True)
        except HydrusError as exc:
            QMessageBox.warning(self, "Candidate Preview Setup Failed", str(exc))
            btn_prefix = "👁 Re-send" if self._candidate_preview_sent else "👁 Send"
            self.send_cand_btn.setText(f"{btn_prefix} Candidate Preview ({cand_count} files)")
            self.send_cand_btn.setEnabled(True)

    def _on_send_rejected_clicked(self) -> None:
        eff_cfg = self._effective_cfg_for_preview()
        p = eff_cfg.hydrus.preview
        if not p or not p.rejected_page_name or not self._result or not self._result.rejected_hashes:
            return

        rej_count = len(self._result.rejected_hashes)
        self.send_rej_btn.setEnabled(False)
        self.send_rej_btn.setText("Sending...")

        try:
            hydrus = HydrusClient(eff_cfg.hydrus.api_url, eff_cfg.hydrus.api_key)
            hydrus.setup_preview_page(
                p.rejected_page_name, self._result.rejected_hashes, p.rejected_page_index, focus=True
            )
            self._rejected_preview_sent = True
            self.rej_status_label.setText(
                f"<span style='color: {self.tokens.status_success};'>✓ {rej_count} files sent to '{p.rejected_page_name}'.</span>"
            )
            self.send_rej_btn.setText(f"👁 Re-send Rejected Preview ({rej_count} files)")
            self.send_rej_btn.setEnabled(True)
        except HydrusError as exc:
            QMessageBox.warning(self, "Rejected Preview Setup Failed", str(exc))
            btn_prefix = "👁 Re-send" if self._rejected_preview_sent else "👁 Send"
            self.send_rej_btn.setText(f"{btn_prefix} Rejected Preview ({rej_count} files)")
            self.send_rej_btn.setEnabled(True)

    def _build_effective_args(self) -> list[str]:
        args: list[str] = []
        if not self.prompt_chk.isChecked():
            args.append("--yes")
        # Terminal launched from UI always skips previewing (handled via this dialog)
        args.append("--no-preview")
        return args

    def _on_copy_command(self) -> None:
        cmd_str = self.cmd_edit.toPlainText().strip()
        QApplication.clipboard().setText(cmd_str)

        self.copy_cmd_btn.setText("✓ Copied!")
        QTimer.singleShot(1800, lambda: self.copy_cmd_btn.setText("Copy Command"))

    def _on_launch_clicked(self) -> None:
        # Confirm before clearing an unfinished run
        if self._active_run is not None and self._active_run_action() == "discard":
            pending_n = len(self._active_run.pending_hashes)
            ans = QMessageBox.warning(
                self,
                "Confirm Discard Unfinished Run",
                f"Are you sure you want to discard the unfinished run from {self._active_run.run.started_at}?\n\n"
                f"{pending_n} file(s) had not finished inference yet.\n"
                "Only proceed if no HyVis process is currently running in another terminal.\n\n"
                "This will clear the unfinished run state and start a fresh run with your current configuration.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return

        from hyvis.gui.settings import get_gui_settings, save_gui_settings

        settings = get_gui_settings()
        if settings.prompt_backup_before_launch:
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Icon.Warning)
            msg_box.setWindowTitle("Hydrus Backup Reminder")
            msg_box.setText("It is strongly recommended to create or update your Hydrus backup.")
            msg_box.setInformativeText("Are you ready to proceed with launching?")
            msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            msg_box.setDefaultButton(QMessageBox.StandardButton.Cancel)

            dont_ask_cb = QCheckBox("Don't remind again", msg_box)
            msg_box.setCheckBox(dont_ask_cb)

            if msg_box.exec() != QMessageBox.StandardButton.Yes:
                return

            if dont_ask_cb.isChecked():
                settings.prompt_backup_before_launch = False
                save_gui_settings(settings)

        # Launch using custom edited command or default auto-generated args
        if self.manual_cmd_chk.isChecked():
            import shlex

            raw_str = self.cmd_edit.toPlainText().strip()
            try:
                cmd_parts = shlex.split(raw_str)
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Invalid Command Syntax",
                    f"Could not parse the command line string:\n\n{exc}\n\nCheck for unclosed quotes or syntax errors.",
                )
                return

            if not cmd_parts:
                QMessageBox.warning(self, "Empty Command", "The command line is empty.")
                return

            ok = launch_in_external_terminal(raw_command=cmd_parts)
        else:
            target_cfg, extra_args = self._build_launch_target()
            ok = launch_in_external_terminal(target_cfg, extra_args)

        if ok:
            self.accept()
        else:
            QMessageBox.warning(
                self,
                "Terminal Launch Failed",
                "Could not detect or launch an external terminal window automatically.\n\n"
                "The command has been copied to your clipboard instead.",
            )
