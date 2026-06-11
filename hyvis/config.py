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
class CategoryThresholdConfig:
    """
    Threshold settings for one output category.

    threshold    Threshold score (0.0–1.0).
    override_tlt If True this threshold also overrides TagLevelThresholds for
                 this category, not just ScoreThresholds.
    """

    threshold: float
    override_tlt: bool = False


@dataclass(frozen=True)
class TagThresholdConfig:
    """
    Threshold settings for one specific tag.

    threshold    Threshold score (0.0–1.0).
    override_tlt If True this threshold also overrides TagLevelThresholds for
                 this tag, not just ScoreThresholds.
    """

    threshold: float
    override_tlt: bool = False


@dataclass(frozen=True)
class OutputFilterConfig:
    """
    Controls which tags are emitted and how they are transformed.

    Applied after inference, before pushing to Hydrus.

    Global values are set under [output_filter].
    Per-model overrides are set inside [[inference.models]] as an
    [output_filter] sub-table; missing fields fall back to the global config.
    """

    # --- Threshold settings ---

    prefer_tag_level_thresholds: bool = True
    """Use TagLevelThresholds when the model supports it."""

    tag_level_threshold_relative_offset: float = 0.0
    """Relative offset for TagLevelThresholds. 0.1 → each threshold * 1.1."""

    default_threshold: float = 0.4
    """Global fallback score threshold."""

    category_thresholds: dict[str, CategoryThresholdConfig] = field(default_factory=dict)
    """Per-category threshold overrides. Keys are category names."""

    tag_thresholds: dict[str, TagThresholdConfig] = field(default_factory=dict)
    """Per-tag threshold overrides. Keys are raw tag names (before prefix)."""

    # --- Output selection ---

    tag_prefix_mapping: dict[str, str] = field(default_factory=dict)
    """Maps category name → tag prefix applied before writing to Hydrus."""

    max_tags_per_category: dict[str, int] = field(default_factory=dict)
    """
    Maximum number of tags to emit per category.
    Tags are selected by descending score.  Omitted categories → no limit.
    """

    output_categories: list[str] = field(default_factory=list)
    """Only emit tags from these categories. Empty → all categories."""

    include_tags: list[str] = field(default_factory=list)
    """Tags that are always included, bypassing output_categories."""

    exclude_tags: list[str] = field(default_factory=list)
    """Tags that are always excluded, even if their category is allowed."""

    # Internal: raw keys present in TOML for this section.
    # Used by resolved_output_filter() to distinguish "not set" from
    # "set to the same value as the default".
    _raw_keys: frozenset[str] = field(default_factory=frozenset, compare=False, hash=False)


@dataclass(frozen=True)
class ModelConfig:
    """Per-model configuration."""

    model_id: str
    source: str | None = None
    device: str = "auto"
    backend: str | None = None  # None → auto-select
    precision: str = "auto"
    batch_size: int = 4

    output_filter: OutputFilterConfig | None = None
    """
    Per-model output filter overrides.  Fields present in the model's
    [output_filter] sub-table replace the corresponding global field;
    all others fall back to the global OutputFilterConfig.
    """

    output_tag_services: list[OutputTagService] | None = None
    """
    Per-model output tag services.  If set, REPLACES the global list for this
    model.  If None, the global hydrus.output_tag_services list is used.
    """


@dataclass(frozen=True)
class InferenceConfig:
    """Model list and per-model configuration."""

    models: list[ModelConfig]


@dataclass(frozen=True)
class HydrusConfig:
    """Hydrus connection + search/output settings."""

    api_url: str
    api_key: str
    file_queries: list[FileQueryConfig]
    output_tag_services: list[OutputTagService]
    """Global output tag services."""


@dataclass(frozen=True)
class DatabaseConfig:
    path: str = "hyvis.db"


@dataclass(frozen=True)
class HyvisConfig:
    """Application-level settings."""

    log_level: str = "WARNING"
    """
    Log level for hyvis and vibe.
    One of DEBUG, INFO, WARNING, ERROR.
    Can be overridden at runtime with --log-level.
    """


@dataclass(frozen=True)
class AppConfig:
    hydrus: HydrusConfig
    inference: InferenceConfig
    output_filter: OutputFilterConfig
    """Global output filter defaults."""
    database: DatabaseConfig
    hyvis: HyvisConfig

    # region Helpers

    def resolved_output_filter(self, model_cfg: ModelConfig) -> OutputFilterConfig:
        """
        Return the effective OutputFilterConfig for a model.

        Per-model fields that were explicitly present in TOML override the
        corresponding global field; all others fall back to the global config.
        """
        m = model_cfg.output_filter
        if m is None:
            return self.output_filter
        g = self.output_filter
        rk = m._raw_keys  # keys explicitly set in the model's output_filter block

        def _pick(key: str, model_val: Any, global_val: Any) -> Any:
            return model_val if key in rk else global_val

        return OutputFilterConfig(
            prefer_tag_level_thresholds=_pick(
                "prefer_tag_level_thresholds",
                m.prefer_tag_level_thresholds,
                g.prefer_tag_level_thresholds,
            ),
            tag_level_threshold_relative_offset=_pick(
                "tag_level_threshold_relative_offset",
                m.tag_level_threshold_relative_offset,
                g.tag_level_threshold_relative_offset,
            ),
            default_threshold=_pick("default_threshold", m.default_threshold, g.default_threshold),
            category_thresholds=_pick("category_thresholds", m.category_thresholds, g.category_thresholds),
            tag_thresholds=_pick("tag_thresholds", m.tag_thresholds, g.tag_thresholds),
            tag_prefix_mapping=_pick("tag_prefix_mapping", m.tag_prefix_mapping, g.tag_prefix_mapping),
            max_tags_per_category=_pick("max_tags_per_category", m.max_tags_per_category, g.max_tags_per_category),
            output_categories=_pick("output_categories", m.output_categories, g.output_categories),
            include_tags=_pick("include_tags", m.include_tags, g.include_tags),
            exclude_tags=_pick("exclude_tags", m.exclude_tags, g.exclude_tags),
        )

    def resolved_output_tag_services(self, model_cfg: ModelConfig) -> list[OutputTagService]:
        """
        Return the effective output tag service list for a model.

        If the model specifies its own list it REPLACES the global list entirely.
        Otherwise the global hydrus.output_tag_services list is used.
        """
        if model_cfg.output_tag_services is not None:
            return model_cfg.output_tag_services
        return self.hydrus.output_tag_services

    # region Factory

    @classmethod
    def from_file(cls, path: Path) -> "AppConfig":
        with path.open("rb") as fh:
            raw: dict[str, Any] = tomllib.load(fh)
        return cls._parse(raw)

    @classmethod
    def _parse(cls, data: dict[str, Any]) -> "AppConfig":
        # --- [hyvis] ---
        hv = data.get("hyvis", {})
        hyvis = HyvisConfig(
            log_level=str(hv.get("log_level", "WARNING")).upper(),
        )

        # --- [hydrus] ---
        h = data.get("hydrus", {})
        file_queries = [
            FileQueryConfig(
                tags=list(q["tags"]),
                tag_service_keys=list(q.get("tag_service_keys", [])),
            )
            for q in h.get("file_queries", [])
        ]
        ots_data = h.get("output_tag_services", {})
        global_keys = ots_data.get("keys", []) if isinstance(ots_data, dict) else []
        global_output_services = [OutputTagService(key=str(k)) for k in global_keys]
        hydrus = HydrusConfig(
            api_url=str(h.get("api_url", "")).rstrip("/"),
            api_key=str(h.get("api_key", "")),
            file_queries=file_queries,
            output_tag_services=global_output_services,
        )

        # --- [output_filter] ---
        global_filter = _parse_output_filter(data.get("output_filter", {}))

        # --- [inference] ---
        inf = data.get("inference", {})
        models = [_parse_model_config(m) for m in inf.get("models", [])]
        inference = InferenceConfig(models=models)

        # --- [database] ---
        db_data = data.get("database", {})
        database = DatabaseConfig(path=str(db_data.get("path", "hyvis.db")))

        return cls(
            hydrus=hydrus,
            inference=inference,
            output_filter=global_filter,
            database=database,
            hyvis=hyvis,
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

        all_models_override = bool(self.inference.models) and all(
            m.output_tag_services is not None for m in self.inference.models
        )
        if not self.hydrus.output_tag_services and not all_models_override:
            errors.append(
                "[hydrus] At least one output service key is required under [hydrus.output_tag_services].keys "
                "(or every [[inference.models]] entry must define its own output_tag_services)"
            )

        if not self.inference.models:
            errors.append("[inference] At least one [[inference.models]] entry is required")
        for i, m in enumerate(self.inference.models):
            if not m.model_id:
                errors.append(f"[[inference.models]][{i}]: model_id is required")
            if m.batch_size < 1:
                errors.append(f"[[inference.models]][{i}]: batch_size must be ≥ 1")
            if m.output_filter is not None:
                errors += _validate_output_filter(
                    m.output_filter,
                    f"[[inference.models]][{i}] output_filter",
                )

        errors += _validate_output_filter(self.output_filter, "[output_filter]")

        if self.hyvis.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            errors.append("[hyvis] log_level must be one of DEBUG, INFO, WARNING, ERROR")

        return errors


# region Parsing helpers


def _parse_output_filter(raw: dict[str, Any]) -> OutputFilterConfig:
    """Parse an [output_filter] table (global or per-model)."""
    category_thresholds: dict[str, CategoryThresholdConfig] = {}
    for cat, val in raw.get("category_thresholds", {}).items():
        category_thresholds[str(cat)] = _parse_cat_threshold_entry(val, cat)

    tag_thresholds: dict[str, TagThresholdConfig] = {}
    for tag, val in raw.get("tag_thresholds", {}).items():
        entry = _parse_cat_threshold_entry(val, tag)
        tag_thresholds[str(tag)] = TagThresholdConfig(threshold=entry.threshold, override_tlt=entry.override_tlt)

    max_tags: dict[str, int] = {str(k): int(v) for k, v in raw.get("max_tags_per_category", {}).items()}

    return OutputFilterConfig(
        prefer_tag_level_thresholds=bool(raw.get("prefer_tag_level_thresholds", True)),
        tag_level_threshold_relative_offset=float(raw.get("tag_level_threshold_relative_offset", 0.0)),
        default_threshold=float(raw.get("default_threshold", 0.4)),
        category_thresholds=category_thresholds,
        tag_thresholds=tag_thresholds,
        tag_prefix_mapping={str(k): str(v) for k, v in raw.get("tag_prefix_mapping", {}).items()},
        max_tags_per_category=max_tags,
        output_categories=list(raw.get("output_categories", [])),
        include_tags=list(raw.get("include_tags", [])),
        exclude_tags=list(raw.get("exclude_tags", [])),
        _raw_keys=frozenset(raw.keys()),
    )


def _parse_cat_threshold_entry(val: Any, key: str) -> CategoryThresholdConfig:
    """
    Parse a threshold entry that is either:
        key = 0.7                                       (plain float)
        key = { threshold = 0.7, override_tlt = true }  (inline table)
    """
    if isinstance(val, (int, float)):
        return CategoryThresholdConfig(threshold=float(val), override_tlt=False)
    if isinstance(val, dict):
        return CategoryThresholdConfig(
            threshold=float(val["threshold"]),
            override_tlt=bool(val.get("override_tlt", False)),
        )
    raise ValueError(f"'{key}': expected a number or {{threshold=…, override_tlt=…}}, got {type(val).__name__}")


def _parse_model_config(raw: dict[str, Any]) -> ModelConfig:
    per_model_filter: OutputFilterConfig | None = None
    if "output_filter" in raw:
        per_model_filter = _parse_output_filter(raw["output_filter"])

    per_model_services: list[OutputTagService] | None = None
    if "output_tag_services" in raw:
        ots_data = raw["output_tag_services"]
        model_keys = ots_data.get("keys", []) if isinstance(ots_data, dict) else []
        per_model_services = [OutputTagService(key=str(k)) for k in model_keys]

    return ModelConfig(
        model_id=str(raw["model_id"]),
        source=str(raw["source"]) if raw.get("source") else None,
        device=str(raw.get("device", "auto")),
        backend=str(raw["backend"]) if "backend" in raw else None,
        precision=str(raw.get("precision", "auto")),
        batch_size=int(raw.get("batch_size", 4)),
        output_filter=per_model_filter,
        output_tag_services=per_model_services,
    )


def _validate_output_filter(f: OutputFilterConfig, prefix: str) -> list[str]:
    errors: list[str] = []

    offset = f.tag_level_threshold_relative_offset
    if not (-1.0 <= offset < 1.0):
        errors.append(f"{prefix}: tag_level_threshold_relative_offset must be in [-1.0, 1.0)")

    if not (0.0 <= f.default_threshold <= 1.0):
        errors.append(f"{prefix}: default_threshold must be in [0.0, 1.0]")

    for cat, cfg in f.category_thresholds.items():
        if not (0.0 <= cfg.threshold <= 1.0):
            errors.append(f"{prefix} category_thresholds '{cat}': threshold must be in [0.0, 1.0]")

    for tag, cfg in f.tag_thresholds.items():
        if not (0.0 <= cfg.threshold <= 1.0):
            errors.append(f"{prefix} tag_thresholds '{tag}': threshold must be in [0.0, 1.0]")

    for cat, limit in f.max_tags_per_category.items():
        if limit < 1:
            errors.append(f"{prefix} max_tags_per_category '{cat}': must be ≥ 1")

    return errors
