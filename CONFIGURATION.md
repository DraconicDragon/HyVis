# Configuration Guide

This document outlines all available settings for your HyVis configuration TOML files.

## Basics

### Structure

- **Single brackets `[section]`**: Defines a single configuration group.
- **Double brackets `[[section]]`**: Defines an array of tables. You can specify multiple of these sections in the config (for example, run/specify multiple models in the same config file).

- **Conditional Requirements**: Some settings will have "**Yes***" / "No\*" in the "Required" column. This means that the setting is only required if certain conditions are met. The specific conditions is explained in the "Description" column for that setting.
  - "**Yes**\*" indicates that the setting is usually required but becomes optional if conditions are met.
  - "No\*" indicates that the setting is not required unless the condition(s) are met.

---

## `[hyvis]`

HyVis application settings.

> [!TIP]
> Log level can be overwritten using the `--log-level` flag .

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `log_level` | String | No | Logging verbosity. Options: `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`. Defaults to `"WARNING"`. |
| `infer_only` | Boolean | No | Run model inference and cache predictions, but do not push any tags to Hydrus. Can be overridden using the `--infer-only` CLI flag. <br> Defaults to `false`. |

<details>
<summary>💡 View <code>[hyvis]</code> Example</summary>

```toml
[hyvis]
log_level = "WARNING"
infer_only = false
```

</details>

---

## `[hydrus]`

Configuration for connecting to your Hydrus client and defining tag/page query and output rules.

> [!TIP]
> Both connection settings can be overridden using the `--api-url` and `--api-key` command-line flags.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `api_url` | String | **Yes** | The base URL of your Hydrus client API |
| `api_key` | String | **Yes** | Your Hydrus API key with appropriate permissions to read files and write tags. |
| `no_wait` | Boolean | No | Do not wait for Hydrus if it is offline or unreachable; fail fast instead. Can be overridden using the `--no-wait` CLI flag. <br> Defaults to `false`. |

<details>
<summary>💡 View <code>[hydrus]</code> Example</summary>

```toml
[hydrus]
api_url = "http://127.0.0.1:45869"
api_key = "your_api_key_here"
no_wait = false
```

</details>

### File Collection Settings

> [!IMPORTANT]
> To run a processing pass, HyVis needs to find files. You must configure at least one file collection method: **either** a `[[hydrus.tag_queries]]` block, a `[[hydrus.page_queries]]` block, **or** provide a `.txt` file containing a list of hashes via the `--extra-hash-file` CLI flag.

### `[[hydrus.tag_queries]]`

*Array of tables (Can be defined multiple times).* Specifies what search parameters to use to collect candidate files from Hydrus.

This is basically WYSIWYG (What You See Is What You Get) as what you can see in the Hydrus client page(s) is what HyVis will attempt to fetch and process - With the exception of unsupported file types of course. It is also useful if tag queries may not support what you are trying to do, but is perfectly possible to do using Hydrus' built-in functionality.

> [!TIP]
> You can use almost all Hydrus system predicates (e.g., `system:limit is 100`). For a technical breakdown and complete list of system predicates, see the [Hydrus Developer API Reference](https://hydrusnetwork.github.io/hydrus/developer_api.html#get_files_search_files) (You may need to scroll down a bit until you see a purple-ish expandable section).

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `tags` | Array of (Strings or Arrays) | **Yes*** | List of tags to query. Nested arrays are evaluated as **OR** queries. <br> **Check [this](#file-collection-settings) for the conditional requirement*. |
| `tag_service_keys` | Array of Strings | No | Hydrus tag service keys to limit the query to. If empty, defaults to `all known tags`. |

<details>
<summary>💡 View <code>[[hydrus.tag_queries]]</code> Example</summary>

```toml
# Simple search: (system:untagged AND class:illustration)
[[hydrus.tag_queries]]
tags = ["system:untagged", "class:illustration"]
tag_service_keys = [] # empty -> all known tags

# A second (option) query block with OR search using nested arrays: (dog OR cat) AND type:photo
[[hydrus.tag_queries]]
tags = [
    ["dog", "cat"], 
    "type:photo",
]
```

</details>

### `[[hydrus.page_queries]]`

> [!NOTE]
> This setting uses a Hydrus API endpoint that is marked as ["Under Construction" in the documentation](https://hydrusnetwork.github.io/hydrus/developer_api.html#manage_pages_get_pages). This setting may stop working with a Hydrus update if the related endpoint changes.

*Array of tables (Can be defined multiple times).* Allows you to target specific open tabs/pages in your Hydrus client to retrieve files from.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `name` | String | **Yes*** | The exact name of the page tab in your Hydrus client. <br> **Check [this](#file-collection-settings) for the conditional requirement*. |
| `index` | integer | No* | A `0`-based index used to disambiguate which tab to use if you have multiple open pages with the exact same name. If duplicate names exist and this is unset, HyVis will error. <br> **Only required if multiple pages exist with the same name*. |

<details>
<summary>💡 View <code>[[hydrus.page_queries]]</code> Example</summary>

```toml
[[hydrus.page_queries]]
name = "memes"

# Example of disambiguating duplicate tab names using 'index'
[[hydrus.page_queries]]
name = "files"
index = 1
```

</details>

### `[hydrus.preview]`

> [!NOTE]
> This setting uses a Hydrus API endpoint that is marked as ["Under Construction" in the documentation](https://hydrusnetwork.github.io/hydrus/developer_api.html#manage_pages_get_pages). This setting may stop working with a Hydrus update if the related endpoint changes drastically.

Optional setting to send the files (and/or rejected files) to a specific open page in your Hydrus client for preview before processing begins.

> If you want to skip previews without commenting in/removing the `[hydrus.preview]` section from the config, you can use the `--no-preview` flag.

> [!IMPORTANT]
> **For Hydrus v676 or newer:** HyVis will automatically create the preview pages for you if they do not already exist. If a page with the configured name already exists, HyVis will use it.
> **For older Hydrus versions:** You must manually create the page(s) in Hydrus. This is an API limitation.
> *In both cases the pages must be empty and cleared manually if they aren't (select all -> right click -> remove)*

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `page_name` | String | No | Target page to preview the files that will be processed. |
| `page_index` | Integer | No* | Disambiguation index if multiple pages share the same `page_name`. <br> **Only required if multiple pages exist with the same name*. |
| `rejected_page_name` | String | No | Target page to preview files that were rejected (e.g., unsupported MIME type). |
| `rejected_page_index` | Integer | No* | Disambiguation index for the rejected page. <br> **Only required if multiple pages exist with the same name*. |

<details>
<summary>💡 View <code>[hydrus.preview]</code> Example</summary>

```toml
[hydrus.preview]
page_name = "hyvis preview"
rejected_page_name = "hyvis rejected"

# Example with disambiguation indices; it's very similar to [hydrus.page_queries]
[hydrus.preview]
page_name = "files"
page_index = 2
rejected_page_name = "rejected files"
rejected_page_index = 0
```

</details>

### `[hydrus.output_tag_services]`

Defines the Hydrus tag services where the inferred tags will be written/pushed to.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `keys` | Array of Strings | **Yes** | A list of Hydrus tag service keys representing the destination service(s) to push tags to. |

<details>
<summary>💡 View <code>[hydrus.output_tag_services]</code> Example</summary>

```toml
[hydrus.output_tag_services]
keys = ["your_service_key_here", "another_service_key_here"]
```

</details>

### `[[hydrus.add_tags]]`

*Array of tables (Can be defined multiple times).* Specifies rules for extra tags to apply to successfully processed files. These tags are added **only after** all configured models have successfully processed them (both inference and pushing succeeded).

Defining multiple blocks allows routing different tags to different tag services (e.g. local workflow tags to your local service, and model metadata tags to a shared service).

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `tags` | Array of Strings | **Yes** | List of tags to add to files. |
| `tag_service_keys` | Array of Strings | **Yes** | Destination Hydrus tag service keys to write these tags to. |

<details>
<summary>💡 View <code>[[hydrus.add_tags]]</code> Example</summary>

```toml
[[hydrus.add_tags]]
tags = ["ai:tagged"]
tag_service_keys = ["my_service_key_here"]
```

</details>

### `[[hydrus.remove_tags]]`

*Array of tables (Can be defined multiple times).* Specifies cleanup rules for removing temporary search/queue tags from Hydrus. These tags are removed **only after** all configured models have successfully processed them (both inference and pushing succeeded).

Defining multiple blocks allows targeting specific cleanup tags to specific tag services.

If tag removal fails for any reason, the tags remain in Hydrus. On the next run, the files are picked up again, bypass inference using the local database cache, and retry the cleanup phase. You can also rerun pending cleanups anytime by executing `hyvis <config> --push-only`.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `tags` | Array of Strings | No | List of tags to remove from successfully processed files. |
| `tag_service_keys` | Array of Strings | No* | Target Hydrus tag service keys to remove these tags from. <br> **Only required if `tags` is specified*. |

<details>
<summary>💡 View <code>[[hydrus.remove_tags]]</code> Example</summary>

```toml
[[hydrus.remove_tags]]
tags = ["temp:tagme", "queue:ai processing"]
tag_service_keys = ["my_service_key_here"]
```

</details>

---

## `[output_filter]`

Global settings for filtering and transforming tags before they are pushed to Hydrus. These values can also be overridden on a per-model basis.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `prefer_tag_level_thresholds` | Boolean | No | Uses model-specific per-tag thresholds if supported. Falls back to `default_threshold` if unsupported. *(Note: Mainly supported by animetimm/"dbv4-full" models).* <br> Defaults to `true`. |
| `tag_level_threshold_relative_offset` | Float | No | Relative offset applied to tag-level thresholds. Must be between `-1.0` and `1.0`. For example, `0.1` reduces the threshold requirements by 10%. <br> Defaults to `0.0`. |
| `default_threshold` | Float | No | Fallback threshold (from `0.0` to `1.0`) when tag-level thresholds are disabled or unavailable. <br> Defaults to `0.4`. |
| `allowed_categories` | Array of Strings / Null | No | Limit output tags to specified categories. If omitted (or `null`), **all categories** are emitted. An explicit empty list `[]` allows no categories (useful if you only want to allow specific tags defined in `include_tags`). <br> *Usable: `rating`, `general`, `artist`, `contributor`, `copyright`, `character`, `meta`, `species`, `lore`*. <br> Defaults to `null`. |
| `include_tags` | Array of Strings | No* | Explicit list of tags to **always include**, bypassing any `allowed_categories` limitations (exact matches only). <br> Defaults to `[]`. <br> **Required if `allowed_categories` is not specified or empty*. |
| `exclude_tags` | Array of Strings | No | Explicit list of tags to **always discard**, even if their category is allowed (exact matches only). <br> Defaults to `[]`. |

<details>
<summary>💡 View <code>[output_filter]</code> Example</summary>

```toml
[output_filter]
prefer_tag_level_thresholds = true
tag_level_threshold_relative_offset = 0.0
default_threshold = 0.4
allowed_categories = ["rating", "general", "artist", "copyright", "character"]

# Ensure some specific meta tags bypass allowed_categories restriction:
include_tags = [
    "some_meta_tag_1",
    "some_meta_tag_2"
]

# Explicitly strip undesirable tags, regardless of category:
exclude_tags = [
    "unwanted_tag",
    "-tag_starting_with_hyphen"
]
```

</details>

### `[output_filter.category_thresholds]`

> Required: No

Allows setting custom static threshold overrides for specific categories when using `default_threshold` fallback logic. Omitted categories will use `default_threshold`.

Values can be declared as simple floats or inline tables if they should override Tag-Level Thresholds (TLT) as well:

- **Float**: Just overrides standard score thresholds (e.g., `character = 0.7`).
- **Inline Table**: Overrides standard score thresholds and overrides TLT calculations if `override_tlt = true`.

<details>
<summary>💡 View <code>[output_filter.category_thresholds]</code> Example</summary>

```toml
[output_filter.category_thresholds]
character = 0.7
artist = { threshold = 0.9, override_tlt = true }
```

</details>

### `[output_filter.tag_thresholds]`

> Required: No

Allows setting static threshold overrides for specific raw tag names. Uses the same Float or Inline Table structure as `category_thresholds`.

<details>
<summary>💡 View <code>[output_filter.tag_thresholds]</code> Example</summary>

```toml
[output_filter.tag_thresholds]
"1girl" = 0.5
"fate_(series)" = { threshold = 0.8, override_tlt = true }
```

</details>

### `[output_filter.category_tag_prefix_mapping]`

> Required: No

Maps model output categories to custom namespace prefixes before they are written to your Hydrus client. Set to an empty string `""` to write tags without a prefix.

- **Keys**: Category name (`rating`, `general`, `artist`, `contributor`, `copyright`, `character`, `meta`, `species`, `lore`)
- **Values**: String (prefix to prepend)

<details>
<summary>💡 View <code>[output_filter.category_tag_prefix_mapping]</code> Example</summary>

```toml
# Example: Model outputs "touhou", which is in the copyright category. With this mapping, the tag is pushed to Hydrus as "series:touhou"
[output_filter.category_tag_prefix_mapping]
rating = "rating:"
general = "" 
artist = "creator:"
copyright = "series:" 
character = "character:"
meta = "meta:"
species = "species:"
lore = "lore:"
contributor = ""
```

</details>

### `[output_filter.tag_prefix_overrides]`

> Required: No

Maps specific raw tag names to custom prefixes, overriding any category-level prefixes set in `tag_prefix_mapping`.

- **Keys**: Raw tag name
- **Values**: String (prefix to prepend)

<details>
<summary>💡 View <code>[output_filter.tag_prefix_overrides]</code> Example</summary>

```toml
# This example would be for JTP-3 / Hydra 3.5 or similar taggers where the "rating" tags are in the meta category (in this case because of e621)
[output_filter.category_tag_prefix_mapping]
meta = "meta:"

# Example: Instead of "meta:safe" we get "rating:safe"
[output_filter.tag_prefix_overrides]
"safe" = "rating:"
"questionable" = "rating:"
"explicit" = "rating:"
```

</details>

### `[output_filter.tag_replacements]`

> Required: No

Replaces specific predicted tag names with alternative names before prefixing and output limits are applied. Useful for converting abbreviated ratings (e.g., `g` → `general`) or mapping tags to your preferred spelling.

- **Keys**: Predicted tag name (supports spaces or underscores)
- **Values**: Replacement tag name

<details>
<summary>💡 View <code>[output_filter.tag_replacements]</code> Example</summary>

```toml
[output_filter.tag_replacements]
"rating:g" = "general" 
# NOTE: If rating:g is part of rating category for example, and category prefix mapping or such is set (eg r: for rating category)
# then the result tag will be r:general instead of r:rating:g
"rating:s" = "sensitive"
"q" = "questionable"
"looking over shoulder" = "looking back"
```

</details>

### `[output_filter.max_tags_per_category]`

> Required: No

Limits the maximum number of tags emitted per category, keeping only the highest-scoring tags. Omitted categories have no limit.

- **Keys**: Category name
- **Values**: Integer ($\ge 1$)

<details>
<summary>💡 View <code>[output_filter.max_tags_per_category]</code> Example</summary>

```toml
[output_filter.max_tags_per_category]
general = 15
character = 5
```

</details>

### `[[output_filter.max_tags_per_subset]]`

Allows setting a joint maximum limit on an arbitrary collection of tags. If multiple tags in this group are above their configured thresholds, only the top highest-scoring tags up to the specified `limit` are retained.

This setting works independently from `[output_filter.max_tags_per_category]`.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `tags` | Array of Strings | No | Explicit list of raw tags that belong to this subset. |
| `limit` | Integer | No* | The maximum number of tags from this subset to output. Must be $\ge 1$. <br> Defaults to `1`. <br> **Required if `tags` is specified*. |

<details>
<summary>💡 View <code>[[output_filter.max_tags_per_subset]]</code> Example</summary>

```toml
# In contrast to other models like those that output rating tags in their own category (for which max_tags_per_category can be used)
# you need to use this to achieve the same effect with JTP-3 / Hydra 3.5 since it's rating tags are all bunched up in the "meta" category.
[[output_filter.max_tags_per_subset]]
tags = ["safe", "questionable", "explicit"]
limit = 1
```

</details>

---

## `[inference]`

Global settings for running tag inference across your models.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `models` | Array of Tables | **Yes** | Definitions of the model sessions to load and run. |

### `[[inference.models]]`

*Array of tables (Can be defined multiple times).* Defines the models to load and run. Each model processes files in its own execution thread/session.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `model_id` | String | **Yes** | The ID or name of the model to use (e.g., `"wd-swinv2-v3"`). You can find all supported and recommended models in the [SUPPORTED_MODELS.md](SUPPORTED_MODELS.md) document. |
| `variant_id` | String / Null | No | Specific model variant to load. <br>• **Omitted / `null` (default)**: Uses the model's default variant. <br>Defaults to `null`. |
| `source` | String / Null | No | Model source location. <br>• **Omitted / `null` (default)**: Automatically downloads from the model's default Hugging Face repository into your HF cache. <br>• **`local:/path/to/folder`**: Uses an existing local directory on disk (skips network requests entirely). <br>• **`username/repo_name`**: Pulls files from a custom or forked Hugging Face repository. <br>Defaults to `null`. |
| `backend` | String / Null | No | Execution engine backend. Options: `"pytorch"`, `"onnx"`, `"auto"`. <br> Defaults to `null` (auto-detect). |
| `device` | String | No | Hardware device to run inference on (e.g., `"auto"`, `"cuda"`/`"gpu"`, `"cpu"`). <br> Defaults to `"auto"`. |
| `precision` | String | No | Numerical precision. Options: `"fp16"`, `"bf16"`, `"fp32"`, `"auto"`. Lower values use less memory. <br> Defaults to `"auto"`. |
| `batch_size` | Integer | No | Number of files processed in a single batch (must be $\ge 1$). Higher values may speed up processing on GPU by a bit but require more memory. This setting can be ignored if running only on CPU. <br> Defaults to `1`. |

Each model block can also contain per-model overrides for tag processing and output targets:

#### Model-specific `[inference.models.output_filter]`

> Required: No

An optional sub-table that accepts any configuration options available in the global [`[output_filter]`](#output_filter) block. Keys specified here replace the global settings for this model; omitted keys fall back to the global defaults.

#### Model-specific `[inference.models.output_tag_services]`

> Required: No

An optional sub-table that overrides the global [`[hydrus.output_tag_services]`](#hydrusoutput_tag_services) configuration. When set, it replaces the global destination list entirely for this model.

<details>
<summary>💡 View <code>[[inference.models]]</code> Overrides Example</summary>

```toml
[[inference.models]]
model_id = "wd-swinv2-v3"
device = "auto"
batch_size = 4

# Run a secondary model with distinct thresholds and custom target services
[[inference.models]]
model_id = "wd-eva02-large-v3"
source = "/path/to/models/eva02"
device = "cuda"
batch_size = 2

# Run a specific variant of a model
[[inference.models]]
model_id = "taggerine"
variant_id = "original"
backend = "pytorch"

# Overrides default_threshold and allowed_categories just for the second model
[inference.models.output_filter]
default_threshold = 0.5
allowed_categories = ["rating", "character", "general"]

# Directs the second model's output to a separate tag service
[inference.models.output_tag_services]
keys = ["special_service_key_123"]
```

</details>

---

## `[database]`

Settings for the application's local state and cache storage.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `path` | String | No | Path to the SQLite database file. Relative paths are resolved from the directory where the application is run. <br> Defaults to `"data/hyvis.db"`. |
| `cache_raw_predictions` | Boolean | No | Saves raw, un-culled* model predictions to the DB. Allows instant re-filtering when thresholds change without re-running GPU inference. \*By default there's minimal culling, see `min_cache_score`. <br> Defaults to `false`. |
| `min_cache_score` | Float | No | Minimum confidence score (`0.0` to `1.0`) saved to the raw cache. **Warning**: setting this below 0.01 will increase DB size by a lot. Cache can be cleared with `--clear-cache` CLI arg. <br> Defaults to `0.01`. |

<details>
<summary>💡 View <code>[database]</code> Example</summary>

```toml
[database]
path = "data/hyvis.db"
cache_raw_predictions = true
min_cache_score = 0.01
```

</details>

---

## `[video]`

Settings for tagging video files and animated GIFs by sampling frames.

When `enabled` is `true`, files with a `video/*` MIME type are accepted alongside images. For each video, a set of frames is sampled (evenly distributed across the content duration, with padding around fade-ins/fade-outs and optional first/last frames), every frame is run through the configured models, and the per-frame results are merged into one result per file: a tag is kept if it appears in any sampled frame, using its highest score across frames. Everything downstream (thresholds, output filter, tag services, preview, push) behaves exactly like it does for images.

Animated GIFs are always accepted as images; while `enabled` is `true` they are sampled like videos instead of only using their first frame. A GIF has no frame rate clock, so each frame's display duration from the GIF metadata defines the animation timeline, and the same uniform sampling, fade, low-interest and near-duplicate filters apply. Because the sampling density rules are shared with videos, a short animation (well under `max_frames_at_seconds`) is sampled proportionally less, typically ending up with its first, middle and last frame.

With `detect_loops` enabled, the first and last frame of every file are compared (two extra decodes at most); when they match, the clip is treated as a loop: its edge frames are skipped (a loop's first frame is just another phase of the same motion) and sampling starts at the center of the content window and works outward, so short loops cost fewer frames while non-looping clips keep their edges. `include_edges_max_seconds` is a cheap duration-based alternative (or addition) that needs no decoding: anything longer than the threshold is assumed to be a loop.

Requires `opencv-python-headless` for video files, which is installed automatically with HyVis (GIF sampling itself only uses Pillow).

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `enabled` | Boolean | No | Accept and tag video files, and sample animated GIFs frame by frame. Set to `false` to keep rejecting videos like before this feature existed; GIFs are then tagged from their first frame only. <br> Defaults to `true`. |
| `max_frames` | Integer | No | Maximum number of sample frames extracted per video or animated GIF. Files shorter than `max_frames_at_seconds` are sampled proportionally less. <br> Defaults to `11`. |
| `max_frames_at_seconds` | Number | No | Duration (seconds) at which the full `max_frames` is used. In `uniform` mode the interval between samples never exceeds `max_frames_at_seconds / max_frames` seconds. <br> Defaults to `60.0`. |
| `include_edges` | Boolean | No | Include the first and last frame of the video. With dedupe enabled they may still be dropped if near-identical to another sampled frame (e.g. a loop's end frame duplicating its start). <br> Defaults to `true`. |
| `include_edges_max_seconds` | Number | No | Loop assumption: clips longer than this many seconds skip their edge frames (they are just another phase of the same loop) and sampling starts at the center of the content window and works outward. Shorter clips keep `include_edges`. Set to `0` to disable the rule and always honour `include_edges`. <br> Defaults to `0.0`. |
| `detect_loops` | Boolean | No | Compare the first and last frame; when they match, the clip is treated as a loop: its edge frames are skipped and sampling starts at the center of the content window. Loop detection uses a looser changed-pixel tolerance than dedupe (a seam is one frame apart), so it catches real loops while leaving non-looping clips alone. The comparison only decodes two frames, so the overhead is negligible. <br> Defaults to `true`. |
| `dedupe` | Boolean | No | Drop sampled frames that are near-duplicates of an already-chosen frame. The main defence against looping animations (the same loop phase sampled at different timestamps) and long static segments wasting the `max_frames` budget. <br> Defaults to `true`. |
| `dedupe_threshold` | Number | No | Normalized mean frame difference (0–1) below which two sampled frames count as duplicates. Lower = stricter (only near-identical frames are dropped). <br> Defaults to `0.03`. |
| `dedupe_change_level` | Integer | No | Second, area-independent duplicate test: pixels differing by more than this (8-bit gray levels) count as "changed". Used with `dedupe_max_changed_fraction` to protect small-but-important changes (a character or water movement in a mostly static frame) that barely move the mean. <br> Defaults to `25`. |
| `dedupe_max_changed_fraction` | Number | No | Max fraction (0–1) of pixels that may differ by more than `dedupe_change_level` for two frames to still count as duplicates. Elements smaller than this fraction of the frame survive dedupe; lower it to protect smaller elements. <br> Defaults to `0.01`. |
| `write_frames` | Boolean | No | Debug aid: save the sampled frames as PNGs under `frames/<file_hash>/` (relative to the directory HyVis is run from) so you can inspect exactly which frames are being inferred. Only written for files that actually run inference this session. <br> Defaults to `false`. |
| `exclude_fades` | Boolean | No | Replace sampled frames that land inside fade-to-black/fade-to-white transitions with the nearest non-fade frame. A fade frame is detected as near-uniform AND near-black/near-white, so dark scenes with detail are preserved. <br> Defaults to `false`. |
| `low_interest` | Table | No | Drop sampled frames with little interesting content (flat AND dark — e.g. black frames and fade-outs the padding missed) so inference is not wasted on them. See `[video.low_interest]`. <br> Defaults to enabled. |

### `[video.fades]`

*Only read when `[video].exclude_fades = true`.* Thresholds for fade-frame detection.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `dark_threshold` | Number | No | Mean luminance (0–1) below which a frame counts as "black". Fade-to-black frames are typically below `0.05`. <br> Defaults to `0.10`. |
| `light_threshold` | Number | No | Mean luminance (0–1) above which a frame counts as "white". <br> Defaults to `0.90`. |
| `uniformity` | Number | No | Luminance std (0–1) below which a frame counts as flat/uniform. A dark scene with detail has a higher std and is therefore NOT treated as a fade. <br> Defaults to `0.08`. |
| `replacement_min_brightness` | Number | No | Minimum mean luminance (0–1) for sampled frames near fades. Frames below this — whether flat fade frames or dim-but-detailed frames just past a fade — are replaced by the nearest sufficiently bright non-fade frame. This is the knob to tune when the frame picked after a fade-in still looks dark (raise it, e.g. `0.4`–`0.5`). Dark scenes are preserved: dim frames are only nudged when a fade-like frame actually exists nearby. <br> Defaults to `0.30`. |
| `search_seconds` | Number | No | How far (seconds) to look in both directions for a replacement frame before giving up and keeping the original. <br> Defaults to `2.0`. |

### `[video.low_interest]`

*Only read when `[video].low_interest.enabled = true` (the default).* A post-sampling filter that **drops** frames with little interesting content — near-uniform AND dark, i.e. black / mostly-black empty frames (fade-ins/outs the fade padding or the fade filter missed). Dropped frames are not replaced; the frame budget simply shrinks. Dark scenes with real detail (high std) are preserved, matching the fade filter's philosophy, and bright frames are never dropped by this filter.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `enabled` | Boolean | No | Set to `false` to keep all sampled frames. <br> Defaults to `true`. |
| `min_luminance` | Number | No | Mean luminance (0–1) below which a frame may count as empty (the darkness gate). <br> Defaults to `0.15`. |
| `min_std` | Number | No | Luminance std (0–1) below which a frame counts as flat/uniform (no detail). A frame is only dropped when it is flat AND dark. Raise it to also drop dim-but-slightly-noisy frames; lower it to keep them. <br> Defaults to `0.05`. |

