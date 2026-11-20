from __future__ import annotations

import types
from collections.abc import Mapping
from typing import Any, Literal, Union, get_args, get_origin

from pydantic_core import PydanticUndefined
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from hyvis.gui.base_widgets import FloatSpinBox
from hyvis.gui.theme import ThemeProperty, WidgetState

_QT_INT_MAX = 2_147_483_647
_QT_INT_MIN = -2_147_483_648


# region Form Helpers & Styling


def _unwrap_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        non_none = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(non_none) == 1:
            return _unwrap_optional(non_none[0])
    return annotation


def validate_field_ui_metadata(
    field_info: Any, *, field_label: str = "Field"
) -> tuple[tuple[Any, ...] | None, dict[Any, str] | None]:
    """
    Verify a Pydantic FieldInfo defines non-empty title/description and valid Literal labels.
    Returns (literal_choices, labels_map).
    """
    title = getattr(field_info, "title", None)
    if not title or not str(title).strip():
        raise ValueError(f"{field_label} is missing a non-empty Field(title=...).")

    desc = getattr(field_info, "description", None)
    if not desc or not str(desc).strip():
        raise ValueError(f"{field_label} ('{title}') is missing a non-empty Field(description=...).")

    core_type = _unwrap_optional(getattr(field_info, "annotation", None))
    choices: tuple[Any, ...] | None = tuple(get_args(core_type)) if get_origin(core_type) is Literal else None

    extra = getattr(field_info, "json_schema_extra", None)
    labels: dict[Any, str] | None = None
    if isinstance(extra, Mapping) and "labels" in extra:
        raw_labels = extra["labels"]
        if choices is None:
            raise TypeError(f"{field_label} ('{title}') defines UI labels, but is not a Literal field.")
        if not isinstance(raw_labels, Mapping) or set(raw_labels.keys()) != set(choices):
            raise ValueError(
                f"{field_label} ('{title}') UI labels {sorted(raw_labels.keys(), key=str)} "
                f"do not match Literal options {sorted(choices, key=str)}."
            )
        for k, v in raw_labels.items():
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"{field_label} ('{title}') has an empty label for option {k!r}.")
        labels = dict(raw_labels)

    return choices, labels


def set_combo_value(combo: QComboBox, value: Any) -> None:
    """Select `value` on a QComboBox safely whether items use `userData` or plain text."""
    if any(combo.itemData(i) is not None for i in range(combo.count())):
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return

    if value is not None:
        text_val = str(value)
        idx = combo.findText(text_val)
        if idx >= 0:
            combo.setCurrentIndex(idx)
            return
        if combo.isEditable():
            combo.setCurrentText(text_val)
            return

    if combo.count() > 0:
        combo.setCurrentIndex(0)


def setup_field_tooltip(widget: QWidget, field_info: Any) -> None:
    """Set the widget tooltip from Pydantic Field description (errors if description is missing)."""
    desc = getattr(field_info, "description", None)
    if not desc or not str(desc).strip():
        raise ValueError("Config FieldInfo is missing a non-empty 'description' for tooltip.")
    widget.setToolTip(str(desc))


def bind_field_metadata(
    widget: QWidget,
    field_info: Any,
    *,
    field_name: str | None = None,
    set_placeholder: bool = True,
    apply_default: bool = True,
) -> None:
    """
    Bind Pydantic Field metadata to a Qt widget:
      - Enforces non-empty title & description tooltips (and validates Literal labels)
      - Applies numeric bounds & defaults to QSpinBox / FloatSpinBox
      - Populates non-editable Literal QComboBox items & labels + default selection
      - Sets title, '&'-escaping, and default on QCheckBox (when text is unset)
      - Sets maxLength, examples[0] placeholder, and default text on QLineEdit
    """
    choices, labels = validate_field_ui_metadata(field_info, field_label=field_name or "Field")

    if field_name:
        widget.setObjectName(field_name)

    widget.setToolTip(str(field_info.description))

    if isinstance(widget, (QAbstractSpinBox, QComboBox)) and widget.focusPolicy() == Qt.FocusPolicy.WheelFocus:
        widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    has_default = False
    default_val: Any = None
    if getattr(field_info, "default", PydanticUndefined) is not PydanticUndefined:
        has_default = True
        default_val = field_info.default
    elif getattr(field_info, "default_factory", None) is not None:
        has_default = True
        default_val = field_info.default_factory()

    ge = gt = le = lt = max_len = None
    for item in getattr(field_info, "metadata", ()):
        if getattr(item, "ge", None) is not None:
            ge = item.ge
        if getattr(item, "gt", None) is not None:
            gt = item.gt
        if getattr(item, "le", None) is not None:
            le = item.le
        if getattr(item, "lt", None) is not None:
            lt = item.lt
        if getattr(item, "max_length", None) is not None:
            max_len = int(item.max_length)

    was_blocked = widget.signalsBlocked()
    widget.blockSignals(True)
    try:
        if isinstance(widget, QSpinBox):
            min_v = int(ge) if ge is not None else (int(gt) + 1 if gt is not None else widget.minimum())
            max_v = int(le) if le is not None else (int(lt) - 1 if lt is not None else _QT_INT_MAX)
            widget.setRange(max(_QT_INT_MIN, min_v), min(_QT_INT_MAX, max_v))
            if apply_default and has_default and isinstance(default_val, int) and not isinstance(default_val, bool):
                widget.setValue(default_val)

        elif isinstance(widget, FloatSpinBox):
            widget.set_schema_bounds(
                ge=float(ge) if ge is not None else None,
                gt=float(gt) if gt is not None else None,
                le=float(le) if le is not None else None,
                lt=float(lt) if lt is not None else None,
            )
            if (
                apply_default
                and has_default
                and isinstance(default_val, (int, float))
                and not isinstance(default_val, bool)
            ):
                widget.setValue(float(default_val))

        elif isinstance(widget, QCheckBox):
            if not widget.text():
                widget.setText(str(field_info.title).replace("&", "&&"))
            if apply_default and has_default and isinstance(default_val, bool):
                widget.setChecked(default_val)

        elif isinstance(widget, QComboBox):
            if not widget.isEditable() and choices is not None:
                widget.clear()
                for opt in choices:
                    widget.addItem(labels[opt] if labels is not None else str(opt), userData=opt)
                if apply_default and has_default and default_val is not None:
                    set_combo_value(widget, default_val)

        elif isinstance(widget, QLineEdit):
            if max_len is not None:
                widget.setMaxLength(max_len)
            examples = getattr(field_info, "examples", None)
            if set_placeholder and examples and isinstance(examples[0], (str, int, float)):
                widget.setPlaceholderText(str(examples[0]))
            if apply_default and has_default and isinstance(default_val, str) and not widget.text():
                widget.setText(default_val)

        elif hasattr(widget, "apply_field_metadata"):
            widget.apply_field_metadata(
                field_info,
                set_placeholder=set_placeholder,
                apply_default=apply_default,
            )
    finally:
        widget.blockSignals(was_blocked)


def set_widget_override_state(widget: QWidget, is_overridden: bool, is_error: bool = False) -> None:
    """Apply or clear visual override/error state on an input widget, card, or table/list."""
    target = widget
    if hasattr(widget, "list_widget"):
        target = widget.list_widget
    elif hasattr(widget, "table"):
        target = widget.table
    elif hasattr(widget, "set_highlight_state"):
        widget.set_highlight_state(is_overridden=is_overridden, is_error=is_error)
        return

    state = WidgetState.ERROR if is_error else (WidgetState.OVERRIDDEN if is_overridden else WidgetState.DEFAULT)
    target.setProperty(ThemeProperty.STATE, state.value if hasattr(state, "value") else str(state))
    style = target.style()
    if style:
        style.unpolish(target)
        style.polish(target)
        target.update()


def add_form_row(
    layout: QFormLayout,
    fields_or_info: Any,
    field_name_or_widget: str | QWidget,
    widget: QWidget | None = None,
    label_text: str | None = None,
    *,
    set_placeholder: bool = True,
) -> QLabel:
    """
    Add a row to a QFormLayout, binding FieldInfo title, description tooltip,
    numeric bounds, Literal choices, examples[0] placeholder, and default value.
    Automatically assigns widget.setObjectName(field_name) when field_name is passed.
    """
    if isinstance(field_name_or_widget, str):
        field_name = field_name_or_widget
        field_info = fields_or_info[field_name]
        target_widget = widget
    else:
        field_name = None
        field_info = fields_or_info
        target_widget = field_name_or_widget

    assert target_widget is not None

    bind_field_metadata(
        target_widget,
        field_info,
        field_name=field_name,
        set_placeholder=set_placeholder,
    )

    title = label_text or str(field_info.title)
    label = QLabel(f"{title}:")
    label.setToolTip(str(field_info.description))

    layout.addRow(label, target_widget)
    return label


# endregion
