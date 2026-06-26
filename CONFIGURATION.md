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

<details>
<summary>💡 View <code>[hyvis]</code> Example</summary>

```toml
[hyvis]
log_level = "WARNING"
```

</details>

---

## `[hydrus]`

Configuration for connecting to your Hydrus client and defining tag/page query and output rules.

> [!TIP]
> Both connection settings can be overridden at runtime using the `--api-url` and `--api-key` command-line flags.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `api_url` | String | **Yes** | The base URL of your Hydrus client API |
| `api_key` | String | **Yes** | Your Hydrus API key with appropriate permissions to read files and write tags. |

<details>
<summary>💡 View <code>[hydrus]</code> Example</summary>

```toml
[hydrus]
api_url = "http://127.0.0.1:45869"
api_key = "your_api_key_here"
```

</details>

### File Collection Settings

> [!IMPORTANT]
> To run a processing pass, HyVis needs to find files. You must configure at least one file collection method: **either** a `[[hydrus.tag_queries]]` block, a `[[hydrus.page_queries]]` block, **or** provide a `.txt` file containing a list of hashes via the `--extra-hash-file` CLI flag.

### `[[hydrus.tag_queries]]`

*Array of tables (Can be defined multiple times).* Specifies what search parameters to use to collect candidate files from Hydrus.

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

### `[hydrus.remove_tags]`

Specifies cleanup rules for removing temporary search/queue tags from Hydrus. These tags are removed from files **only after** all configured models have successfully processed them (both inference and pushing succeeded).

If tag removal fails for any reason, the tags remain in Hydrus. On the next run, the files are picked up again, bypass the inference using the local database cache, and retry the cleanup phase. You can also rerun the cleanup and any pending pushes by executing the `hyvis-push-pending` tool.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `tags` | Array of Strings | No | List of tags to remove from successfully processed files. |
| `tag_service_keys` | Array of Strings | No* | Hydrus tag service keys to remove the tags from. <br> **Only required if `tags` is specified*. |

<details>
<summary>💡 View <code>[hydrus.remove_tags]</code> Example</summary>

```toml
[hydrus.remove_tags]
tags = ["temp:tagme", "queue:ai processing"]
tag_service_keys = []
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
| `output_categories` | Array of Strings | No* | Limit output tags to specified categories. Empty list outputs no categories (useful if you only want to allow specific tags defined in `include_tags`). <br> *Usable: `rating`, `general`, `artist`, `contributor`, `copyright`, `character`, `meta`, `species`, `lore`*. <br> Defaults to `[]`. <br> **Required if `include_tags` is not specified or empty*. |
| `include_tags` | Array of Strings | No* | Explicit list of tags to **always include**, bypassing any `output_categories` limitations (exact matches only). <br> Defaults to `[]`. <br> **Required if `output_categories` is not specified or empty*. |
| `exclude_tags` | Array of Strings | No | Explicit list of tags to **always discard**, even if their category is allowed (exact matches only). <br> Defaults to `[]`. |

<details>
<summary>💡 View <code>[output_filter]</code> Example</summary>

```toml
[output_filter]
prefer_tag_level_thresholds = true
tag_level_threshold_relative_offset = 0.0
default_threshold = 0.4
output_categories = ["rating", "general", "artist", "copyright", "character"]

# Ensure some specific meta tags bypass output_categories restriction:
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
# This example would be for JTP-3 or similar taggers where the "rating" tags are in the meta category (in this case because of e621)
[output_filter.category_tag_prefix_mapping]
meta = "meta:"

# Example: Instead of "meta:safe" we get "rating:safe"
[output_filter.tag_prefix_overrides]
"safe" = "rating:"
"questionable" = "rating:"
"explicit" = "rating:"
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
| `source` | String / Null | No | Path to a local folder containing model files (or, if you are experimenting: a HuggingFace repo ID). If omitted, HyVis will attempt to download the model from HuggingFace. <br> If you set source to a local folder which does NOT have any/all model files required present, then HyVis will download the (missing) files into that directory instead of the `HF_HOME` cache directory. <br> Defaults to `null`. |
| `device` | String | No | Hardware device to run inference on (e.g., `"auto"`, `"cuda"`/`"gpu"`, `"cpu"`). <br> Defaults to `"auto"`. |
| `backend` | String / Null | No | Execution engine backend. Options: `"pytorch"`, `"onnx"`, `"auto"`. <br> Defaults to `null` (auto-detect). |
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

# Overrides default_threshold and output_categories just for the second model
[inference.models.output_filter]
default_threshold = 0.5
output_categories = ["rating", "character", "general"]

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

<details>
<summary>💡 View <code>[database]</code> Example</summary>

```toml
[database]
path = "data/hyvis.db"
```

</details>
