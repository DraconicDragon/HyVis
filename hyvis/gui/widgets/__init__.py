"""
widgets — Reusable compound configuration editors and cards for HyVis.
"""

from __future__ import annotations

from hyvis.gui.widgets.entity_editors import (
    HYDRUS_BUILTIN_ALL_KNOWN_TAGS_KEY,
    BaseStackedEntityEditor,
    PageQueryListEditor,
    TagServiceListEditor,
)

from hyvis.gui.widgets.list_editors import CategoryTagEditor, StringListEditor
from hyvis.gui.widgets.path_picker import PathPickerMode, PathPickerWidget
from hyvis.gui.widgets.rule_editors import (
    TagQueryListEditor,
    TagRuleCard,
    TagRuleListEditor,
    TagSubsetCard,
    TagSubsetListEditor,
)
from hyvis.gui.widgets.section_card import SectionCard
from hyvis.gui.widgets.table_editors import CategoryLimitEditor, KeyValueEditor, ThresholdTableEditor

__all__ = [
    "HYDRUS_BUILTIN_ALL_KNOWN_TAGS_KEY",
    "BaseStackedEntityEditor",
    "CategoryLimitEditor",
    "CategoryTagEditor",
    "KeyValueEditor",
    "PageQueryListEditor",
    "PathPickerMode",
    "PathPickerWidget",
    "SectionCard",
    "StringListEditor",
    "TagQueryListEditor",
    "TagRuleCard",
    "TagRuleListEditor",
    "TagServiceListEditor",
    "TagSubsetCard",
    "TagSubsetListEditor",
    "ThresholdTableEditor",
]
