"""
parity.py — Automated contract check to ensure the GUI exposes all Pydantic settings.
"""

from __future__ import annotations

from typing import Any

from hyvis.config import AppConfig


def get_expected_schema_keys() -> dict[str, set[str]]:
    """Return all field names grouped by top-level section from Pydantic models."""
    return {
        "hydrus": set(AppConfig.model_fields["hydrus"].annotation.model_fields.keys()),
        "output_filter": set(AppConfig.model_fields["output_filter"].annotation.model_fields.keys()),
        "database": set(AppConfig.model_fields["database"].annotation.model_fields.keys()),
        "hyvis": set(AppConfig.model_fields["hyvis"].annotation.model_fields.keys()),
    }


def audit_gui_parity(gui_data: dict[str, Any]) -> tuple[list[str], list[str]]:
    """
    Compare a dictionary produced by MainWindow._gather_config_dict()
    against AppConfig schema.

    Returns:
        (missing_fields, extra_fields)
    """
    expected = get_expected_schema_keys()
    missing: list[str] = []
    extra: list[str] = []

    for section, expected_keys in expected.items():
        actual_keys = set(gui_data.get(section, {}).keys())

        # Check for fields in config.py that the GUI forgot to include
        diff_missing = expected_keys - actual_keys
        for k in diff_missing:
            missing.append(f"[{section}].{k}")

        # Check for fields the GUI emitted that don't exist in config.py
        diff_extra = actual_keys - expected_keys
        for k in diff_extra:
            extra.append(f"[{section}].{k}")

    return missing, extra
