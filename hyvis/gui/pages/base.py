"""
base.py — Baseline class and lifecycle contract for all HyVis configuration pages.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from hyvis.config import AppConfig
from hyvis.gui.theme import ThemeSubscriberMixin


class BaseConfigPage(ThemeSubscriberMixin, QWidget):
    """
    Base widget for all primary configuration pages.

    Enforces the standard configuration lifecycle:
      - `changed`: Emitted when any setting is modified by the user.
      - `load_config(cfg)`: Populates UI components from a validated AppConfig.
      - `apply_to_dict(data)`: Serializes UI component states into a configuration dictionary.
      - Inherits `ThemeSubscriberMixin` for automatic self.tokens and refresh_theme().
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent)

    def load_config(self, cfg: AppConfig) -> None:
        """Populate page controls from an incoming AppConfig instance."""
        raise NotImplementedError

    def apply_to_dict(self, data: dict[str, Any]) -> None:
        """Serialize active page controls into the target dictionary."""
        raise NotImplementedError
