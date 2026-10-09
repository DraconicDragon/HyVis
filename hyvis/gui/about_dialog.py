"""
about_dialog.py — About dialog displaying HyVis metadata and environment details.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hyvis.gui.base_widgets import SmoothScrollArea
from hyvis.system_info import format_system_info_text, get_system_info


class AboutDialog(QDialog):
    """Clean, borderless about dialog for HyVis."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About HyVis")
        self.resize(520, 560)
        self.setMinimumSize(440, 400)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._setup_ui()
        self._populate_dependencies()

    def _setup_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 16, 18, 14)
        root_layout.setSpacing(10)

        # 1. Header (Title + Description on one line, followed by Link & License)
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)

        title_desc_lbl = QLabel(
            "<b>HyVis</b> | Local autotagging utility for Hydrus Network.",
            self,
        )
        title_desc_lbl.setWordWrap(True)
        header_layout.addWidget(title_desc_lbl)

        meta_lbl = QLabel(
            '<a href="https://github.com/DraconicDragon/HyVis">GitHub Repository</a> &nbsp;|&nbsp; License: MIT',
            self,
        )
        meta_lbl.setOpenExternalLinks(True)
        header_layout.addWidget(meta_lbl)

        root_layout.addLayout(header_layout)

        # Native divider separating header from dependencies
        divider = QFrame(self)
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        root_layout.addWidget(divider)

        # 2. Smooth Scrollable Details (Borderless)
        self.scroll_area = SmoothScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self.container = QWidget()
        self.content_layout = QVBoxLayout(self.container)
        self.content_layout.setContentsMargins(0, 4, 10, 4)
        self.content_layout.setSpacing(12)

        self.scroll_area.setWidget(self.container)
        root_layout.addWidget(self.scroll_area, stretch=1)

        # 3. Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.copy_btn = QPushButton("Copy Details", self)
        self.copy_btn.setToolTip("Copy system and dependency information to clipboard")
        self.copy_btn.clicked.connect(self._on_copy_details)
        btn_row.addWidget(self.copy_btn)

        btn_row.addStretch(1)

        self.close_btn = QPushButton("Close", self)
        self.close_btn.setDefault(True)
        self.close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.close_btn)

        root_layout.addLayout(btn_row)

    def _populate_dependencies(self) -> None:
        """Render soft-separated dependency groups using aligned, borderless QFormLayouts."""
        groups = get_system_info()

        for group in groups:
            group_box = QWidget(self.container)
            group_vbox = QVBoxLayout(group_box)
            group_vbox.setContentsMargins(0, 0, 0, 0)
            group_vbox.setSpacing(4)

            # Clean group subheader
            group_header = QLabel(f"<b>{group.title}</b>", group_box)
            group_vbox.addWidget(group_header)

            form_layout = QFormLayout()
            form_layout.setContentsMargins(8, 2, 0, 4)
            form_layout.setHorizontalSpacing(14)
            form_layout.setVerticalSpacing(3)

            for item in group.items:
                name_lbl = QLabel(f"{item.name}:", group_box)
                val_lbl = QLabel(group_box)

                # Format git commit hash with subtle grey color without changing font size or weight
                if " (git-" in item.version:
                    base, git_part = item.version.split(" (git-", 1)
                    val_lbl.setText(f"{base} <span style='color: #888;'> (git-{git_part}</span>")
                else:
                    val_lbl.setText(item.version)

                if not item.is_installed:
                    val_lbl.setStyleSheet("color: #777; font-style: italic;")

                # Attach package summary as tooltip on both labels
                if item.summary:
                    name_lbl.setToolTip(item.summary)
                    val_lbl.setToolTip(item.summary)

                form_layout.addRow(name_lbl, val_lbl)

            group_vbox.addLayout(form_layout)
            self.content_layout.addWidget(group_box)

        self.content_layout.addStretch(1)

    def _on_copy_details(self) -> None:
        """Copy the full plain-text environment report to clipboard."""
        text = format_system_info_text()
        QApplication.clipboard().setText(text)
        self.copy_btn.setText("✓ Copied!")
        QTimer.singleShot(1800, lambda: self.copy_btn.setText("Copy Details"))
