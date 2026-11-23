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
> Log level can be overridden using the `--log-level` flag.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `log_level` | String | No | Logging verbosity written to the session log file (console highest log level can only be `WARNING`). Options: `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`. <br> Defaults to `"INFO"`. |
| `infer_only` | Boolean | No | Run model inference and queue tags in `hyvis.db`, but do not push any tags to Hydrus. Can be overridden using the `--infer-only` CLI flag. <br> Defaults to `false`. |
| `max_log_files` | Integer | No | Maximum number of past run log files to keep on disk (`1` to `1000`). Older logs are automatically deleted when exceeded. <br> Defaults to `20`. |
| `log_dir` | String | No | Directory path where session run logs are stored. Relative paths resolve against the working directory. <br> Defaults to `"logs"`. |

<details>
<summary>💡 View <code>[hyvis]</code> Example</summary>

```toml
[hyvis]
log_level = "INFO"
infer_only = false
max_log_files = 20
log_dir = "logs"
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
> **For Hydrus v696 or newer:** Missing preview pages are created automatically. If an existing preview page is not empty, HyVis can close it and open a fresh one upon user confirmation.
> **For Hydrus v676 - v695:** Missing preview pages are created automatically, but existing pages must be closed or emptied manually first (e.g.: select all -> right click -> remove).
> **For older Hydrus versions:** Preview pages must be both created and cleared manually.

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

*Array of tables (Can be defined multiple times).* Specifies rules for extra tags to apply to successfully processed files. These tags are added **only after** all configured models have processed the file.

Defining multiple blocks allows routing different tags to different tag services (e.g. local workflow tags to your local service, and model metadata tags to a shared service).

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `tags` | Array of Strings | **Yes** | List of tags to add to files. |
| `tag_service_keys` | Array of Strings | **Yes** | Destination Hydrus tag service keys to write these tags to. |
| `only_if_tags_emitted` | Boolean | No | If `true`, this rule is only applied if at least one tag was emitted by the model(s) during this run. If all predictions fall below threshold (zero tags), this rule is skipped. <br> Defaults to `false`. |

<details>
<summary>💡 View <code>[[hydrus.add_tags]]</code> Example</summary>

```toml
[[hydrus.add_tags]]
tags = ["ai:tagged"]
tag_service_keys = ["my_service_key_here"]
only_if_tags_emitted = true
```

</details>

### `[[hydrus.remove_tags]]`

*Array of tables (Can be defined multiple times).* Specifies cleanup rules for removing temporary search/queue tags from Hydrus. These tags are removed **only after** all configured models have processed the file.

Defining multiple blocks allows targeting specific cleanup tags to specific tag services.

If tag pushing fails for any reason, pending operations stay queued in `hyvis.db` and can be retried anytime by running `hyvis <config> --push-only` (or on the next run).

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `tags` | Array of Strings | **Yes** | List of tags to remove from successfully processed files. |
| `tag_service_keys` | Array of Strings | **Yes** | Target Hydrus tag service keys to remove these tags from. |
| `only_if_tags_emitted` | Boolean | No | If `true`, this rule is only applied if at least one tag was emitted by the model(s) during this run. If all predictions fall below threshold (zero tags), this rule is skipped. <br> Defaults to `false`. |

<details>
<summary>💡 View <code>[[hydrus.remove_tags]]</code> Example</summary>

```toml
[[hydrus.remove_tags]]
tags = ["temp:tagme", "queue:ai processing"]
tag_service_keys = ["my_service_key_here"]
only_if_tags_emitted = true
```

</details>

---

## `[video]`

Optional settings for sampling and processing videos (`MP4`, `WebM`, `MKV`, etc.) and multi-frame animated images (`GIF`, `APNG`, `WebP`, `AVIF`, `JXL`, etc.). Keeps the highest score per tag across sampled frames.

When `enabled` is `false` (default), videos and multi-frame animations are skipped. Single-frame images (including 1-frame GIFs, WebPs, etc.) continue to run normally.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `enabled` | Boolean | No | Enable sampling and frame-level processing for videos and multi-frame animated images. <br> Defaults to `false`. |
| `density_k` | Float | No | Sampling density multiplier (`> 0.0` to `60.0`) used in `N = k * sqrt(duration_s)`. Higher values sample more frames per video. <br> Defaults to `2.0`. |
| `min_frames` | Integer | No | Minimum number of frames sampled per video/animation (`1` to `2048`, must be $\le$ `max_frames`; clamped to total frames if shorter). <br> Defaults to `4`. |
| `max_frames` | Integer | No | Hard upper cap on frames sampled per video (`1` to `7200`) to keep processing time bounded on long videos. <br> Defaults to `128`. |
| `sparse_below_frames` | Integer | No | Files with fewer total frames than this threshold (`1` to `2048`) use frame-count scaling instead of duration scaling. <br> Defaults to `32`. |
| `threshold_adjust_mode` | String | No | Formula used by `threshold_adjust_value` to modify resolved output thresholds for videos and animated images. Options: `"headroom"`, `"additive"`, `"multiplier"`.<br>• `"headroom"`: Positive `k` raises thresholds toward `1.0` via `T + (1 - T) * k`; negative `k` scales toward `0.0` via `T * (1 + k)`.<br>• `"additive"`: Adds a flat offset `T + k` (clamped to `[0.0, 1.0]`).<br>• `"multiplier"`: Scales proportionally via `T * (1 + k)` (clamped to `[0.0, 1.0]`). <br> Defaults to `"headroom"`. |
| `threshold_adjust_value` | Float | No | Adjustment parameter `k` (`-1.0` to `1.0`) applied to resolved thresholds for videos/animations. Positive values raise thresholds (stricter, reduces false positives); `0.0` disables adjustment. <br> Defaults to `0.0`. |
| `frame_cache_mode` | String | No | Where extracted frames live during inference. Options: `"auto"`, `"memory"`, `"disk"`. <br>• `"auto"`: Streams in memory for 1 model; caches frames on disk for 2+ models.<br>• `"memory"`: Never writes extracted frames to disk (re-extracts per model).<br>• `"disk"`: Always saves extracted frames to disk until all models finish the file.<br>*(Note: When disk caching is active on multi-model runs, the first model receives original in-memory decoded frames while subsequent models read disk-cached frames.)* <br> Defaults to `"auto"`. |
| `frame_cache_format` | String | No | Image format used when saving extracted frames to disk for multi-model runs. Options: `"jpeg"`, `"png"`, `"jxl"`, `"jxl_lossless"`, `"webp"`, `"webp_lossless"`.<br>• `"jpeg"`: Fastest encode/decode with minimal overhead (lossy).<br>• `"png"`: Universal lossless.<br>• `"jxl"` / `"jxl_lossless"`: JPEG XL via `pillow-jxl-plugin` (use low effort like `1-3` for speed).<br>• `"webp"` / `"webp_lossless"`: Can be slow to encode, and lossy WebP may look worse for a tagger than JPEG. <br> Defaults to `"jpeg"`. |
| `frame_cache_quality` | Integer | No | Encoding quality (`1` to `100`) for lossy frame cache formats (`"jpeg"`, `"jxl"`, `"webp"`). Ignored for lossless formats (`"png"`, `"jxl_lossless"`, `"webp_lossless"`). <br> Defaults to `95`. |
| `frame_cache_effort` | Integer | No | Encoder speed vs. compression level for formats that support it (ignored for `"jpeg"`):<br>• `"png"`: `0-9` (recommended for temp cache: `1-3`)<br>• `"jxl"` / `"jxl_lossless"`: `1-9` (recommended for temp cache: `1-3` for much faster encoding)<br>• `"webp"` / `"webp_lossless"`: `0-6` (recommended for temp cache: `2-4`) <br> Defaults to `3`. |
| `frame_cache_dir` | String / Null | No | Optional directory for temporary extracted frames when disk caching is active. If omitted or `null`, defaults to `frame_cache` beside `database.path`. <br> Defaults to `null`. |

<details>
<summary>💡 View <code>[video]</code> Example</summary>

```toml
[video]
enabled = true
density_k = 2.0
min_frames = 4
max_frames = 128
sparse_below_frames = 32
threshold_adjust_mode = "headroom"
threshold_adjust_value = 0.0
frame_cache_mode = "auto"
frame_cache_format = "jpeg"
frame_cache_quality = 95
frame_cache_effort = 3
# frame_cache_dir = "data/frame_cache"

</details>

---

## `[output_filter]`

Global settings for filtering and transforming tags before they are pushed to Hydrus. These values can also be overridden on a per-model basis.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `prefer_tag_level_thresholds` | Boolean | No | Uses model-specific per-tag thresholds if supported. Falls back to `default_threshold` if unsupported. *(Note: Mainly supported by animetimm/"dbv4-full" models).* <br> Defaults to `true`. |
| `tag_level_threshold_relative_offset` | Float | No | Relative offset applied to tag-level thresholds. Must be between `-1.0` and `1.0`. For example, `0.1` reduces the threshold requirements by 10%. <br> Defaults to `0.0`. |
| `default_threshold` | Float | No | Fallback threshold (from `0.0` to `1.0`) when tag-level thresholds are disabled or unavailable. <br> Defaults to `0.4`. |
| `replace_underscores_with_spaces` | Boolean | No | Replaces underscores with spaces in emitted tags while preserving kaomojis (e.g. `grea_(shingeki_no_bahamut)` -> `grea (shingeki no bahamut)`, `^_^` unchanged). Set to `false` to keep the model's raw tag formatting. <br> Defaults to `true`. |
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
replace_underscores_with_spaces = true
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

Replaces specific predicted tag names with alternative names before prefixing and output limits are applied. Useful for converting abbreviated ratings (e.g., `g` -> `general`) or mapping tags to your preferred spelling.

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
| `tags` | Array of Strings | **Yes** | Explicit list of raw tags that belong to this subset. |
| `limit` | Integer | No | The maximum number of tags from this subset to output. Must be $\ge 1$. <br> Defaults to `1`. |

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

An optional sub-table that accepts any configuration options available in the global `[output_filter]` block. Keys specified here replace the global settings for this model; omitted keys fall back to the global defaults.

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

Settings for the application's operational SQLite database (`hyvis.db`) and disposable raw prediction cache (`cache.db`).

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `path` | String | No | Path to the SQLite operational database (`hyvis.db`). Relative paths are resolved from the directory where the application is run. <br> Defaults to `"data/hyvis.db"`. |
| `cache_path` | String / Null | No | Optional path to the disposable raw prediction cache database (`cache.db`). If omitted or `null`, `cache.db` is stored automatically alongside `database.path`. <br> Defaults to `null`. |
| `cache_raw_predictions` | Boolean | No | Saves raw, un-culled\* model predictions into `cache.db` using compact binary storage. Allows instant CPU re-filtering when `output_filter` settings change without re-running GPU inference. \*Minimal noise pruning is applied via `min_cache_score`. <br> Defaults to `true`. |
| `min_cache_score` | Float | No | Minimum confidence score (`0.0` to `0.1`) saved to `cache.db`. Setting this very low (e.g. `0.001`) increases `cache.db` size significantly. Cache can be cleared anytime with `--clear-cache`. <br> Defaults to `0.005`. |

<details>
<summary>💡 View <code>[database]</code> Example</summary>

```toml
[database]
path = "data/hyvis.db"
# cache_path = "data/cache.db"
cache_raw_predictions = true
min_cache_score = 0.005
```

</details>
