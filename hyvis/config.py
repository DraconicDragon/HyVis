"""
config.py Configuration models and TOML loading via Pydantic V2.
"""

from __future__ import annotations

import difflib
import tomllib
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

# region Schema Metadata Definition

FieldSource = Literal["hydrus:tag_services", "vibe:models"]
FieldPicker = Literal["file", "directory"]


def field_meta(
    *,
    source: FieldSource | None = None,
    picker: FieldPicker | None = None,
) -> dict[str, Any]:
    """
    Helper returning a typed dict[str, Any] for Field(json_schema_extra=...).

    Provides type-checked discovery of UI sources and pickers without
    triggering TypedDict invariance errors with type checkers.
    """
    meta: dict[str, Any] = {}
    if source is not None:
        meta["source"] = source
    if picker is not None:
        meta["picker"] = picker
    return meta


# endregion

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


def _extract_model_cls(annotation: Any) -> type[BaseModel] | None:
    """Recursively extract a BaseModel subclass from generic containers or unions."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    origin = get_origin(annotation)
    if origin is not None:
        for arg in get_args(annotation):
            res = _extract_model_cls(arg)
            if res is not None:
                return res
    return None


def _get_valid_fields_for_loc(root_cls: type[BaseModel], loc: tuple[Any, ...]) -> list[str]:
    """Traverse the schema model hierarchy along loc[:-1] to extract valid field names."""
    if not loc:
        return list(root_cls.model_fields.keys())

    current: type[BaseModel] | None = root_cls

    for segment in loc[:-1]:
        if current is None:
            break

        if isinstance(segment, int):
            # Indexing into a list; current remains the item model class
            continue

        segment_str = str(segment)
        field_info = current.model_fields.get(segment_str)
        if field_info is not None:
            current = _extract_model_cls(field_info.annotation)
        else:
            current = None

    if current is not None and issubclass(current, BaseModel):
        return list(current.model_fields.keys())
    return []


def _format_validation_error(e: ValidationError, path: Path | str) -> str:
    import pydantic

    from hyvis.logging_utils import BOLD, CYAN, RED, YELLOW, _c

    lines = [_c(f"Configuration error in {path}:", RED, BOLD), ""]

    version_parts = pydantic.VERSION.split(".")
    major = version_parts[0]
    minor = version_parts[1] if len(version_parts) > 1 else "0"

    version_tag = f"{major}.{minor}"

    for err in e.errors():
        loc_tuple = err["loc"]
        loc_str = ".".join(str(p) for p in loc_tuple)
        msg = err["msg"]
        err_type = err["type"]

        url = f"https://errors.pydantic.dev/{version_tag}/v/{err_type}"
        hyperlink = f"\033]8;;{url}\033\\{err_type}\033]8;;\033\\"

        lines.append(_c(loc_str, BOLD, CYAN))

        # Human-friendly formatting for unknown/misspelled keys with fuzzy matching
        if err_type == "extra_forbidden":
            unknown_key = str(loc_tuple[-1]) if loc_tuple else "key"
            valid_fields = _get_valid_fields_for_loc(AppConfig, loc_tuple)
            suggestions = difflib.get_close_matches(unknown_key, valid_fields, n=2, cutoff=0.45)

            lines.append(f"  Unknown setting or table name: '{unknown_key}'")
            if suggestions:
                sugg_str = ", ".join(f"'{s}'" for s in suggestions)
                lines.append(_c(f"  Did you mean: {sugg_str}?", YELLOW))
        elif err_type == "missing":
            missing_key = str(loc_tuple[-1]) if loc_tuple else "field"
            lines.append(f"  Missing required setting: '{missing_key}'")
        else:
            lines.append(f"  {msg}")

        # lines.append(_c(f"  [{hyperlink}]", DIM))
        lines.append("")

    return "\n".join(lines).rstrip()


# region Lenient Construction Helper


def construct_lenient(model_cls: type[BaseModel], data: Any) -> Any:
    """
    Construct a BaseModel instance leniently for GUI resilience.
    Validates valid fields strictly; falls back to model_construct() for invalid fields
    so work-in-progress or broken configuration files can be inspected and fixed in the UI.
    """
    if not isinstance(data, dict):
        return data

    try:
        return model_cls.model_validate(data)
    except ValidationError:
        pass

    from pydantic_core import PydanticUndefined

    fields_data: dict[str, Any] = {}
    for field_name, field_info in model_cls.model_fields.items():
        if field_name not in data:
            if field_info.default is not PydanticUndefined:
                fields_data[field_name] = field_info.default
            elif field_info.default_factory is not None:
                fields_data[field_name] = field_info.default_factory()
            else:
                fields_data[field_name] = None
            continue

        val = data[field_name]
        annotation = field_info.annotation
        target_cls = _extract_model_cls(annotation)

        if target_cls is not None and issubclass(target_cls, BaseModel):
            if isinstance(val, dict):
                fields_data[field_name] = construct_lenient(target_cls, val)
            elif isinstance(val, list):
                fields_data[field_name] = [
                    construct_lenient(target_cls, item) if isinstance(item, dict) else item for item in val
                ]
            else:
                fields_data[field_name] = val
        else:
            fields_data[field_name] = val

    return model_cls.model_construct(_fields_set=set(data.keys()), **fields_data)


# endregion

# region Config Models


class StrictBaseModel(BaseModel):
    """Base model that strictly forbids extra/unrecognized fields to catch typos."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class TagQueryConfig(StrictBaseModel):
    """One tag search query issued to Hydrus to collect candidate files."""

    tags: list[Any] = Field(
        ...,
        title="Tag Search Query",
        description="List of tags to query files with. Each tag is a string. Nested lists evaluate as OR predicates.",
        examples=[["system:untagged", "type:illustration"], [["dog", "cat"], "-type:photo"]],
    )

    tag_service_keys: list[str] = Field(
        default_factory=list,
        title="Tag Service Keys",
        description="Hydrus tag service keys to limit the query to. If empty, defaults to all known tags.",
        json_schema_extra=field_meta(source="hydrus:tag_services"),
    )


class PageQueryConfig(StrictBaseModel):
    """Target a specific open page in the Hydrus client."""

    name: str = Field(
        ...,
        min_length=1,
        title="Page Tab Name",
        description="The exact name of the page tab in your Hydrus client.",
        examples=["memes", "inbox"],
    )

    index: int | None = Field(
        default=None,
        ge=0,
        title="Disambiguation Index",
        description="Optional 0-based index to disambiguate if multiple pages share the same name.",
        examples=[0, 1],
    )

    @model_validator(mode="after")
    def _validate_index(self) -> PageQueryConfig:
        if self.index is not None and self.index < 0:
            raise ValueError("'index' cannot be negative")
        return self


class PreviewConfig(StrictBaseModel):
    """Target specific open pages for file previewing before inference."""

    page_name: str | None = Field(
        default=None,
        title="Candidate Preview Page",
        description="Optional target page name in Hydrus to preview candidate files before processing begins.",
        examples=["hyvis preview"],
    )
    page_index: int | None = Field(
        default=None,
        ge=0,
        title="Candidate Page Index",
        description="Disambiguation index if multiple pages share the candidate preview page name.",
    )
    rejected_page_name: str | None = Field(
        default=None,
        title="Rejected Preview Page",
        description="Optional target page name in Hydrus to preview rejected files (e.g. unsupported MIME types).",
        examples=["hyvis rejected"],
    )
    rejected_page_index: int | None = Field(
        default=None,
        ge=0,
        title="Rejected Page Index",
        description="Disambiguation index for the rejected preview page.",
    )

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


class OutputTagServices(StrictBaseModel):
    """A Hydrus tag service where inference results will be written."""

    keys: list[str] = Field(
        default_factory=list,
        title="Destination Service Keys",
        description="A list of Hydrus tag service keys representing the destination service(s) to push tags to.",
        json_schema_extra=field_meta(source="hydrus:tag_services"),
    )


class AddTagConfig(StrictBaseModel):
    """Rule specifying tags to add to successfully processed files."""

    tags: list[str] = Field(
        ...,
        min_length=1,
        title="Extra Tags to Add",
        description="List of tags to apply to files after all configured models have processed them successfully.",
        examples=[["ai:tagged"]],
    )
    tag_service_keys: list[str] = Field(
        ...,
        min_length=1,
        title="Target Service Keys",
        description="Hydrus tag service keys to write the added tags to.",
        json_schema_extra=field_meta(source="hydrus:tag_services"),
    )


class RemoveTagConfig(StrictBaseModel):
    """Rule specifying tags to remove from successful files."""

    tags: list[str] = Field(
        ...,
        min_length=1,
        title="Tags to Remove",
        description="List of temporary queue/status tags to remove from files after all models succeed.",
        examples=[["temp:tagme", "queue:ai"]],
    )
    tag_service_keys: list[str] = Field(
        ...,
        min_length=1,
        title="Target Service Keys",
        description="Hydrus tag service keys to remove the tags from.",
        json_schema_extra=field_meta(source="hydrus:tag_services"),
    )


class CategoryThresholdConfig(StrictBaseModel):
    """
    Threshold settings for one output category.

    threshold    Threshold score (0.0–1.0).
    override_tlt If True this threshold also overrides TagLevelThresholds for
                 this category.
    """

    threshold: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        title="Category Threshold",
        description="Minimum confidence threshold score (0.0 to 1.0) for this category.",
        examples=[0.40],
    )
    override_tlt: bool = Field(
        default=False,
        title="Override TLT",
        description="If True, this threshold also overrides model-calibrated Tag-Level Thresholds (TLT) for this category.",
    )


class TagThresholdConfig(StrictBaseModel):
    """
    Threshold settings for one specific tag.

    threshold    Threshold score (0.0–1.0).
    override_tlt If True this threshold also overrides TagLevelThresholds for
                 this tag.
    """

    threshold: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        title="Tag Threshold",
        description="Minimum confidence threshold score (0.0 to 1.0) for this specific tag.",
        examples=[0.50],
    )
    override_tlt: bool = Field(
        default=False,
        title="Override TLT",
        description="If True, this threshold also overrides model-calibrated Tag-Level Thresholds (TLT) for this tag.",
    )


class TagSubsetConfig(StrictBaseModel):
    """
    A collection of tags subject to a collective output limit.
    Used to isolate and limit tags that belong to the same logical category.
    """

    tags: list[str] = Field(
        ...,
        min_length=1,
        title="Subset Tags",
        description="Explicit list of raw tags that belong to this subset group.",
        examples=[["safe", "questionable", "explicit"]],
    )
    limit: int = Field(
        default=1,
        ge=1,
        title="Max Output Limit",
        description="Maximum number of tags from this subset to retain, prioritized by highest confidence score.",
        examples=[1],
    )


class OutputFilterConfig(StrictBaseModel):
    """
    Controls which tags are emitted and how they are transformed.

    Applied after inference, before pushing to Hydrus.

    Global values are set under [output_filter].
    Per-model overrides are set inside [[inference.models]] as an
    [output_filter] sub-table; missing fields fall back to the global config.
    """

    # --- Threshold settings ---
    prefer_tag_level_thresholds: bool = Field(
        default=True,
        title="Prefer Tag-Level Thresholds (TLT)",
        description="Uses model-specific per-tag thresholds if supported. Falls back to default_threshold if unsupported or if data is missing.",
    )
    tag_level_threshold_relative_offset: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        title="TLT Relative Offset",
        description="Relative offset applied to tag-level thresholds (-1.0 to 1.0). For example, 0.1 lowers threshold requirements by 10%.",
        examples=[0.0],
    )
    default_threshold: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        title="Default Threshold (Fallback)",
        description="Fallback confidence threshold (0.0 to 1.0) when tag-level thresholds are disabled or unavailable.",
        examples=[0.40],
    )
    allowed_categories: list[str] | None = Field(
        default=None,
        title="Allowed Categories",
        description="Limit output tags to specified categories. If omitted (null), all categories are allowed. An empty list [] allows no categories (useful if only allowing tags in include_tags).",
        examples=[["rating", "general", "character"]],
    )
    include_tags: list[str] = Field(
        default_factory=list,
        title="Include Tags",
        description="Explicit list of tags to always include, bypassing any allowed_categories restriction (exact matches).",
    )
    exclude_tags: list[str] = Field(
        default_factory=list,
        title="Exclude Tags",
        description="Explicit list of tags to always discard, even if their category is allowed (exact matches).",
    )
    category_thresholds: dict[str, CategoryThresholdConfig] = Field(
        default_factory=dict,
        title="Category Threshold Overrides",
        description="Static threshold overrides for specific output categories.",
    )
    tag_thresholds: dict[str, TagThresholdConfig] = Field(
        default_factory=dict,
        title="Tag Threshold Overrides",
        description="Static threshold overrides for specific raw tag names.",
    )

    @field_validator("category_thresholds", "tag_thresholds", mode="before")
    @classmethod
    def _coerce_threshold_dict(cls, v: Any) -> Any:
        if isinstance(v, dict):
            for key, val in v.items():
                if isinstance(val, (int, float)):
                    v[key] = {"threshold": float(val), "override_tlt": False}
            return v
        return v

    # --- Output selection ---
    category_tag_prefix_mapping: dict[str, str] = Field(
        default_factory=dict,
        title="Category Tag Prefix Mapping",
        description="Maps model output categories to custom namespace prefixes (e.g. character -> character:).",
        examples=[{"character": "character:", "artist": "creator:"}],
    )
    tag_prefix_overrides: dict[str, str] = Field(
        default_factory=dict,
        title="Tag Prefix Overrides",
        description="Maps specific raw tag names to custom prefixes, overriding category prefixes.",
    )
    tag_replacements: dict[str, str] = Field(
        default_factory=dict,
        title="Tag Replacements",
        description="Replaces specific predicted tag names with alternative names before prefixing and output limits are applied.",
        examples=[{"rating:g": "general", "rating:s": "sensitive"}],
    )
    max_tags_per_category: dict[str, int] = Field(
        default_factory=dict,
        title="Max Tags Per Category",
        description="Limits the maximum number of tags emitted per category, keeping only the highest-scoring tags.",
        examples=[{"general": 15, "character": 5}],
    )
    max_tags_per_subset: list[TagSubsetConfig] = Field(
        default_factory=list,
        title="Joint Subset Limits",
        description="Joint output limits on arbitrary tag groups (e.g. only 1 tag among safe/questionable/explicit).",
    )

    @model_validator(mode="after")
    def _validate_max_tags(self) -> OutputFilterConfig:
        bad = [k for k, v in self.max_tags_per_category.items() if v < 1]
        if bad:
            raise ValueError(f"max_tags_per_category entries must be >= 1: {bad}")
        return self


class ModelConfig(StrictBaseModel):
    """Per-model configuration."""

    model_id: str = Field(
        ...,
        min_length=1,
        title="Model ID",
        description="The ID or name of the model to run.",
        json_schema_extra=field_meta(source="vibe:models"),
        examples=["wd-swinv2-v3"],
    )
    source: str | None = Field(
        default=None,
        title="Source Path / Repo",
        description="Path to local folder containing model weights, or a HuggingFace repo ID. Defaults to standard HF download.",
        json_schema_extra=field_meta(picker="directory"),
    )
    device: str = Field(
        default="auto",
        title="Hardware Device",
        description="Hardware device to run inference on (e.g. auto, cuda, cpu, mps, xpu).",
        examples=["auto", "cuda", "cpu"],
    )
    backend: str | None = Field(
        default=None,
        title="Execution Backend",
        description="Execution engine backend",
        examples=["pytorch"],
    )
    precision: str = Field(
        default="auto",
        title="Precision",
        description="Numerical precision: fp16, bf16, fp32, or auto. Lower precision reduces memory usage. Ignored if backend is onnx.",
        examples=["auto", "fp16", "bf16", "fp32"],
    )
    batch_size: int = Field(
        default=1,
        ge=1,
        title="Batch Size",  # todo: add batch_method setting to force cpu to use true batching?
        description="Number of files processed simultaneously in a single forward pass. Higher values increase memory usage. If device is CPU, batch_size is always 1.",
        examples=[1, 4],
    )

    output_filter: OutputFilterConfig | None = Field(
        default=None,
        title="Model Output Filter Overrides",
        description="Optional per-model overrides for output filtering. Missing/Unset keys fall back to global output_filter.",
    )
    output_tag_services: OutputTagServices | None = Field(
        default=None,
        title="Model Destination Tag Services",
        description="Optional per-model override for destination tag services. Completely replaces global services when set.",
        json_schema_extra=field_meta(source="hydrus:tag_services"),
    )


class InferenceConfig(StrictBaseModel):
    """Model list and per-model configuration."""

    models: list[ModelConfig] = Field(
        ...,
        min_length=1,
        title="Configured Models",
        description="List of model sessions to load and execute sequentially.",
    )


class HydrusConfig(StrictBaseModel):
    """Hydrus connection + search/preview/output settings."""

    api_url: str = Field(
        ...,
        min_length=1,
        title="Hydrus API URL",
        description="The base URL of your Hydrus client API.",
        examples=["http://127.0.0.1:45869"],
    )
    api_key: str = Field(
        ...,
        title="Hydrus API Key",
        description="Your Hydrus API key.",
    )
    no_wait: bool = Field(
        default=False,
        title="Fail Fast (No Wait)",
        description="Do not wait for Hydrus if it is offline or unreachable; fail fast instead.",
    )

    tag_queries: list[TagQueryConfig] = Field(
        default_factory=list,
        title="Tag Queries",
        description="Search queries based on tags and service keys to collect candidate files.",
    )
    page_queries: list[PageQueryConfig] = Field(
        default_factory=list,
        title="Page Queries",
        description="Queries targeting open pages/tabs in the Hydrus client.",
    )
    preview: PreviewConfig | None = Field(
        default=None,
        title="Client Previews",
        description="Optional settings to send candidate and/or rejected files to preview pages in Hydrus.",
    )
    output_tag_services: OutputTagServices = Field(
        default_factory=OutputTagServices,
        title="Destination Tag Services (Global)",
        description="Hydrus tag services where inferred tags will be written.",
        json_schema_extra=field_meta(source="hydrus:tag_services"),
    )
    add_tags: list[AddTagConfig] = Field(
        default_factory=list,
        title="Post-Run Additional Tags",
        description="Extra tags to apply to files after all configured models have processed them successfully.",
    )
    remove_tags: list[RemoveTagConfig] = Field(
        default_factory=list,
        title="Post-Run Cleanup Tags",
        description="Cleanup rules for removing temporary search/queue tags from Hydrus after all models succeed.",
    )


class DatabaseConfig(StrictBaseModel):
    path: str = Field(
        default="data/hyvis.db",
        title="Database File Path",
        description="Desired path to the SQLite database for HyVis to use.",
        json_schema_extra=field_meta(picker="file"),
        examples=["data/hyvis.db"],
    )
    cache_raw_predictions: bool = Field(
        default=True,
        title="Cache Raw Predictions",
        description="Save un-culled model predictions in the database to allow instant re-filtering without re-running inference.",
    )
    min_cache_score: float = Field(
        default=0.01,
        ge=0.0,
        le=1.0,
        title="Min. Raw Cache Score",
        description="Minimum confidence score stored in raw cache. The default of 0.01 prunes zero-confidence noise to save disk space. Values below 0.01 may increase database size significantly.",
        examples=[0.01],
    )


class HyvisConfig(StrictBaseModel):
    """Application-level settings."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="WARNING",
        title="Console Log Level",
        description="Console logging verbosity level.",
    )
    infer_only: bool = Field(
        default=False,
        title="Inference Only Mode",
        description="Run model inference only; do not push results to Hydrus.",
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def coerce_log_level(cls, v: str) -> str:
        if isinstance(v, str):
            return v.upper()
        return v


class AppConfig(StrictBaseModel):
    hydrus: HydrusConfig = Field(
        ...,
        title="Hydrus & Queries",
        description="Hydrus client connection parameters, queries, and destination tag services.",
    )
    inference: InferenceConfig = Field(
        ...,
        title="Inference Models",
        description="Definitions of model sessions to load and execute.",
    )
    output_filter: OutputFilterConfig = Field(
        ...,
        title="Output Filter",
        description="Global settings for filtering, thresholding, and transforming tags before pushing to Hydrus.",
    )
    database: DatabaseConfig = Field(
        default_factory=DatabaseConfig,
        title="Database",
        description="Local SQLite state, file tracking, and prediction cache settings.",
    )
    hyvis: HyvisConfig = Field(
        default_factory=HyvisConfig,
        title="Application Settings",
        description="Application-level execution behavior and logging verbosity.",
    )

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
    def from_file(
        cls,
        path: Path,
        *,
        exit_on_error: bool = True,
        lenient: bool = False,
    ) -> AppConfig:
        try:
            with path.open("rb") as fh:
                raw: dict[str, Any] = tomllib.load(fh)
        except Exception as exc:
            if exit_on_error:
                raise SystemExit(f"Failed to read TOML file {path}: {exc}") from None
            raise

        try:
            return cls.model_validate(raw)
        except ValidationError as e:
            if lenient:
                return construct_lenient(cls, raw)
            if exit_on_error:
                raise SystemExit(_format_validation_error(e, path)) from None
            raise

    @classmethod
    def from_toml_string(cls, toml_str: str) -> AppConfig:
        try:
            raw: dict[str, Any] = tomllib.loads(toml_str)
            return cls.model_validate(raw)
        except ValidationError as e:
            raise SystemExit(_format_validation_error(e, "config string")) from None

    # region Validation

    def hyvis_validate(self, *, has_extra_hashes: bool = False) -> list[str]:
        """
        Return a list of human-readable validation errors (empty → OK).

        Note: Most type/range validation is handled automatically by Pydantic
        at parse time. This method checks cross-field business rules that
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
        if of.allowed_categories is not None and not of.allowed_categories and not of.include_tags:
            errors.append(
                "[output_filter] 'allowed_categories' is set to an empty list [] and 'include_tags' is empty. "
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
