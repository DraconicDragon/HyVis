"""
test_schema_binding.py — Tests for config.py UI metadata completeness and Qt widget SSOT binding.
"""

from __future__ import annotations

import os
from typing import Literal

import pytest
from pydantic import BaseModel, Field

import hyvis.config as config_mod
from hyvis.config import StrictBaseModel, VideoConfig, field_meta
from hyvis.gui.widgets import bind_field_metadata, setup_field_tooltip, validate_field_ui_metadata


def test_all_config_models_have_valid_ui_metadata() -> None:
    """Sweep every StrictBaseModel in hyvis.config to guarantee titles, descriptions, and Literal labels."""
    checked = 0
    for attr_name in dir(config_mod):
        obj = getattr(config_mod, attr_name)
        if isinstance(obj, type) and issubclass(obj, StrictBaseModel) and obj is not StrictBaseModel:
            for field_name, field_info in obj.model_fields.items():
                validate_field_ui_metadata(field_info, field_label=f"{obj.__name__}.{field_name}")
                checked += 1

    assert checked > 0


def test_bind_metadata_rejects_missing_or_mismatched_metadata() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QComboBox, QWidget

    _app = QApplication.instance() or QApplication([])

    class _BadMeta(BaseModel):
        no_title: int = Field(default=1, description="Has desc")
        no_desc: int = Field(default=1, title="Has title")
        bad_str_labels: str = Field(
            default="a",
            title="Str",
            description="Desc",
            json_schema_extra=field_meta(labels={"a": "A"}),
        )
        mismatched_labels: Literal["a", "b"] = Field(
            default="a",
            title="Lit",
            description="Desc",
            json_schema_extra=field_meta(labels={"a": "A"}),
        )

    w = QWidget()
    with pytest.raises(ValueError, match="title"):
        bind_field_metadata(w, _BadMeta.model_fields["no_title"])
    with pytest.raises(ValueError, match="description"):
        setup_field_tooltip(w, _BadMeta.model_fields["no_desc"])
    with pytest.raises(TypeError, match="Literal"):
        bind_field_metadata(QComboBox(), _BadMeta.model_fields["bad_str_labels"])
    with pytest.raises(ValueError, match="do not match"):
        bind_field_metadata(QComboBox(), _BadMeta.model_fields["mismatched_labels"])


def test_qt_bind_field_metadata_applies_bounds_defaults_and_labels() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QSpinBox

    from hyvis.gui.base_widgets import FloatSpinBox

    _app = QApplication.instance() or QApplication([])
    v_fields = VideoConfig.model_fields

    # 1. QCheckBox binds title, tooltip, objectName, and default=False
    chk = QCheckBox()
    bind_field_metadata(chk, v_fields["enabled"], field_name="enabled")
    assert chk.objectName() == "enabled"
    assert "Video" in chk.text()
    assert chk.isChecked() is False
    assert chk.toolTip() == v_fields["enabled"].description

    # 2. QSpinBox binds bounds [1, 7200] and default=128
    spin = QSpinBox()
    bind_field_metadata(spin, v_fields["max_frames"], field_name="max_frames")
    assert spin.minimum() == 1
    assert spin.maximum() == 7200
    assert spin.value() == 128

    # 3. FloatSpinBox tracks strict gt=0.0 dynamically across decimal precision changes
    fspin = FloatSpinBox()
    bind_field_metadata(fspin, v_fields["density_k"], field_name="density_k")
    assert fspin.maximum() == 60.0
    assert fspin.value() == 2.0
    assert fspin.minimum() > 0.0
    fspin._apply_format(3, 0.01)
    assert fspin.minimum() == pytest.approx(0.001)

    # 4. Non-editable Literal QComboBox populates display labels + userData and selects default
    combo = QComboBox()
    bind_field_metadata(combo, v_fields["frame_cache_mode"], field_name="frame_cache_mode")
    assert combo.count() == 3
    assert combo.currentData() == "auto"
    assert "Auto" in combo.currentText()

    # 5. Editable QComboBox is NOT overwritten
    ed_combo = QComboBox()
    ed_combo.setEditable(True)
    ed_combo.addItem("custom_item")
    bind_field_metadata(ed_combo, v_fields["frame_cache_mode"])
    assert ed_combo.count() == 1 and ed_combo.itemText(0) == "custom_item"


def test_bind_field_metadata_does_not_emit_signals_and_pages_instantiate() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QSpinBox

    from hyvis.gui.base_widgets import FloatSpinBox
    from hyvis.gui.pages import AppDbPage, FiltersPage, HydrusPage, ModelsPage, VideoPage

    _app = QApplication.instance() or QApplication([])
    v_fields = VideoConfig.model_fields

    fired: list[str] = []
    spin = QSpinBox()
    spin.valueChanged.connect(lambda _: fired.append("int"))
    bind_field_metadata(spin, v_fields["min_frames"])

    fspin = FloatSpinBox()
    fspin.valueChanged.connect(lambda _: fired.append("float"))
    bind_field_metadata(fspin, v_fields["density_k"])

    assert fired == []
    assert spin.signalsBlocked() is False
    assert fspin.signalsBlocked() is False

    # Smoke-instantiate all 5 config pages
    for page_cls in (HydrusPage, VideoPage, ModelsPage, FiltersPage, AppDbPage):
        page_cls()
