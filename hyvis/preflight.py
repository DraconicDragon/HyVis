"""
preflight.py — Unified preflight query collection, MIME filtering, and preview inspection.
Used by both the CLI and the Desktop GUI launch dialog.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from hyvis.config import AppConfig
from hyvis.hydrus import FileInfo, HydrusClient

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int | None, int | None], None]
PreviewStatus = Literal["ready", "dirty", "will_create"]


@dataclass(frozen=True)
class PagePreviewInfo:
    """Status metadata for an open preview page in Hydrus."""

    page_name: str
    page_index: int | None
    status: PreviewStatus
    num_files: int
    page_key: str | None = None


@dataclass
class PreflightResult:
    """Consolidated outcome of queries, MIME filtering, and preview inspections."""

    hydrus_version: str = "unknown"
    api_version: str = "unknown"
    boot_time: str = "unknown"
    service_name_by_key: dict[str, str] = field(default_factory=dict)

    # Query counts breakdown
    tag_query_counts: list[int] = field(default_factory=list)
    page_query_counts: list[int] = field(default_factory=list)

    # File resolution breakdown
    candidate_file_infos: list[FileInfo] = field(default_factory=list)
    candidate_hashes: list[str] = field(default_factory=list)
    rejected_hashes: list[str] = field(default_factory=list)
    rejected_mimes: set[str] = field(default_factory=set)

    # Preview targets
    candidate_preview: PagePreviewInfo | None = None
    rejected_preview: PagePreviewInfo | None = None


def inspect_preview_page(
    hydrus: HydrusClient,
    page_name: str | None,
    page_index: int | None = None,
) -> PagePreviewInfo | None:
    """Inspect whether a configured preview page is empty, contains files, or needs creation."""
    if not page_name:
        return None

    root_pages = hydrus.get_pages()
    keys = hydrus._find_pages_by_name(root_pages.get("pages", {}), page_name)

    if keys:
        target_idx = page_index if page_index is not None and page_index < len(keys) else 0
        target_key = keys[target_idx]
        p_info = hydrus.get_page_info(target_key, simple=True)
        media = p_info.get("media") or p_info.get("page_info", {}).get("media") or {}
        num_files = int(media.get("num_files", 0))
        status = "dirty" if num_files > 0 else "ready"
        return PagePreviewInfo(
            page_name=page_name,
            page_index=page_index,
            status=status,
            num_files=num_files,
            page_key=target_key,
        )

    return PagePreviewInfo(
        page_name=page_name,
        page_index=page_index,
        status="will_create",
        num_files=0,
        page_key=None,
    )


def run_preflight(
    cfg: AppConfig,
    *,
    hydrus: HydrusClient | None = None,
    extra_hashes: list[str] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> PreflightResult:
    """
    Run candidate collection, MIME filtering, and preview inspections.
    Reports progress via progress_callback(stage_name, current, total).
    """

    def _report(stage: str, cur: int | None = None, tot: int | None = None) -> None:
        if progress_callback is not None:
            progress_callback(stage, cur, tot)

    client = hydrus or HydrusClient(cfg.hydrus.api_url, cfg.hydrus.api_key)

    # 1. Connection & Version Verification
    _report("Verifying Hydrus connection...")
    client.verify_connection()

    v_info = client.get_version_info()
    v_str = str(v_info.get("hydrus_version") or v_info.get("client_version") or v_info.get("version") or "unknown")
    api_str = str(v_info.get("api_version") or v_info.get("version") or "unknown")

    boot_time = "unknown"
    try:
        c_info = client.get_client_info()
        boot_val = c_info.get("boot_time")
        if isinstance(boot_val, (int, float)):
            from hyvis.hydrus import format_boot_time

            boot_time = format_boot_time(float(boot_val))
    except Exception:
        pass

    service_name_by_key: dict[str, str] = {}
    try:
        raw_services = client.get_services().get("services", {})
        service_name_by_key = {k: str(v.get("name", k)) for k, v in raw_services.items()}
    except Exception:
        pass

    # 2. Collect Candidate Hashes
    _report("Collecting candidate files...")
    raw_hashes: set[str] = set()
    tag_counts: list[int] = []
    page_counts: list[int] = []

    if cfg.hydrus.tag_queries:
        tq_hashes, tag_counts = client.collect_candidate_hashes(cfg.hydrus.tag_queries)
        raw_hashes |= tq_hashes

    if cfg.hydrus.page_queries:
        pq_hashes, page_counts = client.collect_page_hashes(cfg.hydrus.page_queries)
        raw_hashes |= pq_hashes

    all_hashes = sorted(raw_hashes)

    # 3. Filter by MIME
    file_infos: list[FileInfo] = []
    rejected_mimes: set[str] = set()
    rejected_hashes: list[str] = []

    if all_hashes:
        _report("Filtering metadata by MIME...", 0, len(all_hashes))
        file_infos, r_mimes, r_hashes = client.filter_by_mime(
            all_hashes,
            progress_callback=lambda d, t: _report("Filtering metadata by MIME...", d, t),
        )
        rejected_mimes.update(r_mimes)
        rejected_hashes.extend(r_hashes)

    # Ingest extra hashes if provided (e.g. from CLI --extra-hash-file)
    if extra_hashes:
        _report("Filtering extra hashes by MIME...", 0, len(extra_hashes))
        extra_infos, ex_mimes, ex_hashes = client.filter_by_mime(
            extra_hashes,
            progress_callback=lambda d, t: _report("Filtering extra hashes by MIME...", d, t),
        )
        rejected_mimes.update(ex_mimes)
        rejected_hashes.extend(ex_hashes)

        # Deduplicate preserving order
        existing_hashes = {fi.file_hash for fi in file_infos}
        for fi in extra_infos:
            if fi.file_hash not in existing_hashes:
                file_infos.append(fi)
                existing_hashes.add(fi.file_hash)

    candidate_hashes = [fi.file_hash for fi in file_infos]

    # 4. Preview Page Inspection
    candidate_preview: PagePreviewInfo | None = None
    rejected_preview: PagePreviewInfo | None = None

    if cfg.hydrus.preview:
        _report("Checking preview pages...")
        p = cfg.hydrus.preview
        candidate_preview = inspect_preview_page(client, p.page_name, p.page_index)
        rejected_preview = inspect_preview_page(client, p.rejected_page_name, p.rejected_page_index)

    _report("Preflight complete.")

    return PreflightResult(
        hydrus_version=v_str,
        api_version=api_str,
        boot_time=boot_time,
        service_name_by_key=service_name_by_key,
        tag_query_counts=tag_counts,
        page_query_counts=page_counts,
        candidate_file_infos=file_infos,
        candidate_hashes=candidate_hashes,
        rejected_hashes=rejected_hashes,
        rejected_mimes=rejected_mimes,
        candidate_preview=candidate_preview,
        rejected_preview=rejected_preview,
    )
