from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hyvis.gui.base_widgets import SuggestionComboBox

# region String List Editor


class StringListEditor(QWidget):
    """
    A widget for viewing, adding, and removing a list of strings.
    Used for include_tags, exclude_tags, and general tag groups.
    Supports multi-line paste splitting and dynamic height expansion.
    """

    changed = Signal()

    def __init__(
        self,
        placeholder: str = "Enter item...",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 1. List view (compact default height, auto-expands with items)
        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._adjust_height()
        layout.addWidget(self.list_widget)

        # 2. Input and Action Bar
        input_layout = QHBoxLayout()
        input_layout.setSpacing(6)

        self.input_line = QLineEdit(self)
        self.input_line.setPlaceholderText(placeholder)
        self.input_line.returnPressed.connect(self._on_add)
        input_layout.addWidget(self.input_line, stretch=1)

        self.add_btn = QPushButton("Add", self)
        self.add_btn.clicked.connect(self._on_add)
        input_layout.addWidget(self.add_btn)

        self.remove_btn = QPushButton("Remove", self)
        self.remove_btn.clicked.connect(self._on_remove)
        input_layout.addWidget(self.remove_btn)

        layout.addLayout(input_layout)

    def _adjust_height(self) -> None:
        """Starts compact (2-3 items) and expands dynamically up to a maximum cap."""
        count = self.list_widget.count()
        target = max(68, min(68 + max(0, count - 2) * 22, 160))
        self.list_widget.setFixedHeight(target)

    def get_items(self) -> list[str]:
        items: list[str] = []
        for i in range(self.list_widget.count()):
            text = self.list_widget.item(i).text().strip()
            if text:
                items.append(text)
        return items

    def set_items(self, items: Sequence[str]) -> None:
        self.blockSignals(True)
        self.list_widget.clear()
        for item in items:
            cleaned = str(item).strip()
            if cleaned:
                self.list_widget.addItem(QListWidgetItem(cleaned))
        self._adjust_height()
        self.blockSignals(False)

    def _on_add(self) -> None:
        raw_text = self.input_line.text()
        if not raw_text.strip():
            return

        # Split on newlines, strip each tag, discard empty lines
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if not lines:
            return

        existing = set(self.get_items())
        added_any = False
        for tag in lines:
            if tag not in existing:
                self.list_widget.addItem(QListWidgetItem(tag))
                existing.add(tag)
                added_any = True

        self.input_line.clear()
        if added_any:
            self._adjust_height()
            self.changed.emit()

    def _on_remove(self) -> None:
        selected = self.list_widget.selectedItems()
        if not selected:
            return

        for item in selected:
            row = self.list_widget.row(item)
            self.list_widget.takeItem(row)

        self._adjust_height()
        self.changed.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self._on_remove()
        else:
            super().keyPressEvent(event)


# endregion


# region Category Tag Editor


class CategoryTagEditor(QWidget):
    """
    Dynamic category list editor.
    Starts empty by default; populates suggestions dynamically from vibe model metadata.
    Filters out already-added categories from the combobox to prevent duplicates.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._suggestions: list[str] = []
        self._provenance: dict[str, list[str]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 1. List of active categories
        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._adjust_height()
        layout.addWidget(self.list_widget)

        # 2. Add bar with editable combobox
        input_layout = QHBoxLayout()
        input_layout.setSpacing(6)

        self.combo = SuggestionComboBox(self)
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.combo.about_to_show_popup.connect(self._refresh_combo)
        line_edit = self.combo.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText("Select or type category...")
            line_edit.returnPressed.connect(self._on_add)
        input_layout.addWidget(self.combo, stretch=1)

        self.add_btn = QPushButton("+ Add", self)
        self.add_btn.clicked.connect(self._on_add)
        input_layout.addWidget(self.add_btn)

        self.remove_btn = QPushButton("- Remove Selected", self)
        self.remove_btn.clicked.connect(self._on_remove)
        input_layout.addWidget(self.remove_btn)

        layout.addLayout(input_layout)

    def _adjust_height(self) -> None:
        count = self.list_widget.count()
        target = max(68, min(68 + max(0, count - 2) * 22, 160))
        self.list_widget.setFixedHeight(target)

    def _refresh_combo(self) -> None:
        self.combo.blockSignals(True)
        current = self.combo.currentText()
        self.combo.clear()

        # Omit categories that are already in the list
        existing = set(self.get_items())
        available = [c for c in self._suggestions if c not in existing]

        for cat in available:
            self.combo.addItem(cat)
            models = self._provenance.get(cat, [])
            if models:
                self.combo.setItemData(
                    self.combo.count() - 1,
                    f"From: {', '.join(models)}",
                    Qt.ItemDataRole.ToolTipRole,
                )

        if current in available:
            self.combo.setCurrentText(current)
        elif available:
            self.combo.setCurrentText(available[0])
        else:
            self.combo.setCurrentText("")

        self.combo.blockSignals(False)

    def set_suggestions(self, suggestions: Sequence[str] | Mapping[str, Sequence[str]]) -> None:
        if isinstance(suggestions, Mapping):
            s_map = cast(Mapping[str, Sequence[str]], suggestions)
            self._suggestions = sorted(s_map.keys())
            self._provenance = {k: list(v) for k, v in s_map.items()}
        else:
            self._suggestions = sorted(dict.fromkeys(suggestions))
            self._provenance = {}
        self._refresh_combo()

    def get_items(self) -> list[str]:
        items: list[str] = []
        for i in range(self.list_widget.count()):
            text = self.list_widget.item(i).text().strip()
            if text:
                items.append(text)
        return items

    def set_items(self, items: Sequence[str]) -> None:
        self.blockSignals(True)
        self.list_widget.clear()
        for item in items:
            cleaned = str(item).strip()
            if cleaned:
                self.list_widget.addItem(QListWidgetItem(cleaned))
        self._adjust_height()
        self._refresh_combo()
        self.blockSignals(False)

    def _on_add(self) -> None:
        raw_text = self.combo.currentText()
        lines = [line.strip().lower() for line in raw_text.splitlines() if line.strip()]
        if not lines:
            return

        existing = set(self.get_items())
        added_any = False
        for cat in lines:
            if cat not in existing:
                self.list_widget.addItem(QListWidgetItem(cat))
                existing.add(cat)
                added_any = True

        if added_any:
            self._adjust_height()
            self._refresh_combo()
            self.changed.emit()

    def _on_remove(self) -> None:
        selected = self.list_widget.selectedItems()
        if not selected:
            return

        for item in selected:
            row = self.list_widget.row(item)
            self.list_widget.takeItem(row)

        self._adjust_height()
        self._refresh_combo()
        self.changed.emit()


# endregion
