"""
settings.py — Persistent GUI preferences, path resolution, and crash recovery storage.
"""

from __future__ import annotations

import logging
import os
import sys
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class SettingsSignals(QObject):
    """Global broadcast signals for runtime GUI preferences."""

    spinbox_format_changed = Signal(int, float)  # (decimals, step)


settings_signals = SettingsSignals()

# Module-level cached instance
_CACHED_SETTINGS: GuiSettings | None = None


# region OS Path Resolution


def get_user_config_dir() -> Path:
    """
    Return the system-standard user configuration directory for HyVis.
      - Windows: %APPDATA%/hyvis/
      - macOS: ~/Library/Application Support/hyvis/
      - Linux / BSD: $XDG_CONFIG_HOME/hyvis/ (fallback: ~/.config/hyvis/)
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
        return Path(base) / "hyvis"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "hyvis"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
        return Path(xdg) / "hyvis"


def get_settings_file_path() -> Path:
    """Return the absolute path to settings.toml."""
    return get_user_config_dir() / "settings.toml"


def get_session_tmp_path() -> Path:
    """Return the absolute path to the intermediate crash/session recovery file."""
    return get_user_config_dir() / "session.tmp.toml"


# endregion


# region Settings Model


class StartupBehavior(StrEnum):
    TEMPLATE = "template"  # Load built-in clean default template
    CUSTOM_PRESET = "custom_preset"  # Load user's specified default config file
    LAST_SESSION = "last_session"  # Reopen the last active configuration file


class GuiSettings(BaseModel):
    """Persistent desktop interface configuration."""

    model_config = ConfigDict(validate_assignment=True)

    # Startup & Loading
    startup_behavior: StartupBehavior = StartupBehavior.TEMPLATE
    custom_preset_path: str | None = None
    auto_connect_hydrus: bool = True
    prompt_backup_before_launch: bool = True

    # History & Recent Files (MRU, max 10)
    recent_configs: list[str] = Field(default_factory=list)
    last_opened_config: str | None = None

    # Future UI Preferences
    spinbox_decimals: int = 2
    spinbox_step: float = 0.01

    def add_recent_config(self, path: Path | str) -> None:
        """Add or bubble up a configuration file path in the MRU history list."""
        resolved = str(Path(path).resolve())
        # Deduplicate and place at the head of the list
        self.recent_configs = [p for p in self.recent_configs if p != resolved]
        self.recent_configs.insert(0, resolved)
        self.recent_configs = self.recent_configs[:10]
        self.last_opened_config = resolved


# endregion


# region Persistence & Session Scratchpad Helpers


def load_gui_settings() -> GuiSettings:
    """Load settings.toml from user config directory, falling back to defaults if missing or corrupted."""
    path = get_settings_file_path()
    if not path.is_file():
        return GuiSettings()

    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
        return GuiSettings.model_validate(raw)
    except Exception as exc:
        logger.warning("Could not read settings from '%s': %s (using defaults)", path, exc)
        return GuiSettings()


def get_gui_settings() -> GuiSettings:
    """Return the cached in-memory GuiSettings, loading from disk on first call."""
    global _CACHED_SETTINGS
    if _CACHED_SETTINGS is None:
        _CACHED_SETTINGS = load_gui_settings()
    return _CACHED_SETTINGS


def save_gui_settings(settings: GuiSettings) -> bool:
    """Write GUI settings to settings.toml and broadcast changes to active widgets."""
    global _CACHED_SETTINGS
    try:
        folder = get_user_config_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = get_settings_file_path()

        data = settings.model_dump(mode="json", exclude_none=True)
        toml_str = tomli_w.dumps(data)
        path.write_text(toml_str, encoding="utf-8")

        # Keep in-memory singleton updated
        _CACHED_SETTINGS = settings

        # Broadcast updated precision and stepping to all live inputs
        settings_signals.spinbox_format_changed.emit(settings.spinbox_decimals, settings.spinbox_step)
        return True
    except Exception as exc:
        logger.error("Failed to save GUI settings: %s", exc)
        return False


def has_session_tmp() -> bool:
    """Return True if an unsaved session scratchpad exists on disk."""
    return get_session_tmp_path().is_file()


def save_session_tmp(raw_data: dict[str, Any]) -> bool:
    """Persist active unsaved GUI state to session.tmp.toml."""
    from hyvis.gui.state import _prune_none

    try:
        folder = get_user_config_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = get_session_tmp_path()

        data = _prune_none(raw_data)
        toml_str = tomli_w.dumps(data)
        path.write_text(toml_str, encoding="utf-8")
        return True
    except Exception as exc:
        logger.debug("Failed to write session scratchpad: %s", exc)
        return False


def delete_session_tmp() -> None:
    """Remove session.tmp.toml if it exists."""
    path = get_session_tmp_path()
    try:
        if path.is_file():
            path.unlink()
    except OSError as exc:
        logger.debug("Could not delete session tmp file: %s", exc)
