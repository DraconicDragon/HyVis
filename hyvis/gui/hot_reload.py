"""Development-time hot reload support for the PySide6 GUI."""

from __future__ import annotations

import importlib
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

logger = logging.getLogger(__name__)

_RELOAD_BLACKLIST = {
    "__init__.py",
    "app.py",
    "main_window.py",
    "state.py",
    "settings.py",
    "launcher.py",
}
_PAGE_MODULES = (
    "hyvis.gui.pages.base",
    "hyvis.gui.pages.hydrus_page",
    "hyvis.gui.pages.models_page",
    "hyvis.gui.pages.filters_page",
    "hyvis.gui.pages.app_db_page",
)


def _source_snapshot(package_root: Path) -> dict[Path, tuple[int, int]]:
    snapshot: dict[Path, tuple[int, int]] = {}
    for path in package_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        snapshot[path] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


class HotReloadManager(QObject):
    """Watch GUI source files and notify the running window after reloads."""

    def __init__(
        self,
        parent: QObject,
        on_reload: Callable[[], None],
        package_root: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._package_root = package_root or Path(__file__).resolve().parent
        self._on_reload = on_reload
        self._snapshot = _source_snapshot(self._package_root)
        self._timer = QTimer(self)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._check_for_changes)

    def start(self) -> None:
        self._timer.start()

    def _check_for_changes(self) -> None:
        current_snapshot = _source_snapshot(self._package_root)
        if current_snapshot == self._snapshot:
            return

        changed_paths = set(current_snapshot) ^ set(self._snapshot)
        changed_paths.update(
            path
            for path in current_snapshot.keys() & self._snapshot.keys()
            if current_snapshot[path] != self._snapshot[path]
        )
        self._snapshot = current_snapshot

        for path in changed_paths:
            if self._reload_module(path):
                self._on_reload()
                print(f"[HyVis] Reloaded {path.relative_to(self._package_root)}", file=sys.stderr)

    def _reload_module(self, source_path: Path) -> bool:
        if source_path.name in _RELOAD_BLACKLIST or source_path.suffix != ".py":
            return False

        module_path = source_path.relative_to(self._package_root).with_suffix("")
        module_name = f"hyvis.gui.{'.'.join(module_path.parts)}"
        try:
            importlib.reload(importlib.import_module(module_name))
            for page_module in _PAGE_MODULES:
                importlib.reload(importlib.import_module(page_module))
        except Exception:
            logger.exception("Could not hot reload %s", source_path)
            return False
        return True
