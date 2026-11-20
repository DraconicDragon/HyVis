from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from hyvis.gui.base_widgets import SemanticLabel, SuggestionComboBox
from hyvis.gui.theme import ButtonVariant, ThemeProperty

HYDRUS_BUILTIN_ALL_KNOWN_TAGS_KEY = "616c6c206b6e6f776e2074616773"


# region Base Stacked Entity Editor


class BaseStackedEntityEditor(QWidget):
    """
    Reusable base widget for stacked non-editable entity selection rows.
    Manages the row layouts, [✕] deletion buttons, empty placeholder state,
    cross-row duplicate exclusion, and + Add button availability capping.
    """

    changed = Signal()

    def __init__(
        self,
        *,
        empty_text: str = "(No items selected)",
        add_btn_text: str = "+ Add Entry",
        offline_tooltip: str = "Connect to Hydrus to select items",
        all_used_tooltip: str = "All available items are already configured",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.empty_text = empty_text
        self.add_btn_text = add_btn_text
        self.offline_tooltip = offline_tooltip
        self.all_used_tooltip = all_used_tooltip

        self._row_widgets: list[QWidget] = []

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(6)

        # 1. Stack container
        self._stack_widget = QWidget(self)
        self._stack_layout = QVBoxLayout(self._stack_widget)
        self._stack_layout.setContentsMargins(0, 0, 0, 0)
        self._stack_layout.setSpacing(6)
        self._root_layout.addWidget(self._stack_widget)

        # 2. Empty placeholder label
        self.empty_label = SemanticLabel(self.empty_text, parent=self)
        self.empty_label.setStyleSheet("font-style: italic; padding: 4px;")
        self._root_layout.addWidget(self.empty_label)

        # 3. Add button
        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton(self.add_btn_text, self)
        self.add_btn.clicked.connect(self._on_add_clicked)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addStretch()
        self._root_layout.addLayout(btn_layout)

        self._update_empty_state()
        self._update_add_btn_state()

    # region Subclass Hooks

    def _extract_key(self, target: Any) -> Any:
        """Return the hashable identity for an item (e.g. key str or (name, index) tuple)."""
        raise NotImplementedError

    def _has_available_items(self) -> bool:
        """Return True if items are available to select from (i.e. connected to Hydrus)."""
        raise NotImplementedError

    def _get_all_available_keys(self) -> set[Any]:
        """Return a set of all unique available item identities."""
        raise NotImplementedError

    def _pick_next_unused_target(self) -> Any:
        """Return the next available entity payload not currently used in any row."""
        raise NotImplementedError

    def _build_combo_items(self, combo: QComboBox, selected_target: Any, used_keys: set[Any]) -> None:
        """Populate the combobox options, filtering out used_keys except selected_target."""
        raise NotImplementedError

    # endregion

    # region Core Lifecycle & Mechanics

    def _clear_rows(self) -> None:
        for row in self._row_widgets:
            self._stack_layout.removeWidget(row)
            row.deleteLater()
        self._row_widgets.clear()

    def _get_used_keys(self, exclude_row: QWidget | None = None) -> set[Any]:
        used = set()
        for row_widget in self._row_widgets:
            if row_widget == exclude_row:
                continue
            combo: QComboBox | None = row_widget.findChild(QComboBox)
            if combo:
                k = self._extract_key(combo.currentData())
                if k is not None:
                    used.add(k)
        return used

    def _repopulate_combo(
        self,
        combo: QComboBox,
        selected_target: Any = None,
        exclude_row: QWidget | None = None,
    ) -> None:
        combo.blockSignals(True)
        combo.clear()
        used = self._get_used_keys(exclude_row=exclude_row)
        self._build_combo_items(combo, selected_target, used)
        combo.blockSignals(False)

    def _add_row(self, initial_target: Any = None) -> QWidget:
        row_widget = QWidget(self._stack_widget)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        # Smart preselection: pick the first available entity not yet configured
        if initial_target is None and self._has_available_items():
            initial_target = self._pick_next_unused_target()

        combo = SuggestionComboBox(row_widget)
        combo.setEditable(False)
        self._repopulate_combo(combo, selected_target=initial_target, exclude_row=row_widget)
        combo.about_to_show_popup.connect(
            lambda c=combo, r=row_widget: self._repopulate_combo(c, c.currentData(), exclude_row=r)
        )
        combo.currentIndexChanged.connect(self._on_combo_changed)
        row_layout.addWidget(combo, stretch=1)

        del_btn = QPushButton("✕", row_widget)
        del_btn.setProperty(ThemeProperty.BTN_VARIANT, ButtonVariant.DANGER_GHOST)
        del_btn.setFixedWidth(28)
        del_btn.setToolTip("Remove this entry")
        del_btn.setStyleSheet("font-weight: bold;")
        del_btn.clicked.connect(lambda: self._on_remove_row(row_widget))
        row_layout.addWidget(del_btn)

        self._stack_layout.addWidget(row_widget)
        self._row_widgets.append(row_widget)
        self._update_empty_state()
        self._update_add_btn_state()
        return row_widget

    def _on_combo_changed(self) -> None:
        self._update_add_btn_state()
        self.changed.emit()

    def _on_add_clicked(self) -> None:
        self._add_row()
        self.changed.emit()

    def _on_remove_row(self, row_widget: QWidget) -> None:
        if row_widget in self._row_widgets:
            self._row_widgets.remove(row_widget)
            self._stack_layout.removeWidget(row_widget)
            row_widget.deleteLater()
            self._update_empty_state()
            self._update_add_btn_state()
            self.changed.emit()

    def _update_empty_state(self) -> None:
        self.empty_label.setVisible(len(self._row_widgets) == 0)

    def _update_add_btn_state(self) -> None:
        if not self._has_available_items():
            self.add_btn.setEnabled(False)
            self.add_btn.setToolTip(self.offline_tooltip)
            return

        used = self._get_used_keys()
        all_keys = self._get_all_available_keys()
        has_unused = bool(all_keys - used)

        self.add_btn.setEnabled(has_unused)
        self.add_btn.setToolTip("" if has_unused else self.all_used_tooltip)

    def _refresh_all_rows(self) -> None:
        for row_widget in self._row_widgets:
            combo: QComboBox | None = row_widget.findChild(QComboBox)
            if not combo:
                continue
            curr_data = combo.currentData()
            self._repopulate_combo(combo, selected_target=curr_data, exclude_row=row_widget)
        self._update_empty_state()
        self._update_add_btn_state()

    def _load_raw_targets(self, targets: Sequence[Any]) -> None:
        self.blockSignals(True)
        self._clear_rows()
        for t in targets:
            self._add_row(t)
        self._update_empty_state()
        self._update_add_btn_state()
        self.blockSignals(False)

    # endregion


# endregion


# region Tag Service List Editor


class TagServiceListEditor(BaseStackedEntityEditor):
    """
    Stacked row editor for Hydrus tag service keys.
    Displays human-readable service names while strictly preserving underlying 64-char hex keys.
    Non-editable entity selector with automatic next-available preselection and duplicate exclusion.
    """

    def __init__(
        self,
        writable_only: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        self.writable_only = writable_only
        self._available_services: dict[str, str] = {}
        super().__init__(
            empty_text="(No services selected — click '+ Add Service' below)",
            add_btn_text="+ Add Service",
            offline_tooltip="Connect to Hydrus to select tag services",
            all_used_tooltip="All available tag services are already configured",
            parent=parent,
        )

    def set_available_services(self, services: dict[str, str]) -> None:
        self._available_services = dict(services)
        self._refresh_all_rows()

    def get_items(self) -> list[str]:
        keys: list[str] = []
        for row_widget in self._row_widgets:
            combo: QComboBox | None = row_widget.findChild(QComboBox)
            if combo:
                data = combo.currentData()
                if data:
                    keys.append(str(data).strip())
        return keys

    def set_items(self, keys: Sequence[str]) -> None:
        self._load_raw_targets([k for k in keys if k])

    # region Base Hooks

    def _extract_key(self, target: Any) -> str | None:
        return str(target).strip() if target else None

    def _has_available_items(self) -> bool:
        return bool(self._available_services)

    def _get_all_available_keys(self) -> set[str]:
        return set(self._available_services.keys())

    def _pick_next_unused_target(self) -> str | None:
        used = self._get_used_keys()
        return next((k for k in self._available_services if k not in used), None)

    def _build_combo_items(self, combo: QComboBox, selected_target: Any, used_keys: set[str]) -> None:
        if not self._available_services:
            if selected_target:
                short_k = f"{str(selected_target)[:8]}..." if len(str(selected_target)) > 12 else str(selected_target)
                combo.addItem(f"Service: {short_k} (Offline)", userData=selected_target)
                combo.setCurrentIndex(0)
            else:
                combo.addItem("⚠ Connect to Hydrus to select services", userData="")
                combo.setCurrentIndex(0)
            combo.setEnabled(False)
            return

        combo.setEnabled(True)

        for key, name in self._available_services.items():
            if key not in used_keys or key == selected_target:
                short_key = f"{key[:8]}..." if len(key) > 12 else key
                display = f"{name}  ({short_key})"
                combo.addItem(display, userData=key)

        if selected_target:
            idx = combo.findData(selected_target)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                short_key = f"{str(selected_target)[:8]}..." if len(str(selected_target)) > 12 else str(selected_target)
                combo.addItem(f"❌ Unrecognized Service  ({short_key})", userData=selected_target)
                combo.setCurrentIndex(combo.count() - 1)
        else:
            combo.setCurrentIndex(0)

    # endregion


# endregion


# region Page Query List Editor


class PageQueryListEditor(BaseStackedEntityEditor):
    """
    Stacked row editor for [[hydrus.page_queries]].
    Displays open media pages with automatic disambiguation indices and file count previews.
    Features smart next-available preselection and duplicate page exclusion.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        self._available_pages: list[dict[str, Any]] = []
        super().__init__(
            empty_text="(No page queries configured — click '+ Add Page Query' below)",
            add_btn_text="+ Add Page Query",
            offline_tooltip="Connect to Hydrus with open media tabs to add page queries",
            all_used_tooltip="All open media tabs are already configured",
            parent=parent,
        )

    def set_available_pages(self, pages: list[dict[str, Any]]) -> None:
        self._available_pages = list(pages)
        self._refresh_all_rows()

    def get_queries(self) -> list[dict[str, Any]]:
        queries: list[dict[str, Any]] = []
        for row_widget in self._row_widgets:
            combo: QComboBox | None = row_widget.findChild(QComboBox)
            if combo:
                data = combo.currentData()
                if isinstance(data, dict) and data.get("name"):
                    q: dict[str, Any] = {"name": data["name"]}
                    if data.get("index") is not None:
                        q["index"] = int(data["index"])
                    queries.append(q)
        return queries

    def set_queries(self, queries: Sequence[Any]) -> None:
        raw_targets: list[dict[str, Any]] = []
        for q in queries:
            name = getattr(q, "name", None) or (q.get("name") if isinstance(q, dict) else "")
            idx = (
                getattr(q, "index", None) if hasattr(q, "index") else (q.get("index") if isinstance(q, dict) else None)
            )
            if name:
                raw_targets.append({"name": str(name), "index": idx})
        self._load_raw_targets(raw_targets)

    # region Base Hooks

    def _extract_key(self, target: Any) -> tuple[str, int | None] | None:
        if isinstance(target, dict):
            name = target.get("name")
            if name:
                return (str(name), target.get("index"))
        return None

    def _has_available_items(self) -> bool:
        return bool(self._available_pages)

    def _get_all_available_keys(self) -> set[tuple[str, int | None]]:
        return {k for p in self._available_pages if (k := self._extract_key(p)) is not None}

    def _pick_next_unused_target(self) -> dict[str, Any] | None:
        used = self._get_used_keys()
        for p in self._available_pages:
            pkey = self._extract_key(p)
            if pkey and pkey not in used:
                return {"name": p["name"], "index": p["index"]}
        return None

    def _build_combo_items(
        self,
        combo: QComboBox,
        selected_target: Any,
        used_keys: set[tuple[str, int | None]],
    ) -> None:
        if not self._available_pages:
            if selected_target:
                name = selected_target.get("name", "Unknown Page")
                idx = selected_target.get("index")
                idx_str = f" [index {idx}]" if idx is not None else ""
                combo.addItem(f"{name}{idx_str} (Offline / Closed)", userData=selected_target)
                combo.setCurrentIndex(0)
            else:
                combo.addItem("⚠ Connect to Hydrus with open media tabs", userData=None)
                combo.setCurrentIndex(0)
            combo.setEnabled(False)
            return

        combo.setEnabled(True)

        selected_key = self._extract_key(selected_target)

        for p in self._available_pages:
            pkey = self._extract_key(p)
            if pkey not in used_keys or pkey == selected_key:
                name = p["name"]
                idx = p["index"]
                raw_idx = p.get("raw_index", 0)
                has_dups = p.get("has_duplicates", False)
                num_files = p.get("num_files")

                files_str = f" · {num_files} files" if num_files is not None else ""
                tab_str = (
                    f" (Tab #{raw_idx + 1}{files_str})"
                    if has_dups
                    else (f" ({num_files} files)" if num_files is not None else "")
                )
                display = f"{name}{tab_str}"
                combo.addItem(display, userData={"name": name, "index": idx})

        if selected_target:
            target_name = selected_target.get("name")
            target_idx = selected_target.get("index")

            found_idx = -1
            for i in range(combo.count()):
                d = combo.itemData(i)
                if (
                    isinstance(d, dict)
                    and d.get("name") == target_name
                    and (target_idx is None or d.get("index") == target_idx)
                ):
                    found_idx = i
                    break

            if found_idx >= 0:
                combo.setCurrentIndex(found_idx)
            else:
                idx_str = f" [index {target_idx}]" if target_idx is not None else ""
                combo.addItem(f"{target_name}{idx_str} (Tab Closed in Hydrus)", userData=selected_target)
                combo.setCurrentIndex(combo.count() - 1)
        else:
            combo.setCurrentIndex(0)

    # endregion


# endregion
