"""
launch_dialog.py — Preflight verification and launch dialog.
Checks Hydrus connectivity, candidate file count, and preview page status before spawning terminal.
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

logger = logging.getLogger(__name__)


class _PreflightSignals(QObject):
    success = Signal(dict)
    error = Signal(str)


class _PreflightWorker(QRunnable):
    """Queries Hydrus candidate counts and preview page status in background."""

    def __init__(self, cfg: AppConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self.signals = _PreflightSignals()

    def run(self) -> None:
        try:
            hydrus = HydrusClient(self.cfg.hydrus.api_url, self.cfg.hydrus.api_key)
            hydrus.verify_connection()

            v_info = hydrus.get_version_info()
            v_str = str(
                v_info.get("hydrus_version") or v_info.get("client_version") or v_info.get("version") or "unknown"
            )

            # 1. Candidate file counts
            raw_hashes: set[str] = set()
            if self.cfg.hydrus.tag_queries:
                tq_hashes, _ = hydrus.collect_candidate_hashes(self.cfg.hydrus.tag_queries)
                raw_hashes |= tq_hashes
            if self.cfg.hydrus.page_queries:
                pq_hashes, _ = hydrus.collect_page_hashes(self.cfg.hydrus.page_queries)
                raw_hashes |= pq_hashes

            # 2. Preview page status
            preview_status = "disabled"
            preview_page_name = None
            preview_num_files = 0

            p = self.cfg.hydrus.preview
            if p and p.page_name:
                preview_page_name = p.page_name
                root_pages = hydrus.get_pages()
                keys = hydrus._find_pages_by_name(root_pages.get("pages", {}), p.page_name)
                if keys:
                    target_key = keys[p.page_index if p.page_index is not None and p.page_index < len(keys) else 0]
                    p_info = hydrus.get_page_info(target_key, simple=True)
                    media = p_info.get("media") or p_info.get("page_info", {}).get("media") or {}
                    preview_num_files = int(media.get("num_files", 0))
                    if preview_num_files > 0:
                        preview_status = "dirty"
                    else:
                        preview_status = "ready"
                else:
                    preview_status = "will_create"

            self.signals.success.emit(
                {
                    "hydrus_version": v_str,
                    "api_url": self.cfg.hydrus.api_url,
                    "candidate_count": len(raw_hashes),
                    "candidate_hashes": sorted(raw_hashes),
                    "preview_status": preview_status,
                    "preview_page_name": preview_page_name,
                    "preview_num_files": preview_num_files,
                }
            )
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

        self._candidate_hashes: list[str] = []
        self._preview_sent = False
        self._preview_status = "disabled"

        self.setWindowTitle("Launch HyVis")
        self.resize(580, 370)
        self.setMinimumWidth(500)
        self.setModal(True)

        self._setup_ui()
        self._run_preflight()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # 1. Connection Header
        self.conn_label = QLabel("◌ Verifying Hydrus connection...", self)
        self.conn_label.setStyleSheet("font-weight: 600; color: #fbbf24;")
        layout.addWidget(self.conn_label)

        # 2. Candidate Files SectionCard
        self.files_card = SectionCard("Candidate Files", parent=self)
        files_layout = QVBoxLayout()
        files_layout.setContentsMargins(0, 0, 0, 0)
        self.files_label = QLabel("Checking queries...", self.files_card)
        files_layout.addWidget(self.files_label)
        self.files_card.setContentLayout(files_layout)
        layout.addWidget(self.files_card)

        # 3. Client Preview SectionCard (Only if configured)
        p = self.cfg.hydrus.preview
        self.has_preview = bool(p and (p.page_name or p.rejected_page_name))

        self.preview_card = SectionCard("Client Preview", parent=self)
        prev_card_layout = QVBoxLayout()
        prev_card_layout.setContentsMargins(0, 0, 0, 0)
        prev_card_layout.setSpacing(8)

        self.preview_status_label = QLabel("Checking preview page...", self.preview_card)
        prev_card_layout.addWidget(self.preview_status_label)

        prev_btn_row = QHBoxLayout()
        prev_btn_row.setSpacing(8)

        self.send_preview_btn = QPushButton("👁 Send Preview to Hydrus", self.preview_card)
        self.send_preview_btn.setEnabled(False)
        self.send_preview_btn.clicked.connect(self._on_send_preview_clicked)
        prev_btn_row.addWidget(self.send_preview_btn)

        self.recheck_btn = QPushButton("⟳ Re-check", self.preview_card)
        self.recheck_btn.setVisible(False)
        self.recheck_btn.clicked.connect(self._run_preflight)
        prev_btn_row.addWidget(self.recheck_btn)
        prev_btn_row.addStretch(1)

        prev_card_layout.addLayout(prev_btn_row)
        self.preview_card.setContentLayout(prev_card_layout)
        self.preview_card.setVisible(self.has_preview)
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
            "Tip: Once local file paths are resolved, Hydrus can be closed during inference if you need to free system resources.",
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
        self.conn_label.setText("◌ Checking Hydrus status...")
        self.conn_label.setStyleSheet("font-weight: 600; color: #fbbf24;")
        self.launch_btn.setEnabled(False)
        self.send_preview_btn.setEnabled(False)
        self.recheck_btn.setVisible(False)

        worker = _PreflightWorker(self.cfg)
        worker.signals.success.connect(self._on_preflight_success)
        worker.signals.error.connect(self._on_preflight_error)
        QThreadPool.globalInstance().start(worker)

    def _on_preflight_success(self, data: dict[str, Any]) -> None:
        v_str = data["hydrus_version"]
        url = data["api_url"]
        count = data["candidate_count"]
        self._candidate_hashes = data["candidate_hashes"]
        self._preview_status = data["preview_status"]
        num_files = data["preview_num_files"]
        page_name = data["preview_page_name"]

        # Connection header
        self.conn_label.setText(f"● Connected to Hydrus v{v_str} at {url}")
        self.conn_label.setStyleSheet("font-weight: 600; color: #34d399;")

        # Candidate files
        if count == 0:
            self.files_label.setText(
                "<span style='color: #fbbf24;'>No candidate files matched your queries. Nothing to process.</span>"
            )
            self.launch_btn.setEnabled(False)
            return
        else:
            self.files_label.setText(f"<b>{count}</b> candidate file{'s' if count != 1 else ''} match your queries.")

        # Preview status (informational only; never blocks launch because terminal always runs --no-preview)
        if self.has_preview:
            if self._preview_sent:
                self.preview_status_label.setText(
                    f"<span style='color: #34d399;'>✓ Preview populated on '{page_name}'.</span>"
                )
                self.send_preview_btn.setEnabled(False)
                self.recheck_btn.setVisible(False)
            elif self._preview_status == "dirty":
                self.preview_status_label.setText(
                    f"<span style='color: #fbbf24;'>⚠ Page '{page_name}' contains {num_files} files. Clear it before sending a new preview.</span>"
                )
                self.send_preview_btn.setEnabled(False)
                self.recheck_btn.setVisible(True)
            elif self._preview_status == "ready":
                self.preview_status_label.setText(
                    f"<span style='color: #34d399;'>Page '{page_name}' is empty and ready.</span>"
                )
                self.send_preview_btn.setEnabled(True)
                self.recheck_btn.setVisible(False)
            elif self._preview_status == "will_create":
                self.preview_status_label.setText(
                    f"<span style='color: #888;'>Page '{page_name}' will be created in Hydrus (v676+).</span>"
                )
                self.send_preview_btn.setEnabled(True)
                self.recheck_btn.setVisible(False)

        # Launch is always enabled as long as candidate files exist
        self.launch_btn.setEnabled(True)

    def _on_preflight_error(self, err_msg: str) -> None:
        self.conn_label.setText("▲ Could not reach Hydrus Client API")
        self.conn_label.setStyleSheet("font-weight: 600; color: #f85149;")
        self.files_label.setText(f"<span style='color: #f85149;'>{err_msg}</span>")
        self.launch_btn.setEnabled(False)

    def _on_send_preview_clicked(self) -> None:
        p = self.cfg.hydrus.preview
        if not p or not p.page_name or not self._candidate_hashes:
            return

        self.send_preview_btn.setEnabled(False)
        self.send_preview_btn.setText("Sending...")

        try:
            hydrus = HydrusClient(self.cfg.hydrus.api_url, self.cfg.hydrus.api_key)
            hydrus.setup_preview_page(p.page_name, self._candidate_hashes, p.page_index, focus=True)
            self._preview_sent = True
            self.send_preview_btn.setText("✓ Preview Sent")
            self.preview_status_label.setText(
                f"<span style='color: #34d399;'>✓ {len(self._candidate_hashes)} files sent to '{p.page_name}'.</span>"
            )
            self.recheck_btn.setVisible(False)
            self.launch_btn.setEnabled(True)
        except HydrusError as exc:
            QMessageBox.warning(self, "Preview Setup Failed", str(exc))
            self.send_preview_btn.setText("👁 Send Preview to Hydrus")
            self.send_preview_btn.setEnabled(True)

    def _build_effective_args(self) -> list[str]:
        args: list[str] = []
        if not self.prompt_chk.isChecked():
            args.append("--yes")
        # Terminal launched from UI always skips previewing (handled separately via the dialog)
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
