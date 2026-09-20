"""
state.py — Central state management, Pydantic synchronization, and live Hydrus entity sourcing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import tomli_w
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from hyvis.config import AppConfig

logger = logging.getLogger(__name__)


def _prune_none(obj: Any) -> Any:
    """Recursively prune None values from dictionaries and collections since TOML does not support null."""
    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            if v is not None:
                pruned = _prune_none(v)
                if pruned is not None:
                    cleaned[k] = pruned
        return cleaned
    if isinstance(obj, (list, tuple)):
        return [_prune_none(v) for v in obj if v is not None]
    return obj


# Minimal starter template for new configurations (starts with clean empty categories)
_DEFAULT_CONFIG_DICT: dict[str, Any] = {
    "hydrus": {
        "api_url": "http://127.0.0.1:45869",
        "api_key": "",
        "no_wait": False,
        "tag_queries": [{"tags": ["system:untagged"]}],
        "output_tag_services": {"keys": [""]},
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
        "output_categories": [],
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


class _HydrusWorkerSignals(QObject):
    """Signals emitted by background Hydrus connection worker."""

    success = Signal(dict, dict, str)  # (all_services, writable_services, version_str)
    error = Signal(str)  # error message


class _HydrusFetchWorker(QRunnable):
    """Worker task that queries Hydrus in a background thread."""

    def __init__(self, api_url: str, api_key: str) -> None:
        super().__init__()
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.signals = _HydrusWorkerSignals()

    def run(self) -> None:
        from hyvis.hydrus import HydrusClient, HydrusConnectionError, HydrusError

        try:
            client = HydrusClient(self.api_url, self.api_key)
            client.verify_connection()

            # Retrieve version info
            version_str = "unknown"
            try:
                v_info = client.get_version_info()
                version_str = str(
                    v_info.get("hydrus_version") or v_info.get("client_version") or v_info.get("version") or "unknown"
                )
            except Exception:
                pass

            # Retrieve services
            resp = client.get_services()
            raw_services = resp.get("services", {})

            all_tag_services: dict[str, str] = {}
            writable_tag_services: dict[str, str] = {}

            for key, info in raw_services.items():
                name = str(info.get("name", key))
                type_pretty = str(info.get("type_pretty", "")).lower()
                stype = info.get("type")

                # Tag services have 'tag' in type_pretty or type in (0, 5)
                is_tag_domain = "tag" in type_pretty or stype in (0, 5)
                if is_tag_domain:
                    all_tag_services[key] = name

                    # Writable services: local tag domains (5) or tag repositories (0)
                    # Excludes virtual read-only "all known tags"
                    is_all_known = "all known tags" in name.lower() or stype == 10
                    if not is_all_known:
                        writable_tag_services[key] = name

            self.signals.success.emit(all_tag_services, writable_tag_services, version_str)

        except HydrusConnectionError as exc:
            self.signals.error.emit(f"Offline: {exc}")
        except HydrusError as exc:
            self.signals.error.emit(f"API Error: {exc}")
        except Exception as exc:
            self.signals.error.emit(f"Connection failed: {exc}")


class ConfigState(QObject):
    """Manages the lifecycle, validation, and live Hydrus entity sourcing of an AppConfig."""

    config_loaded = Signal(object)  # Emits AppConfig instance on open / reset
    config_saved = Signal(Path)  # Emits Path when saved to disk
    dirty_changed = Signal(bool)  # Emits True if unsaved changes exist
    validation_changed = Signal(list)  # Emits list of human-readable error strings

    # Hydrus entity sourcing signals
    services_updated = Signal(dict, dict)  # (all_tag_services, writable_tag_services)
    connection_changed = Signal(str, str)  # (status, display_message)

    def __init__(self) -> None:
        super().__init__()
        self._current_path: Path | None = None
        self._is_dirty: bool = False
        self._config: AppConfig | None = None

        # Hydrus sourcing state
        self._tag_services: dict[str, str] = {}
        self._writable_tag_services: dict[str, str] = {}
        self._connection_status: str = "offline"
        self._connection_info: str = "Not connected"

        # Start with default template
        self.new_config()

    # region Properties

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

    @property
    def tag_services(self) -> dict[str, str]:
        """All tag services (including read-only services like All Known Tags)."""
        return dict(self._tag_services)

    @property
    def writable_tag_services(self) -> dict[str, str]:
        """Writable destination tag services only."""
        return dict(self._writable_tag_services)

    @property
    def connection_status(self) -> str:
        """One of: 'connected', 'connecting', 'offline', 'error'."""
        return self._connection_status

    @property
    def connection_info(self) -> str:
        """Display label for connection status."""
        return self._connection_info

    # endregion

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

            # Auto-sync services if credentials are present
            if loaded.hydrus.api_url and loaded.hydrus.api_key:
                self.sync_hydrus_services(loaded.hydrus.api_url, loaded.hydrus.api_key)

            return True
        except Exception as exc:
            logger.error("Failed to load config '%s': %s", file_path, exc)
            return False

    def save_to_file(
        self,
        path: Path | str | None = None,
        raw_data: dict[str, Any] | None = None,
    ) -> bool:
        """Serialize and save the current configuration to disk as valid TOML."""
        target_path = Path(path).resolve() if path else self._current_path
        if target_path is None:
            raise ValueError("No file path specified for saving.")

        try:
            if raw_data is not None:
                data = _prune_none(raw_data)
            elif self._config is not None:
                raw_dump = self._config.model_dump(mode="json", exclude_none=True)
                data = _prune_none(raw_dump)
            else:
                return False

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
            raise

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

    # region Live Hydrus Sourcing

    def sync_hydrus_services(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """Fetch Hydrus services in a background thread without freezing the UI."""
        cfg = self.config
        url = api_url or (cfg.hydrus.api_url if cfg else "")
        key = api_key or (cfg.hydrus.api_key if cfg else "")

        if not url or not key:
            self._connection_status = "offline"
            self._connection_info = "Credentials missing"
            self.connection_changed.emit(self._connection_status, self._connection_info)
            return

        self._connection_status = "connecting"
        self._connection_info = "Connecting..."
        self.connection_changed.emit(self._connection_status, self._connection_info)

        worker = _HydrusFetchWorker(url, key)
        worker.signals.success.connect(self._on_fetch_success)
        worker.signals.error.connect(self._on_fetch_error)
        QThreadPool.globalInstance().start(worker)

    def _on_fetch_success(
        self,
        all_tags: dict[str, str],
        writable_tags: dict[str, str],
        version_str: str,
    ) -> None:
        self._tag_services = all_tags
        self._writable_tag_services = writable_tags
        self._connection_status = "connected"
        self._connection_info = f"Connected: Hydrus v{version_str}"
        self.services_updated.emit(self._tag_services, self._writable_tag_services)
        self.connection_changed.emit(self._connection_status, self._connection_info)

    def _on_fetch_error(self, err_msg: str) -> None:
        self._connection_status = "error"
        self._connection_info = err_msg
        self.connection_changed.emit(self._connection_status, self._connection_info)

    # endregion
