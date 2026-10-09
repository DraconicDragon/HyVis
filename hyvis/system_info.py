"""
system_info.py — Lightweight dependency and environment introspection.
Provides structured and text-formatted system details for both CLI and GUI without importing heavy binaries.
"""

from __future__ import annotations

import importlib.metadata
import json
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

from hyvis.cli import get_version


@dataclass(frozen=True)
class DependencyInfo:
    name: str
    version: str
    is_installed: bool = True
    summary: str | None = None


@dataclass(frozen=True)
class DependencyGroup:
    title: str
    items: list[DependencyInfo]


def _get_git_commit(directory: Path) -> str | None:
    """Attempt to extract short git commit hash from a local repository directory."""
    if not (directory / ".git").exists():
        return None
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(directory), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.0,
        ).strip()
        return commit or None
    except Exception:
        return None


def _get_dist_info(dist_name: str) -> tuple[str, bool, str | None]:
    """
    Retrieve installed distribution version and summary via importlib.metadata.
    For local/editable installations, attempts to retrieve the git commit hash.
    """
    try:
        dist = importlib.metadata.distribution(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return "(not installed)", False, None

    version = dist.version

    # Retrieve package summary for tooltip inspection
    summary = None
    try:
        meta = dist.metadata
        summary = meta["Summary"] if "Summary" in meta else None  # noqa: SIM401
    except Exception:
        pass

    # Check for direct_url.json (PEP 610) to detect editable checkouts and git hashes
    try:
        direct_url_text = dist.read_text("direct_url.json")
        if direct_url_text:
            data = json.loads(direct_url_text)

            # Case A: Commit ID recorded directly by pip/VCS
            commit_id = data.get("vcs_info", {}).get("commit_id")
            if commit_id:
                return f"{version} (git-{commit_id[:7]})", True, summary

            # Case B: Local directory checkout (e.g. pip install -e ../vibe)
            url = data.get("url", "")
            if url.startswith("file:"):
                local_dir = Path(url2pathname(urlparse(url).path))
                git_hash = _get_git_commit(local_dir)
                if git_hash:
                    return f"{version} (git-{git_hash})", True, summary
    except Exception:
        pass

    return version, True, summary


def _get_torch_info() -> tuple[str, bool, str | None]:
    """
    Detect PyTorch version and build details without importing the heavy torch binary.
    Reads site-packages/torch/version.py as static AST to extract exact version and hardware toolkits.
    """
    ver, installed, summary = _get_dist_info("torch")
    if not installed:
        return "(not installed)", False, None

    # Inspect site-packages/torch/version.py statically as text (0.5ms, zero imports of torch)
    try:
        import ast
        import importlib.util

        spec = importlib.util.find_spec("torch")
        if spec and spec.origin:
            v_py = Path(spec.origin).parent / "version.py"
            if v_py.exists():
                tree = ast.parse(v_py.read_text(encoding="utf-8"))
                var_map: dict[str, Any] = {}

                for stmt in tree.body:
                    name: str | None = None
                    val_node = None

                    # Handles both `var = val` and annotated `var: Optional[str] = val`
                    if isinstance(stmt, ast.Assign):
                        for t in stmt.targets:
                            if isinstance(t, ast.Name):
                                name = t.id
                                val_node = stmt.value
                    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        name = stmt.target.id
                        val_node = stmt.value

                    if name and val_node is not None:
                        try:
                            var_map[name] = ast.literal_eval(val_node)
                        except Exception:
                            pass

                # 1. Use the full __version__ from version.py if available (e.g. '2.11.0+cu130')
                effective_ver = str(var_map.get("__version__") or ver)

                # If the version string already has local build info (+cu130, +rocm, etc.), return it directly
                if "+" in effective_ver:
                    return effective_ver, True, summary

                # 2. Otherwise, check for any active accelerator variables (e.g. cuda='13.0', rocm='...', xpu='...')
                ignored_keys = {"__version__", "git_version", "debug", "__all__"}
                active_accels = [
                    f"{k} {v}" for k, v in var_map.items() if k not in ignored_keys and isinstance(v, (str, int)) and v
                ]

                if active_accels:
                    return f"{effective_ver} ({', '.join(active_accels)})", True, summary

                return effective_ver, True, summary
    except Exception:
        pass

    return ver, True, summary


def _get_onnxruntime_info() -> tuple[str, bool, str | None]:
    """Detect specific ONNX Runtime distribution (GPU, ROCm, or standard)."""
    for pkg in ("onnxruntime-gpu", "onnxruntime-rocm", "onnxruntime"):
        ver, installed, summary = _get_dist_info(pkg)
        if installed:
            suffix = f" ({pkg})" if pkg != "onnxruntime" else ""
            return f"{ver}{suffix}", True, summary
    return "(not installed)", False, None


def get_system_info() -> list[DependencyGroup]:
    """
    Collect system, framework, and dependency information grouped by layer.
    Pure metadata check: safe to call anytime with negligible startup latency.
    """
    # 1. System & Runtime
    os_name = f"{platform.system()} {platform.release()} ({platform.machine()})"
    python_ver = f"{platform.python_version()} ({'64-bit' if sys.maxsize > 2**32 else '32-bit'})"

    system_items = [
        DependencyInfo("Python", python_ver, summary="Python programming language interpreter"),
        DependencyInfo("OS / Platform", os_name, summary="Operating system platform and kernel architecture"),
    ]

    pyside_ver, pyside_inst, pyside_sum = _get_dist_info("PySide6")
    if pyside_inst:
        system_items.append(DependencyInfo("PySide6", pyside_ver, is_installed=True, summary=pyside_sum))

    # 2. Core Dependencies
    vibe_ver, vibe_inst, vibe_sum = _get_dist_info("vibe")
    vrt_ver, vrt_inst, vrt_sum = _get_dist_info("vibe-result-transforms")

    # Clean leading 'v' from HyVis version string
    hyvis_ver = get_version().removeprefix("v")

    core_items = [
        DependencyInfo(
            "HyVis",
            hyvis_ver,
            summary="Local autotagging utility for Hydrus Network",
        ),
        DependencyInfo(
            "vibe",
            vibe_ver,
            is_installed=vibe_inst,
            summary=vibe_sum or "Vision model inference library",
        ),
        DependencyInfo(
            "vibe-result-transforms",
            vrt_ver,
            is_installed=vrt_inst,
            summary=vrt_sum or "Result post-processing transforms for vibe",
        ),
    ]

    core_pkgs = (
        "hydrus-api",
        "pydantic",
        "requests",
        "tomli-w",
        "huggingface-hub",
        "numpy",
        "Pillow",
        "pillow-heif",
        "pillow-jxl-plugin",
    )
    for pkg_name in core_pkgs:
        ver, inst, summary = _get_dist_info(pkg_name)
        core_items.append(DependencyInfo(pkg_name, ver, is_installed=inst, summary=summary))

    # 3. Inference & Backends
    torch_ver, torch_inst, torch_sum = _get_torch_info()
    ort_ver, ort_inst, ort_sum = _get_onnxruntime_info()

    backend_items = [
        DependencyInfo("PyTorch", torch_ver, is_installed=torch_inst, summary=torch_sum),
        DependencyInfo("ONNX Runtime", ort_ver, is_installed=ort_inst, summary=ort_sum),
    ]

    for pkg_name in ("timm", "transformers", "safetensors", "einops"):
        ver, inst, summary = _get_dist_info(pkg_name)
        backend_items.append(DependencyInfo(pkg_name, ver, is_installed=inst, summary=summary))

    return [
        DependencyGroup("System & Runtime", system_items),
        DependencyGroup("Core Dependencies", core_items),
        DependencyGroup("Inference & Backends", backend_items),
    ]


def format_system_info_text(groups: list[DependencyGroup] | None = None) -> str:
    """Format system information as aligned plain text suitable for clipboard copy or CLI."""
    if groups is None:
        groups = get_system_info()

    lines: list[str] = []
    for group in groups:
        lines.append(f"[{group.title}]")
        max_name_len = max((len(item.name) for item in group.items), default=12)
        for item in group.items:
            lines.append(f"  {item.name:<{max_name_len + 1}} : {item.version}")
        lines.append("")

    return "\n".join(lines).strip()
