"""
unsaved_changes_dialog.py — Confirmation dialog prompting the user when uncommitted changes exist.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)


class UnsavedChangesAction(StrEnum):
    SAVE = "save"
    SAVE_AS = "save_as"
    DISCARD = "discard"
    CANCEL = "cancel"


class UnsavedChangesDialog(QDialog):
    """
    Modal dialog prompting the user when unsaved changes exist.

    Button Layout:
      [ Discard ] (left)  --- stretch ---  [ Save As... ] [ Save ] [ Cancel ] (right)
    """

    def __init__(
        self,
        current_path: Path | None = None,
        *,
        allow_discard: bool = True,
        message: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.action = UnsavedChangesAction.CANCEL
        self._current_path = current_path
        self._allow_discard = allow_discard
        self._message = message

        self.setWindowTitle("Unsaved Changes")
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._setup_ui()

    def _setup_ui(self) -> None:
        style = self.style()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(14)

        # 1. Content row: Standard Question Icon + Text
        content_row = QHBoxLayout()
        content_row.setSpacing(14)

        icon_lbl = QLabel(self)
        icon_pix = style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxQuestion).pixmap(32, 32)
        icon_lbl.setPixmap(icon_pix)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignTop)
        content_row.addWidget(icon_lbl)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)
        path_str = f"'{self._current_path.name}'" if self._current_path else "Untitled Configuration"
        title_lbl = QLabel(f"The configuration {path_str} has unsaved changes.\n", self)
        text_layout.addWidget(title_lbl)

        info_lbl = QLabel(self._message or "What would you like to do before proceeding?", self)
        text_layout.addWidget(info_lbl)

        content_row.addLayout(text_layout)
        root.addLayout(content_row)

        # 2. Buttons row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        # 2a. Left: Discard
        if self._allow_discard:
            discard_btn = QPushButton("Discard", self)
            discard_btn.setIcon(
                QIcon.fromTheme("edit-delete", style.standardIcon(QStyle.StandardPixmap.SP_DialogDiscardButton))
            )
            discard_btn.clicked.connect(self._on_discard)
            btn_row.addWidget(discard_btn)

        # 2b. Middle: Space separating Discard from Save/Cancel
        btn_row.addStretch(1)

        # 2c. Right: Save As...
        save_as_btn = QPushButton("Save As...", self)
        save_as_btn.setIcon(
            QIcon.fromTheme("document-save-as", style.standardIcon(QStyle.StandardPixmap.SP_DriveFDIcon))
        )
        save_as_btn.clicked.connect(self._on_save_as)
        btn_row.addWidget(save_as_btn)

        # 2d. Right: Save (Default)
        save_btn = QPushButton("Save", self)
        save_btn.setIcon(
            QIcon.fromTheme("document-save", style.standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        )
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        # 2e. Far Right: Cancel
        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.setIcon(
            QIcon.fromTheme("dialog-cancel", style.standardIcon(QStyle.StandardPixmap.SP_DialogCancelButton))
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        root.addLayout(btn_row)

    def _on_discard(self) -> None:
        self.action = UnsavedChangesAction.DISCARD
        self.accept()

    def _on_save_as(self) -> None:
        self.action = UnsavedChangesAction.SAVE_AS
        self.accept()

    def _on_save(self) -> None:
        self.action = UnsavedChangesAction.SAVE
        self.accept()


def prompt_unsaved_changes(
    parent: QWidget,
    current_path: Path | None = None,
    *,
    allow_discard: bool = True,
    message: str | None = None,
) -> UnsavedChangesAction:
    """Convenience helper to construct and run the UnsavedChangesDialog."""
    dialog = UnsavedChangesDialog(
        current_path=current_path,
        allow_discard=allow_discard,
        message=message,
        parent=parent,
    )
    dialog.exec()
    return dialog.action
