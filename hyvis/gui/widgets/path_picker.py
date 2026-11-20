from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic_core import PydanticUndefined
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from hyvis.gui.base_widgets import SemanticLabel
from hyvis.gui.theme import TextRole, ThemeProperty, WidgetState

PathPickerMode = Literal["directory", "open_file", "save_file"]


class PathPickerWidget(QWidget):
    """
    Reusable file/directory picker combining a QLineEdit, Browse button,
    whole-widget drag-and-drop, and a live resolved-path preview label.
    """

    textChanged = Signal(str)

    def __init__(
        self,
        *,
        mode: PathPickerMode = "directory",
        dialog_title: str | None = None,
        file_filter: str = "All Files (*)",
        default_filename: str = "",
        browse_text: str | None = None,
        path_prefix: str = "",
        empty_display: str | Callable[[], str] | None = None,
        custom_resolver: Callable[[str], str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode: PathPickerMode = mode
        self._dialog_title = dialog_title
        self._file_filter = file_filter
        self._default_filename = default_filename
        self._custom_browse_text = browse_text
        self._path_prefix = path_prefix
        self._empty_display = empty_display
        self._custom_resolver = custom_resolver

        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(3)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self.line_edit = QLineEdit(self)
        # Route all drops to PathPickerWidget so QLineEdit never pastes raw file:// URIs
        self.line_edit.setAcceptDrops(False)
        self.line_edit.textChanged.connect(self._on_line_text_changed)
        row.addWidget(self.line_edit, stretch=1)

        btn_label = browse_text or ("Browse Folder..." if mode == "directory" else "Browse...")
        self.browse_btn = QPushButton(btn_label, self)
        self.browse_btn.clicked.connect(self._on_browse_clicked)
        row.addWidget(self.browse_btn)

        root.addLayout(row)

        self.resolved_label = SemanticLabel("", role=TextRole.MUTED, parent=self)
        self.resolved_label.setStyleSheet("font-size: 11px;")
        self.resolved_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.resolved_label)

        self.refresh_resolved_path()

    def text(self) -> str:
        return self.line_edit.text()

    def setText(self, value: str) -> None:
        self.line_edit.setText(value)
        self.refresh_resolved_path()

    def setPlaceholderText(self, text: str) -> None:
        self.line_edit.setPlaceholderText(text)

    def setToolTip(self, tip: str) -> None:
        super().setToolTip(tip)
        self.line_edit.setToolTip(tip)

    def set_highlight_state(self, is_overridden: bool = False, is_error: bool = False) -> None:
        state = WidgetState.ERROR if is_error else (WidgetState.OVERRIDDEN if is_overridden else WidgetState.DEFAULT)
        self.line_edit.setProperty(ThemeProperty.STATE, state)
        style = self.line_edit.style()
        if style:
            style.unpolish(self.line_edit)
            style.polish(self.line_edit)
            self.line_edit.update()

    def apply_field_metadata(
        self,
        field_info: Any,
        *,
        set_placeholder: bool = True,
        apply_default: bool = True,
    ) -> None:
        extra = getattr(field_info, "json_schema_extra", None)
        if isinstance(extra, Mapping) and extra.get("picker") == "directory":
            self.set_mode("directory")
        examples = getattr(field_info, "examples", None)
        if set_placeholder and examples and isinstance(examples[0], (str, int, float)):
            self.setPlaceholderText(str(examples[0]))
        if (
            apply_default
            and getattr(field_info, "default", PydanticUndefined) is not PydanticUndefined
            and isinstance(field_info.default, str)
            and not self.text()
        ):
            self.setText(field_info.default)

    def set_mode(self, mode: PathPickerMode) -> None:
        self._mode = mode
        if self._custom_browse_text is None:
            self.browse_btn.setText("Browse Folder..." if mode == "directory" else "Browse...")

    def refresh_resolved_path(self) -> None:
        """Update the resolved-path preview label using pure lexical path resolution."""
        raw = self.line_edit.text().strip()
        if not raw:
            if isinstance(self._empty_display, str):
                msg = self._empty_display
            elif callable(self._empty_display):
                msg = self._empty_display()
            else:
                msg = "(Empty)"
            self.resolved_label.setText(msg)
            return

        if self._custom_resolver is not None:
            self.resolved_label.setText(self._custom_resolver(raw))
            return

        resolved = os.path.abspath(os.path.expanduser(raw))
        self.resolved_label.setText(f"Resolves to: {resolved}")

    def _on_line_text_changed(self, text: str) -> None:
        self.refresh_resolved_path()
        self.textChanged.emit(text)

    def _initial_dialog_target(self) -> str:
        raw = self.line_edit.text().strip()
        if self._path_prefix and raw.startswith(self._path_prefix):
            raw = raw[len(self._path_prefix) :].strip()

        if raw:
            abs_p = Path(os.path.abspath(os.path.expanduser(raw)))
            if self._mode == "save_file":
                if abs_p.exists() or abs_p.parent.is_dir():
                    return str(abs_p)
            else:
                if abs_p.exists():
                    return str(abs_p)
                if abs_p.parent.is_dir():
                    return str(abs_p.parent)

        if self._mode == "save_file" and self._default_filename:
            return str(Path.cwd() / self._default_filename)
        return str(Path.cwd())

    def _on_browse_clicked(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        target = self._initial_dialog_target()
        if self._mode == "directory":
            title = self._dialog_title or "Select Directory"
            chosen = QFileDialog.getExistingDirectory(self, title, target)
        elif self._mode == "open_file":
            title = self._dialog_title or "Select File"
            chosen, _ = QFileDialog.getOpenFileName(self, title, target, self._file_filter)
        else:
            title = self._dialog_title or "Select File"
            chosen, _ = QFileDialog.getSaveFileName(self, title, target, self._file_filter)

        if chosen:
            formatted = f"{self._path_prefix}{chosen}" if self._path_prefix else chosen
            self.setText(formatted)

    # region Drag & Drop

    def _extract_dropped_path(self, event: QDragEnterEvent | QDropEvent) -> Path | None:
        mime = event.mimeData()
        if not mime or not mime.hasUrls():
            return None
        for url in mime.urls():
            if url.isLocalFile():
                p = Path(url.toLocalFile())
                if self._mode == "directory" and p.is_dir():
                    return p
                if self._mode == "open_file" and p.is_file():
                    return p
                if self._mode == "save_file" and not p.is_dir():
                    return p
                return None
        return None

    def _set_drop_highlight(self, active: bool) -> None:
        self.set_highlight_state(is_overridden=active)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._extract_dropped_path(event) is not None:
            self._set_drop_highlight(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._set_drop_highlight(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_drop_highlight(False)
        dropped = self._extract_dropped_path(event)
        if dropped is None:
            event.ignore()
            return
        formatted = f"{self._path_prefix}{dropped}" if self._path_prefix else str(dropped)
        self.setText(formatted)
        event.acceptProposedAction()

    # endregion
