"""
launch_dialog.py — Preflight verification and launch dialog.
Checks Hydrus connectivity, candidate & rejected counts, and dual preview pages before spawning terminal.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hyvis.config import AppConfig
from hyvis.gui.launcher import format_cli_command_str, launch_in_external_terminal
from hyvis.gui.widgets import SectionCard
from hyvis.hydrus import HydrusClient, HydrusError
from hyvis.preflight import PreflightResult, run_preflight

logger = logging.getLogger(__name__)


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
        from hyvis.hydrus import HydrusClient
        from hyvis.preflight import inspect_preview_page

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


class LaunchDialog(QDialog):
    """Modal preflight review and terminal launcher."""

    def __init__(
        self,
        config: AppConfig,
        config_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.cfg = config
        self.config_path = config_path

        self._result: PreflightResult | None = None
        self._candidate_preview_sent = False
        self._rejected_preview_sent = False

        self.setWindowTitle("Launch HyVis")
        self.resize(580, 480)
        self.setMinimumWidth(520)
        self.setModal(True)

        self._setup_ui()
        self._run_preflight()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # 1. Connection Header & Progress Status
        self.conn_label = QLabel("◌ Connecting to Hydrus...", self)
        self.conn_label.setStyleSheet("font-weight: 600; color: #fbbf24;")
        layout.addWidget(self.conn_label)

        # 2. Candidate Files SectionCard
        self.files_card = SectionCard("Candidate Files", parent=self)
        files_layout = QVBoxLayout()
        files_layout.setContentsMargins(0, 0, 0, 0)
        self.files_label = QLabel("Checking queries...", self.files_card)
        self.files_label.setWordWrap(True)
        files_layout.addWidget(self.files_label)
        self.files_card.setContentLayout(files_layout)
        layout.addWidget(self.files_card)

        # 3. Client Preview SectionCard (Dual preview targets)
        p = self.cfg.hydrus.preview
        self.has_candidate_preview = bool(p and p.page_name)
        self.has_rejected_preview = bool(p and p.rejected_page_name)
        self.has_any_preview = self.has_candidate_preview or self.has_rejected_preview

        self.preview_card = SectionCard("Client Previews", parent=self)

        # Top-right header re-check button (checks page statuses only)
        self.recheck_btn = QPushButton("⟳ Re-check", self.preview_card)
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
            p_div = QFrame(self.preview_card)
            p_div.setFrameShape(QFrame.Shape.HLine)
            p_div.setStyleSheet("color: rgba(255, 255, 255, 0.08);")
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

        # 4. Execution Option Checkbox
        self.prompt_chk = QCheckBox("Prompt for confirmation before inference", self)
        self.prompt_chk.setChecked(False)  # Unchecked by default -> runs --yes
        self.prompt_chk.setToolTip(
            "When enabled, the terminal displays the full operation summary and pauses for an ENTER keypress "
            "after resolving file paths, giving you a manual breakpoint before model inference begins.\n\n"
            "When disabled (default), the CLI runs with '--yes' and proceeds immediately to model execution."
        )
        layout.addWidget(self.prompt_chk)

        # 5. Neutral Informational Tip
        tip_label = QLabel(
            "Tip: Once local file paths are resolved after launching, Hydrus can be closed during inference if you need to free system resources.",
            self,
        )
        tip_label.setStyleSheet("color: #8a9ba5;")
        tip_label.setWordWrap(True)
        layout.addWidget(tip_label)

        # Elastic space: absorbs expansion when maximized so cards remain top-anchored
        layout.addStretch(1)

        # 6. Bottom Action Bar
        btn_row = QHBoxLayout()
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
        self.launch_btn.clicked.connect(self._on_launch_clicked)
        btn_row.addWidget(self.launch_btn)

        layout.addLayout(btn_row)

    def _run_preflight(self) -> None:
        self.conn_label.setText("◌ Connecting to Hydrus...")
        self.conn_label.setStyleSheet("font-weight: 600; color: #fbbf24;")
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

    def _on_preflight_progress(self, msg: str, cur: Any, tot: Any) -> None:
        if tot is not None and cur is not None:
            self.conn_label.setText(f"◌ {msg} ({cur}/{tot})")
        else:
            self.conn_label.setText(f"◌ {msg}")
        self.conn_label.setStyleSheet("font-weight: 600; color: #fbbf24;")

    def _on_preflight_success(self, result: PreflightResult) -> None:
        self._result = result
        v_str = result.hydrus_version
        url = self.cfg.hydrus.api_url
        cand_count = len(result.candidate_hashes)
        rej_count = len(result.rejected_hashes)

        # Connection header
        self.conn_label.setText(f"● Connected to Hydrus v{v_str} at {url}")
        self.conn_label.setStyleSheet("font-weight: 600; color: #34d399;")
        self.recheck_btn.setEnabled(True)

        # Candidate and Rejected Files summary
        if cand_count == 0 and rej_count == 0:
            self.files_label.setText(
                "<span style='color: #fbbf24;'>No candidate files matched your queries. Nothing to process.</span>"
            )
            self.launch_btn.setEnabled(False)
            return

        msg_lines = [f"<b>{cand_count}</b> candidate file{'s' if cand_count != 1 else ''} will be processed."]
        if rej_count > 0:
            mimes_str = ", ".join(sorted(result.rejected_mimes)) if result.rejected_mimes else "unknown"
            msg_lines.append(
                f"<span style='color: #8a9ba5;'>{rej_count} file(s) rejected with unsupported MIME: {mimes_str}</span>"
            )
        self.files_label.setText("<br>".join(msg_lines))

        # Update preview cards with version-aware creation checks
        self._update_preview_ui()
        self.launch_btn.setEnabled(cand_count > 0)

    def _on_preflight_error(self, err_msg: str) -> None:
        self.conn_label.setText("▲ Could not reach Hydrus Client API")
        self.conn_label.setStyleSheet("font-weight: 600; color: #f85149;")
        self.files_label.setText(f"<span style='color: #f85149;'>{err_msg}</span>")
        self.launch_btn.setEnabled(False)
        self.recheck_btn.setEnabled(True)

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
                    f"<span style='color: #fbbf24;'>⚠ Page '{cp.page_name}' contains {cp.num_files} files. Clear it before sending.</span>"
                )
                self.send_cand_btn.setText(f"{btn_prefix} Candidate Preview ({cand_count} files)")
                self.send_cand_btn.setEnabled(cand_count > 0)

            elif cp.status == "ready":
                self.cand_status_label.setText(
                    f"<span style='color: #34d399;'>Page '{cp.page_name}' is empty and ready.</span>"
                )
                self.send_cand_btn.setText(f"{btn_prefix} Candidate Preview ({cand_count} files)")
                self.send_cand_btn.setEnabled(cand_count > 0)

            elif cp.status == "will_create":
                if v_num is not None and v_num < 676:
                    self.cand_status_label.setText(
                        f"<span style='color: #f85149;'>⚠ Page '{cp.page_name}' does not exist. Automatic page creation requires Hydrus v676+ (connected: v{self._result.hydrus_version}). Please create this empty page manually in Hydrus.</span>"
                    )
                    self.send_cand_btn.setText(f"👁 Send Candidate Preview ({cand_count} files)")
                    self.send_cand_btn.setEnabled(False)
                else:
                    self.cand_status_label.setText(
                        f"<span style='color: #888;'>Page '{cp.page_name}' will be created.</span>"
                    )
                    self.send_cand_btn.setText(f"{btn_prefix} Candidate Preview ({cand_count} files)")
                    self.send_cand_btn.setEnabled(cand_count > 0)

        # 2. Rejected Preview
        if self.has_rejected_preview and self._result.rejected_preview:
            rp = self._result.rejected_preview
            btn_prefix = "👁 Re-send" if self._rejected_preview_sent else "👁 Send"

            if rp.status == "dirty":
                self.rej_status_label.setText(
                    f"<span style='color: #fbbf24;'>⚠ Page '{rp.page_name}' contains {rp.num_files} files. Clear it before sending.</span>"
                )
            elif rp.status == "ready":
                self.rej_status_label.setText(
                    f"<span style='color: #34d399;'>Page '{rp.page_name}' is empty and ready.</span>"
                )
            elif rp.status == "will_create":
                if v_num is not None and v_num < 676:
                    self.rej_status_label.setText(
                        f"<span style='color: #f85149;'>⚠ Page '{rp.page_name}' does not exist. Automatic page creation requires Hydrus v676+ (connected: v{self._result.hydrus_version}). Please create this empty page manually in Hydrus.</span>"
                    )
                else:
                    self.rej_status_label.setText(
                        f"<span style='color: #888;'>Page '{rp.page_name}' will be created.</span>"
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

        worker = _PreviewCheckWorker(self.cfg)
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
        p = self.cfg.hydrus.preview
        if not p or not p.page_name or not self._result or not self._result.candidate_hashes:
            return

        cand_count = len(self._result.candidate_hashes)
        self.send_cand_btn.setEnabled(False)
        self.send_cand_btn.setText("Sending...")

        try:
            hydrus = HydrusClient(self.cfg.hydrus.api_url, self.cfg.hydrus.api_key)
            hydrus.setup_preview_page(p.page_name, self._result.candidate_hashes, p.page_index, focus=True)
            self._candidate_preview_sent = True
            self.cand_status_label.setText(
                f"<span style='color: #34d399;'>✓ {cand_count} files sent to '{p.page_name}'.</span>"
            )
            self.send_cand_btn.setText(f"👁 Re-send Candidate Preview ({cand_count} files)")
            self.send_cand_btn.setEnabled(True)
        except HydrusError as exc:
            QMessageBox.warning(self, "Candidate Preview Setup Failed", str(exc))
            btn_prefix = "👁 Re-send" if self._candidate_preview_sent else "👁 Send"
            self.send_cand_btn.setText(f"{btn_prefix} Candidate Preview ({cand_count} files)")
            self.send_cand_btn.setEnabled(True)

    def _on_send_rejected_clicked(self) -> None:
        p = self.cfg.hydrus.preview
        if not p or not p.rejected_page_name or not self._result or not self._result.rejected_hashes:
            return

        rej_count = len(self._result.rejected_hashes)
        self.send_rej_btn.setEnabled(False)
        self.send_rej_btn.setText("Sending...")

        try:
            hydrus = HydrusClient(self.cfg.hydrus.api_url, self.cfg.hydrus.api_key)
            hydrus.setup_preview_page(
                p.rejected_page_name, self._result.rejected_hashes, p.rejected_page_index, focus=True
            )
            self._rejected_preview_sent = True
            self.rej_status_label.setText(
                f"<span style='color: #34d399;'>✓ {rej_count} files sent to '{p.rejected_page_name}'.</span>"
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
        extra_args = self._build_effective_args()
        cmd_str = format_cli_command_str(self.config_path, extra_args)
        QApplication.clipboard().setText(cmd_str)

        self.copy_cmd_btn.setText("✓ Copied!")
        QTimer.singleShot(1800, lambda: self.copy_cmd_btn.setText("Copy Command"))

    def _on_launch_clicked(self) -> None:
        extra_args = self._build_effective_args()
        ok = launch_in_external_terminal(self.config_path, extra_args)
        if ok:
            self.accept()
        else:
            QMessageBox.warning(
                self,
                "Terminal Launch Failed",
                "Could not detect or launch an external terminal window automatically.\n\n"
                "The command has been copied to your clipboard instead.",
            )
