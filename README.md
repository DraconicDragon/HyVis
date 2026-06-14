> HyVis is in development

# HyVis - Hydrus Tagger

HyVis is a vibecoded project that uses a vibecoded inference backend to let you tag files from Hydrus using a small variety of vision transformers.  

HyVis is a vibecoded CLI utility that tags files in your [Hydrus client](https://hydrusnetwork.github.io/hydrus/) using a vibecoded vision transformer inference backend (to run WD Taggers or JTP).

HyVis retrieves file paths from Hydrus file metadata and reads the files directly from your disk. It requires HyVis to run on the same machine (or have direct access to the same storage) as your Hydrus client. (I don't have a setup to test if files being saved on a NAS or similar works or not)  
Support for downloading the files remotely over API and processing them that way may or may not be added in the future - if there is need/demand for it, it may be added sooner rather than later

---

## Installation

### 1. Prerequisites

- **Python 3.11 or higher** (Python 3.12 is recommended and tested; Python 3.10 may work but is not officially supported).
- Local Hydrus client to connect to.

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

*Note: Some models, such as [JTP-3](https://huggingface.co/RedRocket/JTP-3) or [animetimm's dbv4 ConvNeXt v2 Huge](https://huggingface.co/animetimm/convnextv2_huge.dbv4-full), are not available in ONNX format.*

- **CPU Only:**

  ```bash
  pip install onnxruntime
  ```

- **NVIDIA GPU:**

  ```bash
  pip install onnxruntime-gpu
  ```

---

## Updating

You can update HyVis by using the commands below *or* use the update\.sh script (Linux/macOS) or update.bat (Windows) in the repository root.

```bash
cd HyVis
source .venv/bin/activate   # Linux/macOS
# or: .venv\Scripts\activate.bat      # Windows CMD
# or: .venv\Scripts\Activate.ps1      # Windows PowerShell

git pull
pip install .
```

---

## Configuration

HyVis requires a TOML configuration file to define your Hydrus API connection, search rules, models and output filtering.  

To get started you can:

1. Copy one of the templates in the `config_examples/` directory.
2. Edit your copy to insert your Hydrus `api_url` and `api_key`.
3. Configure your search parameters and output target services.

For a comprehensive list of all configuration options, see the [Configuration Guide](CONFIGURATION.md).

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
- `--push-only`
  Read cached results from the local database and push them to Hydrus, skipping the inference step.
- `--api-url` / `--api-key`
  Override the connection parameters specified in your TOML config.
- `--extra-hash-file PATH`
  Process a text file containing one SHA256 hash per line (useful if you have files from [Garbevoir/wd-e621-hydrus-tagger](https://github.com/Garbevoir/wd-e621-hydrus-tagger) that you want to reuse).

---

### Supported and Recommended Models

[TODO]
