from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from hyvis.gui.base_widgets import SemanticLabel
from hyvis.gui.theme import AutoRepolishMixin, ButtonVariant, CardTheme, TextRole, ThemeProperty, WidgetState
from hyvis.gui.widgets.entity_editors import HYDRUS_BUILTIN_ALL_KNOWN_TAGS_KEY
from hyvis.gui.widgets.list_editors import StringListEditor

# region Tag Subset List Editor (Joint Subset Limits)


class TagSubsetCard(AutoRepolishMixin, QFrame):
    """
    A single card representing a Joint Subset Limit rule (max_tags_per_subset).
    Eliminates comma bugs by managing tags via StringListEditor (with multi-line paste).
    """

    changed = Signal()
    delete_requested = Signal()

    def __init__(self, index: int = 1, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._index = index

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setProperty(ThemeProperty.CARD_THEME, CardTheme.PURPLE)
        self.setProperty(ThemeProperty.STATE, WidgetState.DEFAULT)

        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(10, 8, 10, 10)
        card_layout.setSpacing(8)

        # 1. Header
        header_layout = QHBoxLayout()
        self.title_label = QLabel(f"<b>Subset Group #{self._index}</b>", self)
        self.title_label.setStyleSheet("font-size: 12px;")
        header_layout.addWidget(self.title_label)

        header_layout.addStretch(1)

        self.del_btn = QPushButton("✕", self)
        self.del_btn.setProperty(ThemeProperty.BTN_VARIANT, ButtonVariant.DANGER_GHOST)
        self.del_btn.setFixedWidth(26)
        self.del_btn.setToolTip("Remove this subset group")
        self.del_btn.setStyleSheet("font-weight: bold;")
        self.del_btn.clicked.connect(self.delete_requested.emit)
        header_layout.addWidget(self.del_btn)

        card_layout.addLayout(header_layout)

        # 2. Limit row
        limit_row = QHBoxLayout()
        limit_row.setSpacing(8)
        limit_lbl = SemanticLabel("Max Output Limit:", TextRole.SECONDARY, self)
        limit_row.addWidget(limit_lbl)

        self.limit_spin = QSpinBox(self)
        self.limit_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.limit_spin.setRange(1, 2_147_483_647)
        self.limit_spin.setValue(1)
        self.limit_spin.valueChanged.connect(lambda _: self.changed.emit())
        limit_row.addWidget(self.limit_spin)

        tags_suffix = SemanticLabel("tag(s)", TextRole.MUTED, self)
        limit_row.addWidget(tags_suffix)

        limit_row.addStretch(1)
        card_layout.addLayout(limit_row)

        # 3. Tags in subset
        t_label = SemanticLabel("Tags in Subset:", TextRole.SECONDARY, self)
        card_layout.addWidget(t_label)

        self.tags_editor = StringListEditor(placeholder="Add tag to subset group...", parent=self)
        self.tags_editor.changed.connect(self.changed.emit)
        card_layout.addWidget(self.tags_editor)

    def set_highlight_state(self, is_overridden: bool = False, is_error: bool = False) -> None:
        """Update state property; AutoRepolishMixin automatically triggers restyling."""
        state = WidgetState.ERROR if is_error else (WidgetState.OVERRIDDEN if is_overridden else WidgetState.DEFAULT)
        self.setProperty(ThemeProperty.STATE, state)

    def set_index(self, index: int) -> None:
        self._index = index
        self.title_label.setText(f"<b>Subset Group #{self._index}</b>")

    def get_subset(self) -> dict[str, Any]:
        return {
            "tags": self.tags_editor.get_items(),
            "limit": int(self.limit_spin.value()),
        }

    def set_subset(self, tags: Sequence[str], limit: int = 1) -> None:
        self.tags_editor.set_items(tags)
        self.limit_spin.setValue(int(limit))


class TagSubsetListEditor(QWidget):
    """
    Stacked card editor for [[output_filter.max_tags_per_subset]].
    Eliminates the comma-splitting bug by using individual tag lists per group.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards: list[TagSubsetCard] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 1. Cards container
        self._cards_widget = QWidget(self)
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        layout.addWidget(self._cards_widget)

        # 2. Empty placeholder
        self.empty_label = SemanticLabel(
            "(No joint subset limits configured — click '+ Add Subset Group' below)", parent=self
        )
        self.empty_label.setStyleSheet("font-style: italic; padding: 4px;")
        layout.addWidget(self.empty_label)

        # 3. Add button
        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("+ Add Subset Group", self)
        self.add_btn.clicked.connect(self._on_add_clicked)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self._update_empty_state()

    def set_highlight_state(self, is_overridden: bool = False, is_error: bool = False) -> None:
        for card in self._cards:
            card.set_highlight_state(is_overridden=is_overridden, is_error=is_error)

    def get_subsets(self) -> list[dict[str, Any]]:
        subsets: list[dict[str, Any]] = []
        for card in self._cards:
            s = card.get_subset()
            if s["tags"]:  # Only export groups that have at least one tag
                subsets.append(s)
        return subsets

    def set_subsets(self, subsets: Sequence[Any]) -> None:
        self.blockSignals(True)
        self._clear_cards()
        for idx, s in enumerate(subsets, start=1):
            tags = getattr(s, "tags", None) or (s.get("tags") if isinstance(s, dict) else [])
            limit = getattr(s, "limit", None) or (s.get("limit", 1) if isinstance(s, dict) else 1)
            self._add_card(tags, limit, idx)
        self._update_empty_state()
        self.blockSignals(False)

    def _clear_cards(self) -> None:
        for card in self._cards:
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

    def _add_card(
        self,
        tags: Sequence[str] | None = None,
        limit: int = 1,
        index: int | None = None,
    ) -> TagSubsetCard:
        idx = index or (len(self._cards) + 1)
        card = TagSubsetCard(index=idx, parent=self._cards_widget)
        if tags is not None:
            card.set_subset(tags, limit)
        card.changed.connect(self.changed.emit)
        card.delete_requested.connect(lambda: self._on_delete_card(card))
        self._cards_layout.addWidget(card)
        self._cards.append(card)
        self._update_empty_state()
        return card

    def _on_add_clicked(self) -> None:
        self._add_card()
        self.changed.emit()

    def _on_delete_card(self, card: TagSubsetCard) -> None:
        if card in self._cards:
            self._cards.remove(card)
            self._cards_layout.removeWidget(card)
            card.deleteLater()
            self._renumber_cards()
            self._update_empty_state()
            self.changed.emit()

    def _renumber_cards(self) -> None:
        for idx, card in enumerate(self._cards, start=1):
            card.set_index(idx)

    def _update_empty_state(self) -> None:
        self.empty_label.setVisible(len(self._cards) == 0)


# endregion


# region Tag Rule & Query Stacked Card Editors


class TagRuleCard(AutoRepolishMixin, QFrame):
    """
    A single card mapping tags to a specific Hydrus service key.
    Used for:
      - [[hydrus.tag_queries]] (allow_search_all=True, show_condition=False, theme=CardTheme.INFO)
      - [[hydrus.add_tags]] (allow_search_all=False, show_condition=True, theme=CardTheme.SUCCESS)
      - [[hydrus.remove_tags]] (allow_search_all=False, show_condition=True, theme=CardTheme.DANGER)
    """

    changed = Signal()
    delete_requested = Signal()

    def __init__(
        self,
        index: int = 1,
        title_prefix: str = "Rule",
        tags_label: str = "Tags:",
        placeholder: str = "Enter tag and press Enter or Add...",
        allow_search_all: bool = False,
        show_condition: bool = False,
        show_system_predicates: bool = False,
        theme: CardTheme = CardTheme.DEFAULT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._available_services: dict[str, Any] = {}
        self._index = index
        self._title_prefix = title_prefix
        self._allow_search_all = allow_search_all
        self._show_condition = show_condition
        self._theme = theme

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setProperty(ThemeProperty.CARD_THEME, self._theme)
        self.setProperty(ThemeProperty.STATE, WidgetState.DEFAULT)

        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(10, 8, 10, 10)
        card_layout.setSpacing(8)

        # 1. Header row
        header_layout = QHBoxLayout()
        self.title_label = QLabel(f"<b>{self._title_prefix} #{self._index}</b>", self)
        self.title_label.setStyleSheet("font-size: 12px;")
        header_layout.addWidget(self.title_label)

        header_layout.addStretch(1)

        self.del_btn = QPushButton("✕", self)
        self.del_btn.setProperty(ThemeProperty.BTN_VARIANT, ButtonVariant.DANGER_GHOST)
        self.del_btn.setFixedWidth(26)
        self.del_btn.setToolTip("Remove this rule")
        self.del_btn.setStyleSheet("font-weight: bold;")
        self.del_btn.clicked.connect(self.delete_requested.emit)
        header_layout.addWidget(self.del_btn)

        card_layout.addLayout(header_layout)

        # 2. Target Service row
        svc_row = QHBoxLayout()
        svc_row.setSpacing(8)
        svc_label = SemanticLabel("Target Service:", TextRole.SECONDARY, self)
        svc_row.addWidget(svc_label)

        self.service_combo = QComboBox(self)
        self.service_combo.setEditable(False)
        self._repopulate_services()
        self.service_combo.currentIndexChanged.connect(lambda _: self.changed.emit())
        svc_row.addWidget(self.service_combo, stretch=1)

        card_layout.addLayout(svc_row)

        # 3. Optional Condition Checkbox (strictly for add_tags / remove_tags; never tag_queries)
        if self._show_condition:
            cond_row = QHBoxLayout()
            self.only_if_tags_chk = QCheckBox("Only apply if model emits tags (skip if zero tags)", self)
            self.only_if_tags_chk.setObjectName("only_if_tags_emitted")
            self.only_if_tags_chk.setProperty(ThemeProperty.TEXT_ROLE, TextRole.SECONDARY)
            self.only_if_tags_chk.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.only_if_tags_chk.setToolTip(
                "When enabled, this rule is only executed if at least one tag was emitted by the model(s) "
                "during this run.\n"
                "If all predictions fall below threshold (zero tags), this rule is skipped.\n\n"
                "When disabled, this rule is always applied after processing."
            )
            self.only_if_tags_chk.toggled.connect(lambda _: self.changed.emit())
            cond_row.addWidget(self.only_if_tags_chk)
            card_layout.addLayout(cond_row)

        # 4. Tags list editor
        t_label = SemanticLabel(tags_label, TextRole.SECONDARY, self)
        card_layout.addWidget(t_label)

        self.tags_editor = StringListEditor(
            placeholder=placeholder,
            show_system_predicates=show_system_predicates,
            parent=self,
        )
        self.tags_editor.changed.connect(self.changed.emit)
        card_layout.addWidget(self.tags_editor)

    def set_index(self, index: int) -> None:
        self._index = index
        self.title_label.setText(f"<b>{self._title_prefix} #{self._index}</b>")

    def set_available_services(self, services: dict[str, Any]) -> None:
        self._available_services = dict(services)
        current_key = self.service_combo.currentData()
        self._repopulate_services(selected_key=str(current_key) if current_key else None)

    def _repopulate_services(self, selected_key: str | None = None) -> None:
        self.service_combo.blockSignals(True)
        self.service_combo.clear()

        # Offline State
        if not self._available_services:
            if selected_key and selected_key != HYDRUS_BUILTIN_ALL_KNOWN_TAGS_KEY:
                short_k = f"{selected_key[:8]}..." if len(selected_key) > 12 else selected_key
                self.service_combo.addItem(f"Service: {short_k} (Offline)", userData=selected_key)
            else:
                msg = "⚠ Connect to Hydrus to select service"
                self.service_combo.addItem(msg, userData="")

            self.service_combo.setCurrentIndex(0)
            self.service_combo.setEnabled(False)
            self.service_combo.blockSignals(False)
            return

        self.service_combo.setEnabled(True)

        # Allow Search All ("All Known Tags") only for Tag Queries
        if self._allow_search_all:
            all_known_info = self._available_services.get(HYDRUS_BUILTIN_ALL_KNOWN_TAGS_KEY)
            if all_known_info:
                name = (
                    all_known_info.get("name", "All Known Tags")
                    if isinstance(all_known_info, dict)
                    else str(all_known_info)
                )
                short_k = f"{HYDRUS_BUILTIN_ALL_KNOWN_TAGS_KEY[:8]}..."
                display = f"{name} (virtual)  ({short_k})"
            else:
                display = "All Known Tags (virtual)"
            self.service_combo.addItem(display, userData="")

        # Populate services
        for key, val in self._available_services.items():
            if self._allow_search_all and key == HYDRUS_BUILTIN_ALL_KNOWN_TAGS_KEY:
                continue

            if isinstance(val, dict):
                name = val.get("name", key)
                stype = val.get("type")
                is_virtual = bool(val.get("is_virtual", False) or stype == 10)
            else:
                name = str(val)
                is_virtual = False

            if not self._allow_search_all and is_virtual:
                continue

            virt_label = " (virtual)" if is_virtual else ""
            short_key = f"{key[:8]}..." if len(key) > 12 else key
            self.service_combo.addItem(f"{name}{virt_label}  ({short_key})", userData=key)

        # Resolve selection
        if selected_key:
            if self._allow_search_all and (selected_key == HYDRUS_BUILTIN_ALL_KNOWN_TAGS_KEY or selected_key == ""):
                self.service_combo.setCurrentIndex(0)
            else:
                idx = self.service_combo.findData(selected_key)
                if idx >= 0:
                    self.service_combo.setCurrentIndex(idx)
                else:
                    short_key = f"{selected_key[:8]}..." if len(selected_key) > 12 else selected_key
                    self.service_combo.addItem(f"❌ Unrecognized Service  ({short_key})", userData=selected_key)
                    self.service_combo.setCurrentIndex(self.service_combo.count() - 1)
        else:
            self.service_combo.setCurrentIndex(0)

        self.service_combo.blockSignals(False)

    def get_rule(self) -> dict[str, Any]:
        tags = self.tags_editor.get_items()
        selected_key = str(self.service_combo.currentData() or "").strip()
        service_keys = [selected_key] if selected_key else []
        rule: dict[str, Any] = {"tags": tags, "tag_service_keys": service_keys}
        if self._show_condition and hasattr(self, "only_if_tags_chk"):
            rule["only_if_tags_emitted"] = self.only_if_tags_chk.isChecked()
        return rule

    def set_rule(
        self,
        tags: Sequence[Any],
        service_keys: Sequence[str],
        only_if_tags_emitted: bool = False,
    ) -> None:
        self.tags_editor.set_items([str(t) for t in tags])
        key = service_keys[0] if service_keys else ""
        self._repopulate_services(selected_key=key)
        if self._show_condition and hasattr(self, "only_if_tags_chk"):
            self.only_if_tags_chk.setChecked(bool(only_if_tags_emitted))


class TagRuleListEditor(QWidget):
    """
    Stacked list editor for multi-rule tag blocks.
    Used for tag_queries, add_tags, and remove_tags.
    """

    changed = Signal()

    def __init__(
        self,
        title_prefix: str = "Rule",
        tags_label: str = "Tags:",
        placeholder: str = "Enter tag and press Enter or Add...",
        allow_search_all: bool = False,
        show_condition: bool = False,
        show_system_predicates: bool = False,
        theme: CardTheme = CardTheme.DEFAULT,
        add_btn_text: str = "+ Add Rule",
        empty_text: str = "(No rules configured — click button below)",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cards: list[TagRuleCard] = []
        self._available_services: dict[str, Any] = {}
        self._title_prefix = title_prefix
        self._tags_label = tags_label
        self._placeholder = placeholder
        self._allow_search_all = allow_search_all
        self._show_condition = show_condition
        self._show_system_predicates = show_system_predicates
        self._theme = theme

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(8)

        # 1. Cards container
        self._cards_widget = QWidget(self)
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        self._root_layout.addWidget(self._cards_widget)

        # 2. Empty placeholder
        self.empty_label = SemanticLabel(empty_text, parent=self)
        self.empty_label.setStyleSheet("font-style: italic; padding: 4px;")
        self._root_layout.addWidget(self.empty_label)

        # 3. Add button
        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton(add_btn_text, self)
        self.add_btn.clicked.connect(self._on_add_clicked)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addStretch()
        self._root_layout.addLayout(btn_layout)

        self._update_empty_state()

    def set_available_services(self, services: dict[str, Any]) -> None:
        self._available_services = dict(services)
        for card in self._cards:
            card.set_available_services(services)

    def get_rules(self) -> list[dict[str, Any]]:
        rules: list[dict[str, Any]] = []
        for card in self._cards:
            r = card.get_rule()
            if r["tags"]:  # Only export rules that have at least one tag
                rules.append(r)
        return rules

    def set_rules(self, rules: Sequence[Any]) -> None:
        self.blockSignals(True)
        self._clear_cards()
        for idx, r in enumerate(rules, start=1):
            tags = getattr(r, "tags", None) or (r.get("tags") if isinstance(r, dict) else [])
            keys = getattr(r, "tag_service_keys", None) or (r.get("tag_service_keys") if isinstance(r, dict) else [])
            cond = (
                getattr(r, "only_if_tags_emitted", False)
                if hasattr(r, "only_if_tags_emitted")
                else (r.get("only_if_tags_emitted", False) if isinstance(r, dict) else False)
            )
            self._add_card(tags, keys, idx, only_if_tags_emitted=cond)
        self._update_empty_state()
        self.blockSignals(False)

    def _clear_cards(self) -> None:
        for card in self._cards:
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

    def _add_card(
        self,
        tags: Sequence[Any] | None = None,
        keys: Sequence[str] | None = None,
        index: int | None = None,
        only_if_tags_emitted: bool = False,
    ) -> TagRuleCard:
        idx = index or (len(self._cards) + 1)
        card = TagRuleCard(
            index=idx,
            title_prefix=self._title_prefix,
            tags_label=self._tags_label,
            placeholder=self._placeholder,
            allow_search_all=self._allow_search_all,
            show_condition=self._show_condition,
            show_system_predicates=self._show_system_predicates,
            theme=self._theme,
            parent=self._cards_widget,
        )
        card.set_available_services(self._available_services)
        if tags is not None:
            card.set_rule(tags, keys or [], only_if_tags_emitted=only_if_tags_emitted)
        else:
            card.set_rule([], [], only_if_tags_emitted=only_if_tags_emitted)

        card.changed.connect(self.changed.emit)
        card.delete_requested.connect(lambda: self._on_delete_card(card))

        self._cards_layout.addWidget(card)
        self._cards.append(card)
        self._update_empty_state()
        return card

    def _on_add_clicked(self) -> None:
        self._add_card()
        self.changed.emit()

    def _on_delete_card(self, card: TagRuleCard) -> None:
        if card in self._cards:
            self._cards.remove(card)
            self._cards_layout.removeWidget(card)
            card.deleteLater()
            self._renumber_cards()
            self._update_empty_state()
            self.changed.emit()

    def _renumber_cards(self) -> None:
        for idx, card in enumerate(self._cards, start=1):
            card.set_index(idx)

    def _update_empty_state(self) -> None:
        self.empty_label.setVisible(len(self._cards) == 0)


class TagQueryListEditor(TagRuleListEditor):
    """Convenience subclass for [[hydrus.tag_queries]] with CardTheme.INFO (Cyan)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            title_prefix="Query",
            tags_label="Search Tags:",
            placeholder="Enter search tag and press Enter or Add...",
            allow_search_all=True,
            show_system_predicates=True,
            theme=CardTheme.INFO,
            add_btn_text="+ Add Tag Query",
            empty_text="(No tag queries configured — click '+ Add Tag Query' below)",
            parent=parent,
        )

    # Maintain method aliases
    get_queries = TagRuleListEditor.get_rules
    set_queries = TagRuleListEditor.set_rules


# endregion
