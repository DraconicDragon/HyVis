# HyVis - Hydrus Tagger

HyVis is a vibecoded CLI utility that tags files in your [Hydrus client](https://hydrusnetwork.github.io/hydrus/) using a vibecoded vision transformer inference backend (to run WD Taggers or JTP).

HyVis retrieves file paths from Hydrus file metadata and reads the files directly from your disk. It requires HyVis to run on the same machine (or have direct access to the same storage) as your Hydrus client. (I don't have a setup to test if files being saved on a NAS or similar works or not)  
Support for downloading the files remotely over API and processing them that way may or may not be added in the future - if there is need/demand for it, it may be added sooner rather than later

>[!NOTE]
> It's worth noting that HyVis by default has a single, very simple and non-configurable post-processing step that replaces underscores with spaces before sending the tags to Hydrus (Example - Model outputs: `grea_(shingeki_no_bahamut)` and Hydrus gets: `grea (shingeki no bahamut)`).

## Table of Contents

- [Installation](#installation)
- [Updating](#updating)
- [Configuration](#configuration)
  - [Supported and Recommended Models](#supported-and-recommended-models)
- [Usage](#usage)
  - [Useful CLI Flags](#useful-cli-flags)

---

## Installation

### 1. Prerequisites

- **git** | [Installation Guide](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git)
- **Python 3.11** ***or higher*** | Python 3.12 is recommended and tested
- Local [Hydrus client](https://hydrusnetwork.github.io/hydrus/introduction.html) to connect to with Client API enabled in: services > manager services > client api > "run the client api?:" - yes/checked

### 2. Clone the Repository

```bash
git clone https://github.com/DraconicDragon/HyVis.git
cd HyVis
```

### 3. Create and Activate a Virtual Environment

```bash
python -m venv .venv
```

- **Linux/macOS:** `source .venv/bin/activate`
- **Windows (CMD):** `.venv\Scripts\activate.bat`
- **Windows (PowerShell):** `.venv\Scripts\Activate.ps1`

### 4. Install HyVis

This installs the main utility and its core dependencies:

```bash
pip install .
```

### 5. Install an Inference Backend

HyVis supports PyTorch and ONNX backends. You do not need to install both; choose the one that matches the models you plan to run if you want to save space. PyTorch is recommended for broader model compatibility.

> **Hardware Support Note:** HyVis is made and tested on Nvidia hardware. AMD ROCm and Intel GPU configurations are untested since I don't have the respective hardware. If you run HyVis on these platforms, you may need to install the corresponding backend packages manually (e.g., `onnxruntime-rocm`). It is possible that a few-line code change may or may not be needed to support other hardware-specific libraries.  
Feedback on alternative hardware configurations is welcome.

#### Option A: PyTorch Backend (Recommended)

- **CPU Only:**

  ```bash
  pip install "torch>=2.7.1" "safetensors>=0.6.2" "timm>=1.0.22" "transformers>=5.0.0" "einops"
  ```

- **NVIDIA GPU (CUDA):**

  ```bash
  pip install "torch>=2.7.1" "safetensors>=0.6.2" "timm>=1.0.22" "transformers>=5.0.0" "einops" --index-url https://download.pytorch.org/whl/cu128 --extra-index-url https://pypi.org/simple
  ```

> NOTE: If you have a Maxwell (eg: GTX 9xx), Pascal (GTX 10xx/Tesla P100/P40) or Volta (V100) GPU (or older), then you **MUST** switch out `cu128` in the install command above to `cu126` or `cu124`.  
`cu128` dropped support for sm_50, sm_60 and sm_70.
Otherwise your GPU should support cu128 and you may even increase value to `cu130` or `cu132` - if your drivers are up to date (I don't know about any practical differences)

#### Option B: ONNX Backend

*Note: Some models, such as [JTP-3](SUPPORTED_MODELS.md#jtp-3) or [animetimm's dbv4 ConvNeXt v2 Huge](SUPPORTED_MODELS.md#at-convnextv2-huge-dbv4-full), are not available in ONNX format.*

- **CPU Only:**

  ```bash
  pip install onnxruntime
  ```

- **NVIDIA GPU:**

  ```bash
  pip install onnxruntime-gpu
  ```

> On Linux you may need to install CUDA and cuDNN manually through your package manager or whatever the correct method is for your distro.

---

## Updating

You can update HyVis by using the commands below *or* use the update\.sh script (Linux/macOS) or update.bat (Windows) in the repository root.

```bash
cd HyVis
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate.bat      # Windows CMD
# .venv\Scripts\Activate.ps1      # Windows PowerShell

git pull
pip install .
```

---

## Configuration

HyVis uses TOML configuration files to define your Hydrus API connection, search rules, models and output filtering.  

To get started you can copy one of the examples in the `config_examples/` directory and modify the copy to your liking.

For a comprehensive list of all configuration options, see the [Configuration Guide](CONFIGURATION.md). You may want to have this open while checking the example configs and editing/creating your own.

**Available example configs:**

- [`config.example.toml`](config_examples/config.example.toml) - Example config file with pretty much all available options + some comments. Reading the configuration guide over the comments is preferred though

- [`tagging_example.toml`](config_examples/tagging_example.toml) - Generic example config for general tagging of files using a model with basic default settings - likely a good starting point for most users

- [`dan_rating_only.toml`](config_examples/dan_rating_only.toml) - Example config that utilizes output filter options to only send the rating tag with the highest confidence score

- [`tagging_multi_model.toml`](config_examples/tagging_multi_model.toml) - A more advanced example config that uses 2 models (one outputting Danbooru tags, the other E621 tags) to tag files and puts each model's output in separate tag services

### Supported and Recommended Models

Please see [SUPPORTED_MODELS.md](SUPPORTED_MODELS.md)

---

## Usage

Run HyVis by passing the path to your configured TOML file:

```bash
hyvis path/to/config.toml
```

### Useful CLI Flags

- `-y`, `--yes`
  Skip all interactive confirmation prompts.
- `-f`, `--force`
  Ignore the local database cache and re-process all matching files.
- `--infer-only`
  Run model inference and save results to the database cache, but do not send any tags to Hydrus.
- `--no-preview`
  Skip any [configured page previews](CONFIGURATION.md#hydruspreview).
- `--api-url` / `--api-key`
  Override the [connection parameters](CONFIGURATION.md#hydrus) specified in your TOML config.
- `--extra-hash-file PATH`
  Process a text file containing one SHA256 hash per line.

>[!TIP]
> There is also a separate utility - `hyvis-push-pending` - which allows you to push any results to Hydrus that were not pushed during a previous run (for example, if Hydrus was unreachable at the time or if you used `--infer-only`).

<!-- ## FAQ

There would be frequently asked questions here, but there are none, because I can't come up with any and nobody asked yet -->
