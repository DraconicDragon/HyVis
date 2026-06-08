"""
config.py Configuration dataclasses and TOML loading.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib  # stdlib in Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-reattr]
    except ImportError:
        sys.exit("ERROR: TOML support not found. Python 3.11+ includes it by default, or install: pip install tomli")

# region Constants

#: MIME types
# todo find better solution
ALLOWED_MIMES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "image/bmp",
        "image/jxl",
        "image/avif",
        "image/heic",
        "image/heif",
        # "image/gif",
    }
)

# region Config dataclasses  


@dataclass(frozen=True)
class FileQueryConfig:
    """One search query issued to Hydrus to collect candidate files."""

    tags: list[str]
    """Each tag is a separate string (Hydrus tags can contain commas/spaces)."""

    tag_service_keys: list[str] = field(default_factory=list)
    """Tag service keys to search within. Empty → Hydrus default (all known tags)."""


@dataclass(frozen=True)
class OutputTagService:
    """A Hydrus tag service where inference results will be written."""

    key: str


@dataclass(frozen=True)
class ModelConfig:
    """Per-model configuration."""

    model_id: str
    source: str | None = None
    device: str = "auto"
    backend: str | None = None  # None → auto-select
    precision: str = "auto"
    batch_size: int = 4


@dataclass(frozen=True)
class InferenceConfig:
    """Global inference settings shared across all models."""

    models: list[ModelConfig]

    prefer_tag_level_thresholds: bool = True
    """Use TagLevelThresholds when the model supports it (best_threshold column in CSV)."""

    tag_level_threshold_multiplier: float = 0.0
    """Proportional multiplier for TagLevelThresholds. 0.1 → thresholds reduced by 10%."""

    default_threshold: float = 0.4
    """Global score threshold for ScoreThresholds (fallback)."""

    category_thresholds: dict[str, float] = field(default_factory=dict)
    """Per-category score threshold overrides for ScoreThresholds."""

    output_categories: list[str] = field(default_factory=list)
    """Only emit tags from these categories. Empty list → all categories."""

    log_level: str = "INFO"
    """Log level."""


@dataclass(frozen=True)
class HydrusConfig:
    """Hydrus connection + search/output settings."""

    api_url: str
    api_key: str
    file_queries: list[FileQueryConfig]
    output_tag_services: list[OutputTagService]


@dataclass(frozen=True)
class DatabaseConfig:
    path: str = "hyvis.db"


@dataclass(frozen=True)
class AppConfig:
    hydrus: HydrusConfig
    inference: InferenceConfig
    tag_prefix_mapping: dict[str, str]
    database: DatabaseConfig

    # region Factory

    @classmethod
    def from_file(cls, path: Path) -> "AppConfig":
        with path.open("rb") as fh:
            raw: dict[str, Any] = tomllib.load(fh)
        return cls._parse(raw)

    @classmethod
    def _parse(cls, data: dict[str, Any]) -> "AppConfig":
        # hydrus
        h = data.get("hydrus", {})
        file_queries = [
            FileQueryConfig(
                tags=list(q["tags"]),
                tag_service_keys=list(q.get("tag_service_keys", [])),
            )
            for q in h.get("file_queries", [])
        ]
        output_services = [OutputTagService(key=str(s["key"])) for s in h.get("output_tag_services", [])]
        hydrus = HydrusConfig(
            api_url=str(h.get("api_url", "")).rstrip("/"),
            api_key=str(h.get("api_key", "")),
            file_queries=file_queries,
            output_tag_services=output_services,
        )

        # inference
        inf = data.get("inference", {})
        models = [
            ModelConfig(
                model_id=str(m["model_id"]),
                source=str(m["source"]) if m.get("source") else None,
                device=str(m.get("device", "auto")),
                backend=str(m["backend"]) if "backend" in m else None,
                precision=str(m.get("precision", "auto")),
                batch_size=int(m.get("batch_size", 4)),
            )
            for m in inf.get("models", [])
        ]
        inference = InferenceConfig(
            models=models,
            prefer_tag_level_thresholds=bool(inf.get("prefer_tag_level_thresholds", True)),
            tag_level_threshold_multiplier=float(inf.get("tag_level_threshold_multiplier", 0.0)),
            default_threshold=float(inf.get("default_threshold", 0.4)),
            category_thresholds={str(k): float(v) for k, v in inf.get("category_thresholds", {}).items()},
            output_categories=list(inf.get("output_categories", [])),
            log_level=str(inf.get("log_level", "INFO")).upper(),
        )

        # tag prefix mapping
        prefix_mapping = {str(k): str(v) for k, v in data.get("tag_prefix_mapping", {}).items()}

        # database
        db_data = data.get("database", {})
        database = DatabaseConfig(path=str(db_data.get("path", "hyvis.db")))

        return cls(
            hydrus=hydrus,
            inference=inference,
            tag_prefix_mapping=prefix_mapping,
            database=database,
        )

    # region Validation

    def validate(self) -> list[str]:
        """Return a list of human-readable validation errors (empty → OK)."""
        errors: list[str] = []

        if not self.hydrus.api_url:
            errors.append("[hydrus] api_url is required")
        if not self.hydrus.api_key:
            errors.append("[hydrus] api_key is required")
        if not self.hydrus.file_queries:
            errors.append("[hydrus] At least one [[hydrus.file_queries]] entry is required")
        if not self.hydrus.output_tag_services:
            errors.append("[hydrus] At least one [[hydrus.output_tag_services]] entry is required")

        if not self.inference.models:
            errors.append("[inference] At least one [[inference.models]] entry is required")
        for i, m in enumerate(self.inference.models):
            if not m.model_id:
                errors.append(f"[[inference.models]][{i}]: model_id is required")
            if m.batch_size < 1:
                errors.append(f"[[inference.models]][{i}]: batch_size must be ≥ 1")

        multiplier = self.inference.tag_level_threshold_multiplier
        if not (0.0 <= multiplier < 1.0):
            errors.append("[inference] tag_level_threshold_multiplier must be in [-1.0, 1.0)")

        threshold = self.inference.default_threshold
        if not (0.0 <= threshold <= 1.0):
            errors.append("[inference] default_threshold must be in [0.0, 1.0]")

        for cat, val in self.inference.category_thresholds.items():
            if not (0.0 <= val <= 1.0):
                errors.append(f"[inference.category_thresholds] '{cat}' must be in [0.0, 1.0]")

        if self.inference.log_level not in ["DEBUG", "INFO", "WARNING", "ERROR"]:
            errors.append("[inference] log_level must be one of DEBUG, INFO, WARNING, ERROR")

        return errors
