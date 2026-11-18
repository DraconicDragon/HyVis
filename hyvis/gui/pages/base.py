"""
base.py — Baseline class and lifecycle contract for all HyVis configuration pages.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from hyvis.config import AppConfig
from hyvis.gui.base_widgets import SmoothScrollArea
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
        self._is_loading_ui: bool = False

    @contextmanager
    def suppress_changes(self) -> Iterator[None]:
        """Temporarily suppress `changed` signals while programmatically populating UI controls."""
        prev = self._is_loading_ui
        self._is_loading_ui = True
        try:
            yield
        finally:
            self._is_loading_ui = prev

    def emit_changed(self) -> None:
        """Emit `changed` only if UI is not currently loading config state."""
        if not self._is_loading_ui:
            self.changed.emit()

    def create_scroll_layout(
        self,
        *,
        margins: tuple[int, int, int, int] = (12, 12, 0, 12),
        spacing: int = 12,
    ) -> tuple[QWidget, QVBoxLayout]:
        """Set up a standard borderless SmoothScrollArea on this page and return (container, layout)."""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = SmoothScrollArea(self, scroll_margin=(8, 4))
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(*margins)
        layout.setSpacing(spacing)

        scroll.setWidget(container)
        root.addWidget(scroll)
        return container, layout

    def load_config(self, cfg: AppConfig) -> None:
        """Populate page controls from an incoming AppConfig instance."""
        raise NotImplementedError

    def apply_to_dict(self, data: dict[str, Any]) -> None:
        """Serialize active page controls into the target dictionary."""
        raise NotImplementedError
