"""
bg_imports.py Handles background loading of heavy libraries.
"""

import logging
import threading

_import_thread: threading.Thread | None = None

logger = logging.getLogger(__name__)

# TODO: these logs can cause weirdness with confirmation screen because async/diff thread but might be an issue in future
# right now they dont interfere with anything except visually, but if making TUI in future it could be weird

# todo: torch/onnx are not preloaded at all if backend = auto, in future use vibe to resolve backend and preload based on that

def _load_libraries(backends: set[str | None]) -> None:
    normalized = {b.lower() for b in backends if b is not None}

    try:
        import PIL.Image  # noqa: F401
    except Exception as exc:
        logger.critical("Failed to preload PIL.Image: %s", exc)

    try:
        import vibe  # noqa: F401
    except Exception as exc:
        logger.critical("Failed to preload vibe: %s", exc)

    try:
        import numpy  # noqa: F401
    except Exception as exc:
        logger.critical("Failed to preload numpy: %s", exc)

    # Torch
    if "pytorch" in normalized or "torch" in normalized:
        try:
            import torch  # noqa: F401
        except Exception as exc:
            logger.debug("Failed to preload torch: %s", exc)

    # ONNX Runtime
    if "onnx" in normalized or "onnxruntime" in normalized:
        try:
            import onnxruntime  # noqa: F401
        except Exception as exc:
            logger.debug("Failed to preload onnxruntime: %s", exc)

    logger.debug("Finished preloading libraries in background.")


def start_imports(backends: list[str | None]) -> None:
    """Spawns the background thread to load libraries selectively."""
    global _import_thread
    _import_thread = threading.Thread(
        target=_load_libraries,
        args=(set(backends),),
        daemon=True,
    )
    _import_thread.start()


def wait_for_imports() -> None:
    """Blocks the calling thread until background loading is complete."""
    if _import_thread is not None:
        _import_thread.join()
