"""
test_gui_parity.py — Guarantees every Pydantic setting is represented in the GUI.
"""

from __future__ import annotations

import pytest

from hyvis.gui.parity import audit_gui_parity


def test_gui_exposes_all_config_settings() -> None:
    try:
        from PySide6.QtWidgets import QApplication

        from hyvis.gui.main_window import MainWindow
    except ImportError:
        pytest.skip("PySide6 not installed")

    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    # 1. Enable preview and populate 1 dummy entry in each stacked list
    # so every sub-component serializes its structure into gathered_dict
    window.hydrus_page.prev_card.setChecked(True)
    window.hydrus_page.tag_queries_editor._add_card(["solo"], ["svc1"])
    window.hydrus_page.page_queries_editor._add_row({"name": "test_page", "index": 0})
    window.hydrus_page.add_tags_editor._add_card(["ai:tagged"], ["svc1"])
    window.hydrus_page.rem_tags_editor._add_card(["temp:tag"], ["svc1"])

    window.models_page.svc_card.setChecked(True)

    window.filters_page.cat_thresh_card.setChecked(True)
    window.filters_page.cat_thresh_editor._on_add_row()
    window.filters_page.tag_thresh_card.setChecked(True)
    window.filters_page.tag_thresh_editor._on_add_row()
    window.filters_page.subset_card.setChecked(True)
    window.filters_page.subset_editor._add_card(["safe"], 1)

    # 2. Gather dictionary directly from live UI widgets
    gathered_dict = window._gather_config_dict()

    # 3. Audit parity against schema models
    missing, extra = audit_gui_parity(gathered_dict)

    assert not missing, f"GUI is missing the following fields from config.py:\n  {missing}"
    assert not extra, f"GUI produces unknown fields not in config.py:\n  {extra}"
