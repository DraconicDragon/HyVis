from __future__ import annotations

import functools
import logging
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

import hydrus_api
import hydrus_api.utils

from hyvis.config import ALLOWED_MIMES, AppConfig, PageQueryConfig, TagQueryConfig

logger = logging.getLogger(__name__)


# PATCH: temporary for new_page endpoint to hydrus_api lib until its available in the official release
def _new_page(
    self,
    page_type: int,
    page_name: str | None = None,
    page_of_pages_key: str | None = None,
    focus_page: bool | None = None,
    tags: list[str] | None = None,
    file_service_key: str | None = None,
    tag_service_key: str | None = None,
    hashes: list[str] | None = None,
    service_key: str | None = None,
    paths: list[str] | None = None,
    delete_after_success: bool | None = None,
    file_sort_type: int | None = None,
    file_sort_asc: bool | None = None,
    file_sort_namespaces: list[str] | None = None,
    collect_namespaces: list[str] | None = None,
    system_hash_locked: bool | None = None,
    urls: list[str] | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"page_type": page_type}
    if page_name is not None:
        payload["page_name"] = page_name
    if page_of_pages_key is not None:
        payload["page_of_pages_key"] = page_of_pages_key
    if focus_page is not None:
        payload["focus_page"] = focus_page
    if tags is not None:
        payload["tags"] = tags
    if file_service_key is not None:
        payload["file_service_key"] = file_service_key
    if tag_service_key is not None:
        payload["tag_service_key"] = tag_service_key
    if hashes is not None:
        payload["hashes"] = hashes
    if service_key is not None:
        payload["service_key"] = service_key
    if paths is not None:
        payload["paths"] = paths
    if delete_after_success is not None:
        payload["delete_after_success"] = delete_after_success
    if file_sort_type is not None:
        payload["file_sort_type"] = file_sort_type
    if file_sort_asc is not None:
        payload["file_sort_asc"] = file_sort_asc
    if file_sort_namespaces is not None:
        payload["file_sort_namespaces"] = file_sort_namespaces
    if collect_namespaces is not None:
        payload["collect_namespaces"] = collect_namespaces
    if system_hash_locked is not None:
        payload["system_hash_locked"] = system_hash_locked
    if urls is not None:
        payload["urls"] = urls
    if url is not None:
        payload["url"] = url

    return self._api_request("POST", "/manage_pages/new_page", json=payload).json()


if not hasattr(hydrus_api.Client, "new_page"):
    hydrus_api.Client.new_page = _new_page  # ty:ignore[unresolved-attribute]


# region Exceptions


class HydrusError(Exception):
    """Base for all Hydrus-related errors."""


class HydrusConnectionError(HydrusError):
    """Wraps hydrus_api.ConnectionError to provide consistent string representation."""

    def __init__(self, original: hydrus_api.ConnectionError) -> None:
        self.original = original
        msg = self._extract_message(original)
        super().__init__(msg)

    @staticmethod
    def _extract_message(exc: hydrus_api.ConnectionError) -> str:
        """Extract raw details and append clean user-friendly suggestions."""
        cause = exc.__cause__
        raw_detail = str(cause) if cause is not None else str(exc)

        msg = f"Connection failed: {raw_detail}\n"

        if cause is not None and any(
            term in str(cause) for term in ("Connection refused", "Failed to establish", "timeout")
        ):
            msg += (
                "\nSuggestions:\n"
                "  1. Check if Hydrus is running.\n"
                "  2. Verify that the Client API is enabled in Hydrus settings (services -> Manage services -> client api).\n"
                "  3. Check that the port in api_url matches Hydrus."
            )
        elif cause is not None and any(
            term in str(cause) for term in ("Name or service not known", "getaddrinfo failed")
        ):
            msg += (
                "\nSuggestions:\n"
                "  1. Verify your network connection.\n"
                "  2. Check that api_url in your config is correct."
            )
        else:
            msg += "\nSuggestions:\n  • Check your Hydrus network connection, port settings or other related settings."

        return msg

    @classmethod
    def from_hydrus_api(cls, exc: hydrus_api.ConnectionError) -> "HydrusConnectionError":
        return cls(exc)


class HydrusAPIError(HydrusError):
    """Wraps hydrus_api.APIError to provide consistent interface."""

    def __init__(self, original: hydrus_api.APIError) -> None:
        self.original = original
        self.status_code = original.response.status_code
        super().__init__(f"HTTP {self.status_code}: {original.response.text[:200]}")


# region Error Handling Decorator

F = TypeVar("F", bound=Callable[..., Any])


def _handle_hydrus_errors(func: F) -> F:
    """
    Decorator that translates hydrus_api exceptions into local HydrusError subclasses."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except hydrus_api.ConnectionError as exc:
            raise HydrusConnectionError.from_hydrus_api(exc) from exc
        except hydrus_api.APIError as exc:
            raise HydrusAPIError(exc) from exc

    return wrapper  # type: ignore[return-value]


# region FileInfo (lightweight metadata record)


class FileInfo:
    """Minimal resolved metadata for one file."""

    __slots__ = ("file_hash", "mime", "size", "local_path")

    def __init__(
        self,
        file_hash: str,
        mime: str,
        size: int,
        local_path: str | None = None,
    ) -> None:
        self.file_hash = file_hash
        self.mime = mime
        self.size = size
        self.local_path = local_path  # filled in by resolve_paths()


# region HydrusClient

_METADATA_BATCH = 256
_PATH_LOG_INTERVAL = 500


class HydrusClient:
    def __init__(self, api_url: str, api_key: str) -> None:
        self._client = hydrus_api.Client(access_key=api_key, api_url=api_url)

    # region Public API

    @_handle_hydrus_errors
    def verify_connection(self) -> dict[str, Any]:
        return self._client.verify_access_key()

    @_handle_hydrus_errors
    def get_services(self) -> dict[str, Any]:
        return self._client.get_services()

    @_handle_hydrus_errors
    def get_client_info(self) -> dict[str, Any]:
        return self._client.get_client_info()

    @_handle_hydrus_errors
    def get_version_info(self) -> dict[str, Any]:
        return self._client.get_api_version()

    @_handle_hydrus_errors
    def get_pages(self) -> dict[str, Any]:
        return self._client.get_pages()

    @_handle_hydrus_errors
    def get_page_info(self, page_key: str, simple: bool = False) -> dict[str, Any]:
        return self._client.get_page_info(page_key=page_key, simple=simple)

    @_handle_hydrus_errors
    def search_files(self, query: TagQueryConfig) -> set[str]:
        """
        Execute one tag query and return the set of matching hashes.

        Uses tag_service_key when the query specifies exactly one service;
        fans out across multiple service keys when more than one is given.
        """
        if len(query.tag_service_keys) > 1:
            hashes: set[str] = set()
            for key in query.tag_service_keys:
                sub_query = TagQueryConfig(tags=query.tags, tag_service_keys=[key])
                hashes |= self.search_files(sub_query)
            return hashes

        tag_service_key = query.tag_service_keys[0] if query.tag_service_keys else None
        data = self._client.search_files(
            tags=query.tags,
            tag_service_key=tag_service_key,
            return_hashes=True,
        )
        return set(data.get("hashes", []))

    def _find_pages_by_name(self, node: dict[str, Any], target_name: str) -> list[str]:
        keys = []
        if node.get("name") == target_name and node.get("is_media_page") is True:
            keys.append(node["page_key"])
        for child in node.get("pages", []):
            keys.extend(self._find_pages_by_name(child, target_name))
        return keys

    @_handle_hydrus_errors
    def search_page(self, query: PageQueryConfig) -> set[str]:
        """Execute one page query by name/index and return matching hashes."""
        root_data = self.get_pages()
        keys = self._find_pages_by_name(root_data.get("pages", {}), query.name)

        if not keys:
            raise HydrusError(f"No media page found with name '{query.name}'.")

        if len(keys) > 1:
            if query.index is None:
                raise HydrusError(
                    f"Found {len(keys)} pages named '{query.name}'. "
                    f"Please specify an 'index' (0 to {len(keys) - 1}) in config or rename the target page in Hydrus."
                )
            if query.index < 0 or query.index >= len(keys):
                raise HydrusError(
                    f"Page index {query.index} out of bounds for '{query.name}' (found {len(keys)} instances)."
                )
            target_key = keys[query.index]
        else:
            if query.index is not None and query.index != 0:
                raise HydrusError(
                    f"Found only 1 page named '{query.name}', but index {query.index} was requested. "
                    "Set index to 0 or remove it."
                )
            target_key = keys[0]

        info_data = self.get_page_info(target_key, simple=False)
        media_info = info_data.get("media", {})
        if not media_info and "page_info" in info_data:
            media_info = info_data["page_info"].get("media", {})
        hashes = media_info.get("hashes", [])
        return set(hashes)

    @_handle_hydrus_errors
    def setup_preview_page(self, name: str, hashes: list[str], index: int | None = None, focus: bool = True) -> str:
        """Find an existing empty page or create a new one, and populate it with files."""
        root_data = self.get_pages()
        keys = self._find_pages_by_name(root_data.get("pages", {}), name)

        if keys:
            # Page exists, apply index/disambiguation logic
            if len(keys) > 1:
                if index is None:
                    raise HydrusError(f"Found {len(keys)} pages named '{name}'. Please specify an 'index'.")
                if index < 0 or index >= len(keys):
                    raise HydrusError(f"Page index {index} out of bounds for '{name}'.")
                target_key = keys[index]
            else:
                if index is not None and index != 0:
                    raise HydrusError(f"Found only 1 page named '{name}', but index {index} was requested.")
                target_key = keys[0]

            # Check if empty
            info_data = self.get_page_info(target_key, simple=True)
            media_info = info_data.get("media", {}) or info_data.get("page_info", {}).get("media", {})
            if media_info.get("num_files", 0) > 0:
                raise HydrusError(
                    f"The page '{name}' is not empty. Please clear it first (select all > right click > remove)."
                )

            self.add_files_to_page(target_key, hashes)
            if focus:
                self.focus_page(target_key)
            return target_key

        # Attempt to create page (Hydrus v676+)
        try:
            resp = self._client.new_page(page_type=6, page_name=name, hashes=hashes, focus_page=focus)  # ty:ignore[possibly-missing-attribute]
            return resp["page_key"]
        except hydrus_api.APIError as exc:
            if exc.response.status_code == 404:
                raise HydrusError(
                    f"No media page found with name '{name}' and page creation failed (HTTP Error 404). "
                    "Hydrus v676+ is required for automatic page creation. "
                    "Please create an empty page with this name manually."
                )
            raise

    def collect_candidate_hashes(self, queries: list[TagQueryConfig]) -> tuple[set[str], list[int]]:
        """Union results from all configured tag queries. Returns (union_hashes, list_of_counts)."""
        result: set[str] = set()
        counts: list[int] = []
        for query in queries:
            hashes = self.search_files(query)
            counts.append(len(hashes))
            result |= hashes
        return result, counts

    def collect_page_hashes(self, queries: list[PageQueryConfig]) -> tuple[set[str], list[int]]:
        """Union results from all configured page queries. Returns (union_hashes, list_of_counts)."""
        result: set[str] = set()
        counts: list[int] = []
        for query in queries:
            hashes = self.search_page(query)
            counts.append(len(hashes))
            result |= hashes
        return result, counts

    @_handle_hydrus_errors
    def filter_by_mime(
        self,
        hashes: list[str],
        progress_callback: Any | None = None,
    ) -> tuple[list[FileInfo], set[str], list[str]]:
        """
        Batch-fetch metadata for all hashes, retain only allowed MIME types.

        Returns (accepted_infos, rejected_mimes, rejected_hashes).
        """
        accepted: list[FileInfo] = []
        rejected_mimes: set[str] = set()
        rejected_hashes: list[str] = []
        total = len(hashes)

        for start in range(0, total, _METADATA_BATCH):
            batch = hashes[start : start + _METADATA_BATCH]
            try:
                data = self._client.get_file_metadata(hashes=batch)
                metas: list[dict[str, Any]] = data.get("metadata", [])
            except hydrus_api.HydrusAPIException as exc:
                # Non-connection/API errors still need explicit handling
                logger.error("Metadata fetch error for batch starting %d: %s", start, exc)
                raise HydrusError(f"Hydrus API Exception: {exc}") from exc

            for meta in metas:
                mime: str = meta.get("mime", "")
                if mime not in ALLOWED_MIMES:
                    if mime:
                        rejected_mimes.add(mime)
                    rejected_hashes.append(meta["hash"])
                    logger.debug("Skipping %s: unsupported MIME %s", meta.get("hash", "?"), mime)
                    continue
                accepted.append(
                    FileInfo(
                        file_hash=meta["hash"],
                        mime=mime,
                        size=meta.get("size", 0),
                    )
                )

            if progress_callback is not None:
                progress_callback(min(start + _METADATA_BATCH, total), total)

        return accepted, rejected_mimes, rejected_hashes

    def resolve_paths(
        self,
        file_infos: list[FileInfo],
        progress_callback: Any | None = None,
    ) -> list[FileInfo]:
        """
        Resolve local file paths for each FileInfo in-place concurrently using a thread pool.
        """
        import concurrent.futures
        import threading

        # Use up to 8 concurrent threads for fast local parallel queries
        max_workers = min(8, len(file_infos))
        if max_workers <= 1:
            for idx, info in enumerate(file_infos):
                self._resolve_single_path(info)
                if progress_callback is not None and idx % _PATH_LOG_INTERVAL == 0:
                    progress_callback(idx, len(file_infos))
            return file_infos

        resolved_count = 0
        lock = threading.Lock()

        def worker(info: FileInfo) -> None:
            nonlocal resolved_count
            self._resolve_single_path(info)
            if progress_callback is not None:
                with lock:
                    resolved_count += 1
                    if resolved_count % _PATH_LOG_INTERVAL == 0 or resolved_count == len(file_infos):
                        progress_callback(resolved_count, len(file_infos))

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Consume the map iterator to block until all workers complete
            list(executor.map(worker, file_infos))

        return file_infos

    @_handle_hydrus_errors
    def _resolve_single_path(self, info: FileInfo) -> None:
        """Helper to resolve a single file path synchronously."""
        try:
            data = self._client.get_file_path(hash_=info.file_hash)
            info.local_path = data.get("path")
        except hydrus_api.APIError as exc:
            # 404 is expected for files without local paths; re-raise others to decorator
            if exc.response.status_code == 404:
                logger.debug("No local path for %s (404)", info.file_hash)
            else:
                raise
        except hydrus_api.HydrusAPIException as exc:
            logger.error("Path resolution failed for %s: %s", info.file_hash, exc)

    @_handle_hydrus_errors
    def add_tags(
        self,
        hashes: list[str],
        service_key: str,
        tags: list[str],
    ) -> None:
        """Apply tags to hashes on service_key using action ADD."""
        if not tags or not hashes:
            return
        self._client.add_tags(
            hashes=hashes,
            service_keys_to_actions_to_tags={service_key: {hydrus_api.TagAction.ADD: tags}},
        )

    @_handle_hydrus_errors
    def delete_tags(
        self,
        hashes: list[str],
        service_keys: list[str],
        tags: list[str],
    ) -> None:
        """Remove tags from hashes across the specified service keys."""
        if not tags or not hashes or not service_keys:
            return

        for key in service_keys:
            self._client.add_tags(
                hashes=hashes,
                service_keys_to_actions_to_tags={key: {hydrus_api.TagAction.DELETE: tags}},
            )

    @_handle_hydrus_errors
    def add_files_to_page(self, page_key: str, hashes: list[str]) -> None:
        """Add files to a specific page using the native hydrus-api wrapper."""
        if not hashes:
            return

        # Chunk hashes in batches of 500 to keep request sizes manageable
        for i in range(0, len(hashes), 500):
            batch = hashes[i : i + 500]
            self._client.add_files_to_page(page_key=page_key, hashes=batch)

    @_handle_hydrus_errors
    def focus_page(self, page_key: str) -> None:
        """Focus a specific page using the native hydrus-api wrapper."""
        self._client.focus_page(page_key=page_key)


def validate_service_keys(
    cfg: AppConfig,
    service_name_by_key: dict[str, str],
) -> None:
    """
    Check that all service keys referenced in the config exist in Hydrus.
    Raises HydrusError with a list of missing keys if any are invalid.
    """
    missing: list[str] = []

    # Global output services
    for key in cfg.hydrus.output_tag_services.keys:
        if key not in service_name_by_key:
            missing.append(f"hydrus.output_tag_services: {key}")

    # Per-model output services
    for model_cfg in cfg.inference.models:
        if model_cfg.output_tag_services is not None:
            for key in model_cfg.output_tag_services.keys:
                if key not in service_name_by_key:
                    missing.append(f"model '{model_cfg.model_id}' output_tag_services: {key}")

    # Add tags service keys
    if cfg.hydrus.add_tags is not None:
        for key in cfg.hydrus.add_tags.tag_service_keys:
            if key not in service_name_by_key:
                missing.append(f"hydrus.add_tags: {key}")

    # Remove tags service keys
    if cfg.hydrus.remove_tags is not None:
        for key in cfg.hydrus.remove_tags.tag_service_keys:
            if key not in service_name_by_key:
                missing.append(f"hydrus.remove_tags: {key}")

    # Optional: tag query service keys
    for idx, query in enumerate(cfg.hydrus.tag_queries):
        for key in query.tag_service_keys:
            if key not in service_name_by_key:
                missing.append(f"hydrus.tag_queries[{idx}].tag_service_keys: {key}")

    if missing:
        error_msg = (
            "The following Hydrus service keys are invalid (not found in Hydrus):\n"
            + "\n".join(f"  • {key}" for key in missing)
            + "\n\nCheck your config and ensure these service keys exist in Hydrus."
        )
        raise HydrusError(error_msg)


def format_boot_time(timestamp: float) -> str:
    """Format a Hydrus boot_time value for display."""
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
