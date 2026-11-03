"""
test_filters.py — Tests for the two-stage tag extraction and filtering pipeline.
Verifies category whitelisting, replacements, prefixes, and subset limits.
"""

from __future__ import annotations

from hyvis.config import OutputFilterConfig, TagSubsetConfig
from hyvis.inference import extract_tags

# region Filter Pipeline Tests


def test_inclusions_exclusions_and_categories() -> None:
    """Test Stage 1: Category whitelisting, explicit inclusions, and exclusions."""
    raw_input = {
        "categories": {
            "general": {
                "1girl": 0.90,
                "solo": 0.85,
                "bad_tag": 0.80,
            },
            "character": {
                "hatsune_miku": 0.95,
            },
            "meta": {
                "absurdres": 0.70,
            },
        }
    }

    # Only allow 'general', but explicitly include 'hatsune_miku' and exclude 'bad_tag'
    filter_cfg = OutputFilterConfig(
        allowed_categories=["general"],
        include_tags=["hatsune_miku"],
        exclude_tags=["bad_tag"],
    )

    records = extract_tags(raw_input, output_filter=filter_cfg)
    output_tags = {r.prefixed_tag for r in records}

    # '1girl' and 'solo' allowed via category
    assert "1girl" in output_tags
    assert "solo" in output_tags

    # 'bad_tag' excluded explicitly
    assert "bad_tag" not in output_tags

    # 'hatsune miku' included explicitly despite 'character' category not in allowed_categories
    assert "hatsune miku" in output_tags

    # 'absurdres' dropped because 'meta' is not in allowed_categories
    assert "absurdres" not in output_tags


def test_tag_replacements_and_prefixes() -> None:
    """
    Test Stage 2: Underscore cleaning, tag replacements, and namespace prefixes.
    All configuration options target the model's original raw vocabulary.
    """
    raw_input = {
        "categories": {
            "rating": {
                "rating:g": 0.99,
            },
            "character": {
                "rem_(re:zero)": 0.92,
            },
            "artist": {
                "bison_仓鼠": 0.88,
            },
        }
    }

    filter_cfg = OutputFilterConfig(
        category_tag_prefix_mapping={
            "character": "character:",
            "artist": "creator:",
        },
        # Both rules target the model's raw tag 'rating:g'
        tag_replacements={
            "rating:g": "general",
        },
        tag_prefix_overrides={
            "rating:g": "rating:",
        },
    )

    records = extract_tags(raw_input, output_filter=filter_cfg)
    output_tags = {r.prefixed_tag for r in records}

    # 1. rating:g replaced to 'general', with 'rating:' prefix override from raw tag
    assert "rating:general" in output_tags

    # 2. character category prefix applied
    assert "character:rem (re:zero)" in output_tags

    # 3. artist category prefix applied
    assert "creator:bison 仓鼠" in output_tags


def test_category_limits_and_joint_subsets() -> None:
    """Test Stage 1: Category caps and mutually exclusive subset caps."""
    raw_input = {
        "categories": {
            "rating": {
                "safe": 0.60,
                "questionable": 0.85,
            },
            "general": {
                "tag_c": 0.50,
                "tag_a": 0.90,
                "tag_b": 0.70,
            },
        }
    }

    filter_cfg = OutputFilterConfig(
        # Limit general to top 2 highest scores
        max_tags_per_category={
            "general": 2,
        },
        # Joint subset: keep only 1 tag among safe and questionable
        max_tags_per_subset=[
            TagSubsetConfig(tags=["safe", "questionable"], limit=1),
        ],
    )

    records = extract_tags(raw_input, output_filter=filter_cfg)
    output_tags = {r.prefixed_tag for r in records}

    # Subset limit: questionable (0.85) beats safe (0.60)
    assert "questionable" in output_tags
    assert "safe" not in output_tags

    # Category limit: 'tag a' (0.90) and 'tag b' (0.70) survive, 'tag c' (0.50) dropped
    assert "tag a" in output_tags
    assert "tag b" in output_tags
    assert "tag c" not in output_tags


# endregion
