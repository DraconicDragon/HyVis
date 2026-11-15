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

When `enabled` is `true`, files with a `video/*` MIME type are accepted alongside images. For each video a dense grid of candidate frames is probed across the content window (the clip minus padding around its fade-in/fade-out), dead frames are dropped, near-duplicates are removed, and the survivors go through the configured models. Per-frame results are merged into one result per file: a tag is kept if it appears in any sampled frame, using its highest score across frames. Everything downstream (thresholds, output filter, tag services, preview, push) behaves exactly like it does for images.

The probe grid holds `probe_multiplier` times as many candidates as frames that will be kept, capped at `max_probes`. Probing more than you keep is the point: when a candidate is a fade, a black frame or a repeat of an earlier moment, the filter drops it and a different moment takes its place, so the file still ends up with a full set of representative frames instead of a thinner one. Probes are decoded in ascending frame order and the decoder walks forward rather than seeking, because a random seek on WebM re-decodes from the start of the file.

Animated GIFs are always accepted as images; while `enabled` is `true` they are sampled like videos instead of only using their first frame. A GIF has no frame rate clock, so each frame's display duration from the GIF metadata defines the animation timeline and probe timestamps are mapped onto frame indices through that timeline.

`[video.loop]` holds the assumptions about looping animations: sub-second clips get a single centered frame, clips longer than `edges_max_seconds` skip their edge probes by duration alone, and `detect` compares the first and last frame so a clip that visibly wraps around loses its redundant edge probes. `[video.dedupe]` controls the near-duplicate test that drops one phase of a loop sampled twice. `[video.fades]` and `[video.low_interest]` drop dead probes: fade frames and flat dark frames. No filter replaces a dropped frame; the probe grid supplies the alternative.

Requires `opencv-python-headless` for video files, which is installed automatically with HyVis (GIF sampling itself only uses Pillow).

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `enabled` | Boolean | No | Accept and tag video files, and sample animated GIFs frame by frame. Set to `false` to keep rejecting videos like before this feature existed; GIFs are then tagged from their first frame only. <br> Defaults to `true`. |
| `max_frames` | Integer | No | Maximum number of frames per file sent to inference. Clips shorter than `max_frames_at_seconds` keep proportionally fewer. <br> Defaults to `11`. |
| `max_frames_at_seconds` | Number | No | Duration (seconds) at which the full `max_frames` is kept. Shorter clips scale down; longer clips stay at `max_frames`. <br> Defaults to `60.0`. |
| `probe_multiplier` | Integer | No | Probe grid size as a multiple of `max_frames`: a multiplier of `3` probes three candidates for every frame that may be kept, so dead and duplicate probes can be replaced by other moments. <br> Defaults to `3`. |
| `max_probes` | Integer | No | Hard cap on probed frames per file, regardless of `probe_multiplier`. Higher values give the filters more alternatives and cost more decode time. <br> Defaults to `48`. |
| `include_edges` | Boolean | No | Add the true first and last frame to the probe grid. They are ordinary probes: they can be dropped as duplicates, and `[video.loop]` can remove them for detected loops. <br> Defaults to `true`. |
| `write_frames` | Boolean | No | Debug aid: save the sampled frames as PNGs under `frames/<file_hash>/` (relative to the directory HyVis is run from) so you can inspect exactly which frames are being inferred. Only written for files that actually run inference this session. <br> Defaults to `false`. |
| `loop` | Table | No | Loop assumptions: single-frame rule, duration-based edge skipping and first/last frame detection. See `[video.loop]`. |
| `dedupe` | Table | No | Near-duplicate probe removal. See `[video.dedupe]`. |
| `fades` | Table | No | Fade-frame detection thresholds. See `[video.fades]`. |
| `low_interest` | Table | No | Flat-and-dark probe removal. See `[video.low_interest]`. |

### `[video.loop]`

Assumptions about looping animations. Loops are the common case for short clips, and a loop's first and last frame are just another phase of the same motion, so edge probes often waste budget.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `detect` | Boolean | No | Compare the first and last probe of each file; when they match, the clip is treated as a loop and both edge probes are skipped, leaving the interior grid to represent every phase. The comparison decodes two frames and uses a looser changed-pixel tolerance than dedupe (a seam is one frame apart). <br> Defaults to `true`. |
| `single_frame_below_seconds` | Number | No | Clips shorter than this many seconds are assumed to be loops too small to say anything beyond one moment, so a single centered frame is inferred and the probe grid is skipped entirely. Set to `0` to disable the rule. <br> Defaults to `1.0`. |
| `edges_max_seconds` | Number | No | Duration-only loop assumption: clips longer than this many seconds drop their edge probes without decoding anything to check. Useful when long clips are predominantly loops. Set to `0` to disable the rule. <br> Defaults to `0.0`. |

### `[video.dedupe]`

Drops probes that are near-duplicates of an already-kept frame, i.e. the same phase of a loop sampled twice, or a long static segment. The test requires both a low mean frame difference and a small fraction of changed pixels, so a small but important change (a character, a face, water movement) in a mostly static frame survives.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `enabled` | Boolean | No | Set to `false` to keep every probe that is not a dead frame. <br> Defaults to `true`. |
| `threshold` | Number | No | Normalized mean frame difference (0–1) below which two probes count as duplicates. Lower = stricter. <br> Defaults to `0.03`. |
| `change_level` | Integer | No | Pixels differing by more than this many 8-bit gray levels count as "changed". <br> Defaults to `25`. |
| `max_changed_fraction` | Number | No | Max fraction (0–1) of changed pixels for two probes to still count as duplicates. Elements smaller than this fraction of the frame survive dedupe; lower it to protect smaller elements. A value of `0` disables the duplicate test. <br> Defaults to `0.01`. |

### `[video.fades]`

Drops probes that land inside a fade-to-black or fade-to-white transition. A fade frame is detected as near-uniform AND near-black/near-white, so dark scenes with detail are preserved. Nothing replaces the dropped probe; the next grid candidate takes its place.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `enabled` | Boolean | No | Set to `true` to drop fade probes. <br> Defaults to `false`. |
| `dark_threshold` | Number | No | Mean luminance (0–1) below which a frame counts as "black". Fade-to-black frames are typically below `0.05`. <br> Defaults to `0.10`. |
| `light_threshold` | Number | No | Mean luminance (0–1) above which a frame counts as "white". <br> Defaults to `0.90`. |
| `uniformity` | Number | No | Luminance std (0–1) below which a frame counts as flat/uniform. A dark scene with detail has a higher std and is therefore NOT treated as a fade. <br> Defaults to `0.08`. |

### `[video.low_interest]`

Drops probes with little interesting content: near-uniform AND dark, i.e. black or mostly-black empty frames (fade-ins/outs the padding missed). Dark scenes with real detail (high std) are preserved, and bright frames are never dropped by this filter.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `enabled` | Boolean | No | Set to `false` to keep all probed frames. <br> Defaults to `true`. |
| `min_luminance` | Number | No | Mean luminance (0–1) below which a frame may count as empty (the darkness gate). <br> Defaults to `0.15`. |
| `min_std` | Number | No | Luminance std (0–1) below which a frame counts as flat/uniform (no detail). A frame is only dropped when it is flat AND dark. <br> Defaults to `0.05`. |
