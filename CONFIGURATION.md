> WIP, doc changes needed for
`[[hydrus.file_queries]] -> tags` find out what ACTUALLY doesn't work

# Configuration Guide

This document outlines all available settings in your `configuration.toml` file.

If you see any parameter's *Description* column prefixed with "**S2C**", it means subject to change in future updates.

---

## Basics

### Structure

- **Single brackets `[section]`**: Defines a single configuration group.
- **Double brackets `[[section]]`**: Defines an array of tables. You can specify multiple of these sections in a row (for example, to query multiple sets of tags or run multiple models).

---

## `[hyvis]`

Global application-level settings.

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

Configuration for connecting to your Hydrus Network client and defining input/output rules.

> Both connection settings can be overridden using `--api-url` and `--api-key` CLI args.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `api_url` | String | **Yes** | The base URL of your Hydrus client API (e.g., `http://127.0.0.1:45869`). |
| `api_key` | String | **Yes** | Your Hydrus API key with appropriate permissions to read files and write tags. |

### `[[hydrus.file_queries]]`

*Array of tables (Can be defined multiple times).* Specifies which tags to search for when collecting target files from Hydrus.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `tags` | Array of Strings | **Yes** | List of tags to query. Only supports basic Hydrus search syntax. |
| `tag_service_keys` | Array of Strings | No | Hydrus tag service keys to limit the query to. If empty, defaults to `all known tags`. |

<details>
<summary>💡 View <code>[[hydrus.file_queries]]</code> Example</summary>

```toml
[[hydrus.file_queries]]
tags = ["system:untagged", "class:illustration"]
tag_service_keys = [] # empty -> all known tags

# You can add a second query block to fetch other files
[[hydrus.file_queries]]
tags = ["source:pixiv", "my namespace:some value"]
tag_service_keys = ["abcdef1234567890"]
```

</details>

### `[hydrus.output_tag_services]`

Defines the Hydrus tag services where the inferred tags will be written.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `keys` | Array of Strings | **Yes** | A list of Hydrus service keys representing the destination service(s) to push tags to. |

<details>
<summary>💡 View <code>[hydrus.output_tag_services]</code> Example</summary>

```toml
[hydrus.output_tag_services]
keys = ["your_service_key_here", "another_service_key_here"]
```

</details>

---

## `[output_filter]`

Global settings for filtering and transforming tags before they are pushed to Hydrus. These values can also be overridden on a per-model basis.

| Parameter | Type | Required | Description |
| :-------- | :--- | :------- | :---------- |
| `prefer_tag_level_thresholds` | Boolean | `true` | When `true`, uses model-specific per-tag thresholds if supported. Falls back to `default_threshold` if unsupported. *(Note: Mainly supported by animetimm/"dbv4-full" models).* |
| `tag_level_threshold_relative_offset` | Float | `0.0` | Relative offset applied to tag-level thresholds. Must be between `-1.0` and `1.0`. For example, `0.1` reduces the threshold requirements by 10%. |
| `default_threshold` | Float | `0.4` | Fallback threshold (from `0.0` to `1.0`) when tag-level thresholds are disabled or unavailable. |
| `output_categories` | Array of Strings | `[]` | Limit output tags to specified categories. Empty list outputs all categories. <br> *Usable: `rating`, `general`, `artist`, `contributor`, `copyright`, `character`, `meta`, `species`, `lore`* |
| `include_tags` | Array of Strings | `[]` | Explicit list of tags to **always include**, bypassing any `output_categories` limitations (exact matches only). |
| `exclude_tags` | Array of Strings | `[]` | Explicit list of tags to **always discard**, even if their category is allowed (exact matches only). No prefix escaping is needed. |

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

Allows setting static threshold overrides for specific raw tag names. Uses the same Float or Inline Table structure as `category_thresholds`.

<details>
<summary>💡 View <code>[output_filter.tag_thresholds]</code> Example</summary>

```toml
[output_filter.tag_thresholds]
"1girl" = 0.5
"fate_(series)" = { threshold = 0.8, override_tlt = true }
```

</details>

### `[output_filter.tag_prefix_mapping]`

Maps model output categories to custom namespace prefixes before they are written to your Hydrus client. Set to an empty string `""` to write tags without a prefix.

- **Keys**: Category name (`rating`, `general`, `artist`, `contributor`, `copyright`, `character`, `meta`, `species`, `lore`)
- **Values**: String (prefix to prepend)

<details>
<summary>💡 View <code>[output_filter.tag_prefix_mapping]</code> Example</summary>

```toml
[output_filter.tag_prefix_mapping]
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

### `[output_filter.max_tags_per_category]`

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
| `model_id` | String | **Yes** | The ID or name of the model to use (e.g., `"wd-swinv2-v3"`). |
| `source` | String / Null | `""` (Null) | Path to a local folder containing model files. If empty/omitted, the application attempts to download the model from HuggingFace. |
| `device` | String | `"auto"` | Hardware to run execution on (e.g., `"auto"`, `"cuda"`, `"cpu"`). |
| `backend` | String / Null | `""` (Null) | Execution engine backend. Options: `"pytorch"`, `"onnx"`, `"auto"`. |
| `precision` | String | `"auto"` | Numerical precision. Options: `"fp16"`, `"bf16"`, `"fp32"`, `"auto"`. |
| `batch_size` | Integer | `4` | Number of files processed in a single batch (must be $\ge 1$). Higher values speed up processing on GPU but require more memory. |

Each model block can also contain per-model overrides for tag processing and output targets:

#### Model-specific `[inference.models.output_filter]`

An optional sub-table that accepts any configuration options available in the global `[output_filter]` block. Keys specified here replace the global settings for this model; omitted keys fall back to the global defaults.

#### Model-specific `[inference.models.output_tag_services]`

An optional sub-table that overrides the global `[hydrus.output_tag_services]` configuration. When set, it replaces the global destination list entirely for this model.

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
| `path` | String | `"hyvis.db"` | Path to the SQLite database file. Relative paths are resolved from the directory where the application is run. |

<details>
<summary>💡 View <code>[database]</code> Example</summary>

```toml
[database]
path = "data/hyvis.db"
```

</details>
```
