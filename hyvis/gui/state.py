"""
state.py — Central state management and Pydantic synchronization for the GUI.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import tomli_w
from PySide6.QtCore import QObject, Signal

from hyvis.config import AppConfig

logger = logging.getLogger(__name__)

# Minimal starter template for new configurations
_DEFAULT_CONFIG_DICT: dict[str, Any] = {
    "hydrus": {
        "api_url": "http://127.0.0.1:45869",
        "api_key": "your_api_key_here",
        "no_wait": False,
        "tag_queries": [{"tags": ["system:untagged"]}],
        "output_tag_services": {"keys": ["your_output_tag_service_key_here"]},
    },
    "inference": {
        "models": [
            {
                "model_id": "wd-swinv2-v3",
                "device": "auto",
                "batch_size": 1,
            }
        ]
    },
    "output_filter": {
        "default_threshold": 0.4,
        "prefer_tag_level_thresholds": True,
        "output_categories": ["general", "character", "rating"],
    },
    "database": {
        "path": "data/hyvis.db",
        "cache_raw_predictions": True,
        "min_cache_score": 0.01,
    },
    "hyvis": {
        "log_level": "WARNING",
        "infer_only": False,
    },
}


class ConfigState(QObject):
    """Manages the lifecycle, file path, validation, and dirty state of an AppConfig."""

    config_loaded = Signal(object)  # Emits AppConfig instance on open / reset
    config_saved = Signal(Path)  # Emits Path when saved to disk
    dirty_changed = Signal(bool)  # Emits True if unsaved changes exist
    validation_changed = Signal(list)  # Emits list of human-readable error strings

    def __init__(self) -> None:
        super().__init__()
        self._current_path: Path | None = None
        self._is_dirty: bool = False
        self._config: AppConfig | None = None

        # Start with default template
        self.new_config()

    @property
    def current_path(self) -> Path | None:
        return self._current_path

    @property
    def is_dirty(self) -> bool:
        return self._is_dirty

    @property
    def config(self) -> AppConfig:
        assert self._config is not None
        return self._config

    def set_dirty(self, dirty: bool = True) -> None:
        if self._is_dirty != dirty:
            self._is_dirty = dirty
            self.dirty_changed.emit(self._is_dirty)

    def new_config(self) -> None:
        """Create a fresh default configuration."""
        self._config = AppConfig.model_validate(_DEFAULT_CONFIG_DICT)
        self._current_path = None
        self.set_dirty(False)
        self.config_loaded.emit(self._config)
        self.validate()

    def load_from_file(self, path: Path | str) -> bool:
        """Parse and load a TOML configuration file."""
        file_path = Path(path).resolve()
        try:
            loaded = AppConfig.from_file(file_path)
            self._config = loaded
            self._current_path = file_path
            self.set_dirty(False)
            self.config_loaded.emit(self._config)
            self.validate()
            logger.info("Loaded configuration from %s", file_path)
            return True
        except Exception as exc:
            logger.error("Failed to load config '%s': %s", file_path, exc)
            return False

    def save_to_file(self, path: Path | str | None = None) -> bool:
        """Serialize and save the current configuration to disk as TOML."""
        target_path = Path(path).resolve() if path else self._current_path
        if target_path is None:
            raise ValueError("No file path specified for saving.")

        if self._config is None:
            return False

        try:
            # Dump Pydantic model to clean dictionary and serialize to TOML
            data = self._config.model_dump(mode="json", exclude_defaults=False)
            toml_str = tomli_w.dumps(data)

            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(toml_str, encoding="utf-8")

            self._current_path = target_path
            self.set_dirty(False)
            self.config_saved.emit(target_path)
            logger.info("Saved configuration to %s", target_path)
            return True
        except Exception as exc:
            logger.error("Failed to save config to '%s': %s", target_path, exc)
            return False

    def update_config(self, new_config: AppConfig) -> None:
        """Update active configuration from page views and mark dirty."""
        self._config = new_config
        self.set_dirty(True)
        self.validate()

    def validate(self) -> list[str]:
        """Run business validation rules and emit validation status."""
        if self._config is None:
            errors = ["No configuration loaded."]
        else:
            errors = self._config.hyvis_validate()

        self.validation_changed.emit(errors)
        return errors
