from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hyvis.gui.base_widgets import SuggestionComboBox
from hyvis.gui.widgets.table_editors import parse_suggestions

# Each entry is either "template" or ("Menu display label", "pasted template")
_PredicateEntry = str | tuple[str, str]

_SYSTEM_PREDICATE_GROUPS: tuple[tuple[str, tuple[_PredicateEntry, ...]], ...] = (
    (
        "Common && Status",
        (
            "system:everything",
            "system:inbox",
            "system:archive",
            "system:has tags",
            "system:no tags",
            "system:untagged",
            "system:has duration",
            "system:no duration",
            "system:has audio",
            "system:no audio",
            "system:limit = 100",
        ),
    ),
    (
        "Tags && Notes",
        (
            "system:number of tags > 0",
            "system:number of tags > 5",
            "system:number of tags ~= 10",
            "system:number of words < 2",
            "system:tag as number page < 5",
            "---",
            "system:has notes",
            "system:no notes",
            "system:does not have notes",
            "system:num notes is 5",
            "system:num notes > 1",
            "system:has note with name note name",
            "system:no note with name note name",
            "system:does not have note with name note name",
        ),
    ),
    (
        "Dimensions && File Size",
        (
            "system:width > 1000",
            "system:width < 200",
            "system:height > 900",
            "system:height = 600",
            "system:num pixels > 50 px",
            "system:num pixels < 1 megapixels",
            "system:num pixels ~= 5 kilopixel",
            "system:ratio is 16:9",
            "system:ratio is wider than 16:9",
            "system:ratio taller than 1:1",
            "---",
            "system:filesize > 0 B",
            "system:filesize ~= 50 kilobytes",
            "system:filesize > 10megabytes",
            "system:filesize < 1 GB",
            "system:filetype = image/jpg, image/png, apng",
        ),
    ),
    (
        "Duration && Metadata",
        (
            "system:has duration",
            "system:no duration",
            "system:duration < 5 seconds",
            "system:duration ~= 600 msecs",
            "system:duration > 3 milliseconds",
            "---",
            "system:has exif",
            "system:no exif",
            "system:has human-readable metadata",
            "system:no human-readable metadata",
            "system:has icc profile",
            "system:no icc profile",
        ),
    ),
    (
        "Time && Views",
        (
            "system:time imported < 7 years 45 days 7h",
            "system:time imported > 2011-06-04",
            "system:time imported > 7 years 2 months",
            "system:time imported < 0 years 1 month 1 day 1 hour",
            "system:time imported ~= 2011-1-3",
            "system:modified date < 7 years 45 days 7h",
            "system:modified date > 2011-06-04",
            "system:last viewed time < 7 years 45 days 7h",
            "---",
            "system:views > 0",
            "system:views in media ~= 10",
            "system:views in preview < 10",
            "system:viewtime in client api < 1 days 1 hour 0 minutes",
            "system:viewtime in media, client api, preview ~= 1 day 30 hours 100 minutes 90s",
        ),
    ),
    (
        "Duplicates && Relationships",
        (
            "system:is the best quality file of its duplicate group",
            "system:is not the best quality file of its duplicate group",
            "system:number of file relationships = 2 duplicates",
            "system:number of file relationships > 10 potential duplicates",
            "system:num file relationships < 3 alternates",
            "system:num file relationships > 3 false positives",
        ),
    ),
    (
        "URLs && Domains",
        (
            "system:has url https://somebooru.org/posts/123456",
            "system:does not have url https://somebooru.org/posts/123456",
            "system:has domain somebooru.com",
            "system:does not have domain somebooru.com",
            r"system:has url matching regex index\.php",
            r"system:does not have a url matching regex index\.php",
            "system:has a url with class somebooru file page",
            "system:does not have a url with url class somebooru file page",
        ),
    ),
    (
        "Services, Hashes && Ratings",
        (
            "system:file service currently in my files",
            "system:file service is not currently in my files",
            "system:file service is pending to my files",
            "system:file service is not pending to my files",
            "---",
            ("system:hash = abcdef01 abcdef02 abcdef03  (sha256)", "system:hash = abcdef01 abcdef02 abcdef03"),
            "system:hash = abcdef01 abcdef02 md5",
            "system:similar to abcdef01 abcdef02 abcdef03, abcdef04 with distance 3",
            "---",
            "system:has a rating for service_name",
            "system:does not have a rating for service_name",
            ("system:rating for service_name > 3/5  (numerical)", "system:rating for service_name > 3/5"),
            ("system:rating for service_name is like  (like/dislike)", "system:rating for service_name is like"),
            ("system:rating for service_name = 13  (inc/dec)", "system:rating for service_name = 13"),
        ),
    ),
)


def _build_system_predicate_menu(parent: QWidget, on_selected: Callable[[str], None]) -> QMenu:
    """Build a categorized QMenu of Hydrus system predicate templates."""
    menu = QMenu(parent)
    for group_name, entries in _SYSTEM_PREDICATE_GROUPS:
        sub = menu.addMenu(group_name)
        for entry in entries:
            if entry == "---":
                sub.addSeparator()
                continue
            if isinstance(entry, tuple):
                label, template = entry
            else:
                label, template = entry, entry
            action = sub.addAction(label)
            action.triggered.connect(lambda _chk=False, tpl=template: on_selected(tpl))
    return menu


def adjust_list_widget_height(list_widget: QListWidget, *, min_h: int = 68, max_h: int = 160) -> None:
    """Dynamically expand list widget height to fit items up to max_h."""
    count = list_widget.count()
    target = max(min_h, min(min_h + max(0, count - 2) * 22, max_h))
    list_widget.setFixedHeight(target)


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
        show_system_predicates: bool = False,
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

        if show_system_predicates:
            self.sys_pred_btn = QPushButton("System", self)
            self.sys_pred_btn.setToolTip("Insert a Hydrus system predicate template into the input box")
            self.sys_pred_btn.setMenu(_build_system_predicate_menu(self.sys_pred_btn, self._insert_predicate_template))
            input_layout.addWidget(self.sys_pred_btn)

        self.add_btn = QPushButton("Add", self)
        self.add_btn.clicked.connect(self._on_add)
        input_layout.addWidget(self.add_btn)

        self.remove_btn = QPushButton("Remove", self)
        self.remove_btn.clicked.connect(self._on_remove)
        input_layout.addWidget(self.remove_btn)

        layout.addLayout(input_layout)

    def _insert_predicate_template(self, template: str) -> None:
        self.input_line.setText(template)
        self.input_line.setFocus()
        self.input_line.setCursorPosition(len(template))

    def _adjust_height(self) -> None:
        adjust_list_widget_height(self.list_widget)

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
        adjust_list_widget_height(self.list_widget)

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
        self._suggestions, self._provenance = parse_suggestions(suggestions)
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
