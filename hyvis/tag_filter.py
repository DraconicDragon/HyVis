from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from vibe import TagResult
from vibe.transforms import CleanTags, TagLevelThresholds, TransformPipeline

if TYPE_CHECKING:
    import vibe

    from hyvis.config import OutputFilterConfig, VideoConfig
    from hyvis.db import CachedVocab

_CLEANER = CleanTags()

# region Tag extraction


@dataclass(slots=True)
class TagRecord:
    """One tag destined for Hydrus, with its provenance."""

    category: str
    raw_tag: str
    prefixed_tag: str
    score: float


def _norm(tag: str) -> str:
    """Normalize tag for flexible space/underscore matching."""
    return tag.strip().replace("_", " ")


@dataclass(slots=True, frozen=True)
class CompiledOutputFilter:
    """Pre-compiled normalized lookup sets for fast tag extraction."""

    filter_cfg: OutputFilterConfig
    include_set: set[str]
    exclude_set: set[str]
    subset_managed_tags: set[str]
    prefix_overrides: dict[str, str]
    category_prefixes: dict[str, str]
    tag_replacements: dict[str, str]
    compiled_subsets: list[tuple[set[str], int]]


def compile_output_filter(output_filter: OutputFilterConfig) -> CompiledOutputFilter:
    """Compile an OutputFilterConfig once so sets/dicts aren't rebuilt on every image."""
    return CompiledOutputFilter(
        filter_cfg=output_filter,
        include_set={_norm(t) for t in output_filter.include_tags},
        exclude_set={_norm(t) for t in output_filter.exclude_tags},
        subset_managed_tags={_norm(t) for group in output_filter.max_tags_per_subset for t in group.tags},
        prefix_overrides={_norm(t): pfx for t, pfx in output_filter.tag_prefix_overrides.items()},
        category_prefixes=dict(output_filter.category_tag_prefix_mapping),
        tag_replacements={_norm(k): v for k, v in output_filter.tag_replacements.items()},
        compiled_subsets=[({_norm(t) for t in g.tags}, g.limit) for g in output_filter.max_tags_per_subset],
    )


def extract_tags(
    result_input: TagResult | dict[str, Any],
    *,
    output_filter: OutputFilterConfig | CompiledOutputFilter,
) -> list[TagRecord]:
    """
    Convert a TagResult or dictionary representation into TagRecord objects.

    Follows a strict Two-Stage architecture:
      Stage 1 (Decision Engine): Evaluates exclusions, inclusions, categories,
              subsets, and category limits using the model's native vocabulary.
      Stage 2 (Presentation Layer): Cleans underscores to spaces, applies tag
              replacements, deduplicates by max score, and applies namespace prefixes.
    """
    # Accept either raw config or pre-compiled filter
    if isinstance(output_filter, CompiledOutputFilter):
        compiled = output_filter
        filter_cfg = output_filter.filter_cfg
    else:
        compiled = compile_output_filter(output_filter)
        filter_cfg = output_filter

    # Normalize input into {category: {tag: score}} mapping
    tags_by_category: dict[str, dict[str, float]] = {}
    if isinstance(result_input, TagResult):
        for cat_name, entries in result_input.categories.items():
            tags_by_category[cat_name] = {e.tag: float(e.score) for e in entries}
    elif isinstance(result_input, dict):
        raw_cats = result_input.get("categories") or result_input.get("tags") or {}
        if isinstance(raw_cats, dict):
            for cat_name, entries in raw_cats.items():
                if isinstance(entries, list):
                    tags_by_category[cat_name] = {
                        e["tag"]: float(e.get("score", 0.0)) for e in entries if isinstance(e, dict) and "tag" in e
                    }
                elif isinstance(entries, dict):
                    tags_by_category[cat_name] = {t: float(s) for t, s in entries.items()}

    categories = filter_cfg.allowed_categories
    include_set = compiled.include_set
    exclude_set = compiled.exclude_set
    subset_managed_tags = compiled.subset_managed_tags

    prefix_overrides = compiled.prefix_overrides
    category_prefixes = compiled.category_prefixes
    tag_replacements = compiled.tag_replacements

    # region Stage 1
    # Decision engine

    standard_records_by_category: dict[str, list[TagRecord]] = {}
    subset_records: list[TagRecord] = []

    for category, tag_scores in tags_by_category.items():
        standard_records_by_category[category] = []

        for raw_tag, score in tag_scores.items():
            norm_tag = _norm(raw_tag)

            # 1. Exclusions: Drop if explicitly excluded
            if norm_tag in exclude_set:
                continue

            # 2. Inclusions: Always keep if explicitly included
            is_allowed = norm_tag in include_set

            # 3. Category Whitelist: If not explicitly included, check category allowance
            if not is_allowed and (categories is not None and category not in categories):
                continue

            record = TagRecord(
                category=category,
                raw_tag=raw_tag,
                prefixed_tag=raw_tag,  # Formatted in Stage 2
                score=float(score),
            )

            # Isolate subset-managed tags from standard category limits
            if norm_tag in subset_managed_tags:
                subset_records.append(record)
            else:
                standard_records_by_category[category].append(record)

    surviving_records: list[TagRecord] = []

    # apply category limits
    for category, cat_records in standard_records_by_category.items():
        limit = filter_cfg.max_tags_per_category.get(category)
        if limit is not None:
            # sort descending by score, keep top-N
            cat_records.sort(key=lambda r: r.score, reverse=True)
            cat_records = cat_records[:limit]
        surviving_records.extend(cat_records)

    # apply subset limits to isolated tags using pre-compiled tag sets
    for group_tags_set, limit in compiled.compiled_subsets:
        matching_subset_records = [r for r in subset_records if _norm(r.raw_tag) in group_tags_set]

        if len(matching_subset_records) > limit:
            matching_subset_records.sort(key=lambda r: r.score, reverse=True)
            matching_subset_records = matching_subset_records[:limit]

        surviving_records.extend(matching_subset_records)

        # Purge ALL records belonging to this subset group so dropped ones cannot leak into fallback
        subset_records = [r for r in subset_records if _norm(r.raw_tag) not in group_tags_set]

    # Add any remaining subset records that were not covered by a defined group
    surviving_records.extend(subset_records)

    # region Stage 2
    # Presentation layer

    formatted_map: dict[str, TagRecord] = {}

    for r in surviving_records:
        norm_raw = _norm(r.raw_tag)

        # 1. Optionally clean underscores to spaces while preserving kaomojis
        cleaned_tag = _CLEANER.clean_text(r.raw_tag) if filter_cfg.replace_underscores_with_spaces else r.raw_tag
        norm_cleaned = _norm(cleaned_tag)

        # 2. Tag replacement (checks both normalized raw and cleaned versions)
        replaced_tag = tag_replacements.get(norm_cleaned, tag_replacements.get(norm_raw, cleaned_tag))

        # 3. Namespace prefixing (individual overrides take precedence over category prefixes)
        prefix = prefix_overrides.get(
            norm_raw, prefix_overrides.get(norm_cleaned, category_prefixes.get(r.category, ""))
        )
        prefixed_tag = f"{prefix}{replaced_tag}"

        # 4. Merge duplicates by taking the maximum confidence score
        if prefixed_tag not in formatted_map or r.score > formatted_map[prefixed_tag].score:
            formatted_map[prefixed_tag] = TagRecord(
                category=r.category,
                raw_tag=replaced_tag,
                prefixed_tag=prefixed_tag,
                score=r.score,
            )

    return list(formatted_map.values())


# endregion


# region Processor chain


def _resolve_effective_thresholds_from_items(
    label_items: Iterable[tuple[str, str]],
    calibrated_values: Mapping[str, float] | None,
    output_filter: OutputFilterConfig,
    *,
    video_cfg: VideoConfig | None = None,
) -> tuple[dict[str, float], float]:
    """
    Resolve exact, unambiguous per-tag thresholds honoring precedence,
    relative offsets, override_tlt, category/tag-level fallbacks, and optional
    video/animation threshold adjustments.
    """
    use_tlt = output_filter.prefer_tag_level_thresholds and bool(calibrated_values)
    base_calibrated = dict(calibrated_values) if (use_tlt and calibrated_values) else {}

    rel_scale = 1.0 + output_filter.tag_level_threshold_relative_offset
    apply_video_adj = video_cfg is not None and video_cfg.threshold_adjust_value != 0.0

    # Normalized user configuration lookups
    tag_overrides = {_norm(k): v for k, v in output_filter.tag_thresholds.items()}
    cat_overrides = output_filter.category_thresholds

    final_map: dict[str, float] = {}

    for raw_name, cat_name in label_items:
        norm_name = _norm(raw_name)
        tag_cfg = tag_overrides.get(norm_name)
        cat_cfg = cat_overrides.get(cat_name)

        # Case A: Tag has a model-calibrated threshold
        if raw_name in base_calibrated:
            if tag_cfg and tag_cfg.override_tlt:
                thresh = tag_cfg.threshold  # User tag override (unscaled)
            elif cat_cfg and cat_cfg.override_tlt:
                thresh = cat_cfg.threshold  # User category override (unscaled)
            else:
                thresh = base_calibrated[raw_name] * rel_scale  # Calibrated model value (scaled)

        # Case B: Tag has NO calibrated threshold (fallback path)
        else:
            if tag_cfg:
                thresh = tag_cfg.threshold  # Tag-specific fallback
            elif cat_cfg:
                thresh = cat_cfg.threshold  # Category-specific fallback
            else:
                thresh = output_filter.default_threshold  # Global fallback

        if apply_video_adj:
            assert video_cfg is not None
            thresh = video_cfg.adjust_threshold(thresh)

        final_map[raw_name] = thresh
        final_map[norm_name] = thresh

    fallback = (
        video_cfg.adjust_threshold(output_filter.default_threshold)
        if apply_video_adj and video_cfg is not None
        else output_filter.default_threshold
    )
    return final_map, fallback


def resolve_effective_thresholds(
    session: vibe.ModelSession,
    output_filter: OutputFilterConfig,
    *,
    video_cfg: VideoConfig | None = None,
) -> tuple[dict[str, float], float]:
    """Resolve effective per-tag thresholds from an active vibe ModelSession."""
    label_items = (
        (
            info.name,
            str(info.category.value if hasattr(info.category, "value") else info.category),
        )
        for info in (session.tagger.catalog.labels if session.tagger.catalog else ())
    )
    calibrated = (
        session.tagger.thresholds.values
        if (TagLevelThresholds.is_supported(session) and session.tagger.thresholds)
        else None
    )
    return _resolve_effective_thresholds_from_items(
        label_items,
        calibrated,
        output_filter,
        video_cfg=video_cfg,
    )


def build_transform_pipeline(
    session: vibe.ModelSession,
    output_filter: OutputFilterConfig,
    *,
    video_cfg: VideoConfig | None = None,
) -> TransformPipeline:
    """
    Build the TransformPipeline for one model session using vibe.transforms.
    """
    threshold_map, global_fallback = resolve_effective_thresholds(
        session,
        output_filter,
        video_cfg=video_cfg,
    )

    transforms = [
        # Relative offsets were already applied during resolution to calibrated tags only.
        # Passing relative_offset=0.0 prevents double-scaling or scaling explicit user values.
        TagLevelThresholds(
            threshold_map=threshold_map,
            fallback=global_fallback,
        ),
    ]
    if output_filter.replace_underscores_with_spaces:
        transforms.append(CleanTags())
    return TransformPipeline(transforms)


def build_transform_pipeline_from_vocab(
    vocab: CachedVocab,
    output_filter: OutputFilterConfig,
    *,
    video_cfg: VideoConfig | None = None,
) -> TransformPipeline:
    """
    Build the TransformPipeline directly from cache.db CachedVocab without loading model weights.
    """
    label_items = ((vocab.idx_to_tag[idx], vocab.idx_to_category[idx]) for idx in vocab.idx_to_tag)
    calibrated = vocab.calibrated_thresholds or None
    threshold_map, global_fallback = _resolve_effective_thresholds_from_items(
        label_items,
        calibrated,
        output_filter,
        video_cfg=video_cfg,
    )
    transforms = [
        TagLevelThresholds(
            threshold_map=threshold_map,
            fallback=global_fallback,
        ),
    ]
    if output_filter.replace_underscores_with_spaces:
        transforms.append(CleanTags())
    return TransformPipeline(transforms)


# endregion
