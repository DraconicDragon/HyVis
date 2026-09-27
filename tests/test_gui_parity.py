import pytest


def test_gui_exposes_all_config_settings():
    try:
        from PySide6.QtWidgets import QApplication

        from hyvis.gui.main_window import MainWindow
        from hyvis.gui.parity import audit_gui_parity
    except ImportError:
        pytest.skip("PySide6 not installed")

    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    gathered_dict = window._gather_config_dict()
    missing, extra = audit_gui_parity(gathered_dict)

    assert not missing, f"GUI is missing the following fields from config.py: {missing}"
    assert not extra, f"GUI produces unknown fields not in config.py: {extra}"
