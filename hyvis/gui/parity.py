"""
parity.py — Automated contract check ensuring the GUI exposes all Pydantic settings.
Dynamically traverses Pydantic type annotations to audit GUI data without hardcoded lists.
"""

from __future__ import annotations

import types
from collections.abc import Mapping, Sequence
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from hyvis.config import AppConfig


def _resolve_submodel(annotation: Any) -> tuple[type[BaseModel] | None, str]:
    """
    Inspects a field's type annotation using standard Python typing reflection.
    Returns (ModelClass, kind) where kind is 'model', 'list', 'dict', or 'leaf'.
    """
    origin = get_origin(annotation)
    args = get_args(annotation)

    # Unwrap Optional[T] / Union[T, None]
    if origin is types.UnionType or origin is Union:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            annotation = non_none[0]
            origin = get_origin(annotation)
            args = get_args(annotation)

    # 1. Direct BaseModel (e.g. OutputTagServices, PreviewConfig)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation, "model"

    # 2. List of BaseModels (e.g. list[AddTagConfig], list[TagQueryConfig])
    if origin in (list, Sequence) and args:
        item_cls = _resolve_submodel(args[0])[0]
        if item_cls:
            return item_cls, "list"

    # 3. Dict of BaseModels (e.g. dict[str, CategoryThresholdConfig])
    if origin in (dict, Mapping) and len(args) == 2:
        val_cls = _resolve_submodel(args[1])[0]
        if val_cls:
            return val_cls, "dict"

    return None, "leaf"


def _audit_model(
    model_cls: type[BaseModel],
    data: dict[str, Any],
    prefix: str,
    missing: list[str],
    extra: list[str],
) -> None:
    # 1. Check for extra keys in GUI data that don't exist in Pydantic schema
    for k in data.keys():
        if k not in model_cls.model_fields:
            extra.append(f"{prefix}.{k}" if prefix else k)

    # 2. Check each declared field in the Pydantic model
    for field_name, field_info in model_cls.model_fields.items():
        current_path = f"{prefix}.{field_name}" if prefix else field_name

        # Model-specific filter overrides are checked under root output_filter
        if prefix == "inference.models" and field_name == "output_filter":
            if field_name not in data:
                missing.append(current_path)
            continue

        if field_name not in data:
            missing.append(current_path)
            continue

        val = data[field_name]
        sub_model, kind = _resolve_submodel(field_info.annotation)

        if kind == "model" and sub_model is not None:
            if isinstance(val, dict):
                _audit_model(sub_model, val, current_path, missing, extra)
        elif kind == "list" and sub_model is not None:
            if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                _audit_model(sub_model, val[0], current_path, missing, extra)
        elif kind == "dict" and sub_model is not None and isinstance(val, dict) and len(val) > 0:
            first_val = next(iter(val.values()))
            if isinstance(first_val, dict):
                _audit_model(sub_model, first_val, current_path, missing, extra)


def audit_gui_parity(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    """
    Compare serialized GUI dictionary against AppConfig schema models dynamically.
    Returns: (missing_fields, extra_fields)
    """
    missing: list[str] = []
    extra: list[str] = []

    _audit_model(AppConfig, data, "", missing, extra)

    return sorted(missing), sorted(extra)
