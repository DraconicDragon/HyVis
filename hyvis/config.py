"""
config.py Configuration models and TOML loading via Pydantic V2.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

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
        "image/heif",
        # "image/gif",
    }
)


# region Config Models


class TagQueryConfig(BaseModel, frozen=True):
    """One tag search query issued to Hydrus to collect candidate files."""

    tags: list[Any]
    """Each tag is a separate string. Nested lists evaluate as OR predicates."""

    tag_service_keys: list[str] = Field(default_factory=list)
    """Tag service keys to search within. Empty → Hydrus default (all known tags)."""


class PageQueryConfig(BaseModel, frozen=True):
    """Target a specific open page in the Hydrus client."""

    name: str = Field(min_length=1)
    """The exact name of the page tab in Hydrus."""

    index: int | None = None
    """Optional index (0-based) to disambiguate if multiple pages share the same name."""

    @model_validator(mode="after")
    def _validate_index(self) -> PageQueryConfig:
        if self.index is not None and self.index < 0:
            raise ValueError("'index' cannot be negative")
        return self


class PreviewConfig(BaseModel, frozen=True):
    """Target specific open pages for file previewing before inference."""

    page_name: str | None = None
    page_index: int | None = None
    rejected_page_name: str | None = None
    rejected_page_index: int | None = None

    @model_validator(mode="after")
    def _validate_indices(self) -> PreviewConfig:
        errors: list[str] = []
        if self.page_index is not None and self.page_index < 0:
            errors.append("page_index cannot be negative")
        if self.rejected_page_index is not None and self.rejected_page_index < 0:
            errors.append("rejected_page_index cannot be negative")
        if errors:
            raise ValueError("; ".join(errors))
        return self


class OutputTagServices(BaseModel, frozen=True):
    """A Hydrus tag service where inference results will be written."""

    keys: list[str] = Field(default_factory=list, min_length=1)


class AddTagConfig(BaseModel, frozen=True):
    """Rule specifying tags to add to successfully processed files."""

    tags: list[str] = Field(min_length=1)
    tag_service_keys: list[str] = Field(min_length=1)


class RemoveTagConfig(BaseModel, frozen=True):
    """Rule specifying tags to remove from successful files."""

    tags: list[str] = Field(min_length=1)
    tag_service_keys: list[str] = Field(min_length=1)


class CategoryThresholdConfig(BaseModel, frozen=True):
    """
    Threshold settings for one output category.

    threshold    Threshold score (0.0–1.0).
    override_tlt If True this threshold also overrides TagLevelThresholds for
                 this category, not just ScoreThresholds.
    """

    threshold: float = Field(ge=0.0, le=1.0)
    override_tlt: bool = False


class TagThresholdConfig(BaseModel, frozen=True):
    """
    Threshold settings for one specific tag.

    threshold    Threshold score (0.0–1.0).
    override_tlt If True this threshold also overrides TagLevelThresholds for
                 this tag, not just ScoreThresholds.
    """

    threshold: float = Field(ge=0.0, le=1.0)
    override_tlt: bool = False


class TagSubsetConfig(BaseModel, frozen=True):
    """
    A collection of tags subject to a collective output limit.
    Used to isolate and limit tags that belong to the same logical category.
    """

    tags: list[str]
    limit: int = Field(default=1, ge=1)


class OutputFilterConfig(BaseModel, frozen=True):
    """
    Controls which tags are emitted and how they are transformed.

    Applied after inference, before pushing to Hydrus.

    Global values are set under [output_filter].
    Per-model overrides are set inside [[inference.models]] as an
    [output_filter] sub-table; missing fields fall back to the global config.
    """

    # --- Threshold settings ---
    prefer_tag_level_thresholds: bool = True
    tag_level_threshold_relative_offset: float = Field(default=0.0, ge=-1.0, lt=1.0)
    default_threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    output_categories: list[str] = Field(default_factory=list)
    include_tags: list[str] = Field(default_factory=list)
    exclude_tags: list[str] = Field(default_factory=list)
    category_thresholds: dict[str, CategoryThresholdConfig] = Field(default_factory=dict)
    tag_thresholds: dict[str, TagThresholdConfig] = Field(default_factory=dict)

    # --- Output selection ---
    category_tag_prefix_mapping: dict[str, str] = Field(default_factory=dict)
    tag_prefix_overrides: dict[str, str] = Field(default_factory=dict)
    max_tags_per_category: dict[str, int] = Field(default_factory=dict)
    max_tags_per_subset: list[TagSubsetConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_max_tags(self) -> OutputFilterConfig:
        bad = [k for k, v in self.max_tags_per_category.items() if v < 1]
        if bad:
            raise ValueError(f"max_tags_per_category entries must be >= 1: {bad}")
        return self


class ModelConfig(BaseModel, frozen=True):
    """Per-model configuration."""

    model_id: str = Field(min_length=1)
    source: str | None = None
    device: str = "auto"
    backend: str | None = None
    precision: str = "auto"
    batch_size: int = Field(default=1, ge=1)

    output_filter: OutputFilterConfig | None = None
    output_tag_services: OutputTagServices | None = None


class InferenceConfig(BaseModel, frozen=True):
    """Model list and per-model configuration."""

    models: list[ModelConfig] = Field(min_length=1)


class HydrusConfig(BaseModel, frozen=True):
    """Hydrus connection + search/preview/output settings."""

    api_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)

    tag_queries: list[TagQueryConfig] = Field(default_factory=list)
    page_queries: list[PageQueryConfig] = Field(default_factory=list)
    preview: PreviewConfig | None = None
    output_tag_services: OutputTagServices = Field(default_factory=OutputTagServices)
    add_tags: AddTagConfig | None = None
    remove_tags: RemoveTagConfig | None = None


class DatabaseConfig(BaseModel, frozen=True):
    path: str = "data/hyvis.db"


class HyvisConfig(BaseModel, frozen=True):
    """Application-level settings."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "WARNING"

    @field_validator("log_level", mode="before")
    @classmethod
    def coerce_log_level(cls, v: str) -> str:
        if isinstance(v, str):
            return v.upper()
        return v


class AppConfig(BaseModel, frozen=True):
    hydrus: HydrusConfig
    inference: InferenceConfig
    output_filter: OutputFilterConfig
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    hyvis: HyvisConfig = Field(default_factory=HyvisConfig)

    # region Helpers

    def resolved_output_filter(self, model_cfg: ModelConfig) -> OutputFilterConfig:
        """
        Return the effective OutputFilterConfig for a model.

        Per-model fields that were explicitly present in TOML override the
        corresponding global field; all others fall back to the global config.
        """
        if model_cfg.output_filter is None:
            return self.output_filter

        # model_fields_set contains ONLY keys explicitly provided in TOML
        overrides = {
            k: v
            for k, v in model_cfg.output_filter.model_dump().items()
            if k in model_cfg.output_filter.model_fields_set
        }

        merged = {**self.output_filter.model_dump(), **overrides}
        return OutputFilterConfig(**merged)

    def resolved_output_tag_services(self, model_cfg: ModelConfig) -> list[str]:
        """
        Return the effective output tag service list for a model.

        If the model specifies its own list it REPLACES the global list entirely.
        Otherwise the global hydrus.output_tag_services list is used.
        """
        if model_cfg.output_tag_services is not None:
            return model_cfg.output_tag_services.keys
        return self.hydrus.output_tag_services.keys

    # region Factory

    @classmethod
    def from_file(cls, path: Path) -> AppConfig:
        try:
            with path.open("rb") as fh:
                raw: dict[str, Any] = tomllib.load(fh)
            return cls.model_validate(raw)
        except ValidationError as e:
            raise SystemExit(f"Configuration error in {path}:\n{e}") from None

    @classmethod
    def from_toml_string(cls, toml_str: str) -> AppConfig:
        try:
            raw: dict[str, Any] = tomllib.loads(toml_str)
            return cls.model_validate(raw)
        except ValidationError as e:
            raise SystemExit(f"Configuration error:\n{e}") from None

    # region Validation

    def hyvis_validate(self, *, has_extra_hashes: bool = False) -> list[str]:
        """
        Return a list of human-readable validation errors (empty → OK).

        Note: Most type/range validation is now handled automatically by Pydantic
        at parse time. This method only checks cross-field business rules that
        cannot be expressed as field-level constraints.
        """
        errors: list[str] = []

        if not self.hydrus.tag_queries and not self.hydrus.page_queries and not has_extra_hashes:
            errors.append(
                "[hydrus] At least one [[hydrus.tag_queries]] or [[hydrus.page_queries]] entry is required, "
                "or an extra hash file must be supplied via --extra-hash-file CLI flag."
            )

        all_models_override = bool(self.inference.models) and all(
            m.output_tag_services is not None for m in self.inference.models
        )
        if not self.hydrus.output_tag_services.keys and not all_models_override:
            errors.append(
                "[hydrus] At least one output service key is required under [hydrus.output_tag_services] "
                "(or every [[inference.models]] entry must define its own output_tag_services)"
            )

        # Cross-field rule: at least one emission path must exist
        of = self.output_filter
        if not of.output_categories and not of.include_tags:
            errors.append(
                "[output_filter] Both 'output_categories' and 'include_tags' are empty. "
                "No tags will ever be emitted under this configuration."
            )

        return errors


# region Connection Override


def override_toml_connection_settings(toml_str: str, api_url: str | None, api_key: str | None) -> str:
    import tomli_w

    data = tomllib.loads(toml_str)

    if "hydrus" not in data:
        data["hydrus"] = {}

    if api_url is not None:
        data["hydrus"]["api_url"] = api_url
    if api_key is not None:
        data["hydrus"]["api_key"] = api_key

    return tomli_w.dumps(data)
