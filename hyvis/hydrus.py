from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import hydrus_api
import hydrus_api.utils

from .config import ALLOWED_MIMES, FileQueryConfig

logger = logging.getLogger(__name__)

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
        """Extract a useful error message from hydrus_api.ConnectionError."""
        if exc.__cause__ is not None:
            return str(exc.__cause__)
        return "Connection error (no details available)"

    @classmethod
    def from_hydrus_api(cls, exc: hydrus_api.ConnectionError) -> "HydrusConnectionError":
        return cls(exc)


class HydrusAPIError(HydrusError):
    """Wraps hydrus_api.APIError to provide consistent interface."""

    def __init__(self, original: hydrus_api.APIError) -> None:
        self.original = original
        self.status_code = original.response.status_code
        super().__init__(f"HTTP {self.status_code}: {original.response.text[:200]}")


# region FileInfo  (lightweight metadata record)


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

    def verify_connection(self) -> dict[str, Any]:
        try:
            return self._client.verify_access_key()
        except hydrus_api.ConnectionError as exc:
            raise HydrusConnectionError.from_hydrus_api(exc) from exc
        except hydrus_api.APIError as exc:
            raise HydrusAPIError(exc) from exc

    def get_services(self) -> dict[str, Any]:
        try:
            return self._client.get_services()
        except hydrus_api.ConnectionError as exc:
            raise HydrusConnectionError.from_hydrus_api(exc) from exc
        except hydrus_api.APIError as exc:
            raise HydrusAPIError(exc) from exc

    def get_client_info(self) -> dict[str, Any]:
        try:
            return self._client.get_client_info()
        except hydrus_api.ConnectionError as exc:
            raise HydrusConnectionError.from_hydrus_api(exc) from exc
        except hydrus_api.APIError as exc:
            raise HydrusAPIError(exc) from exc

    def get_version_info(self) -> dict[str, Any]:
        try:
            return self._client.get_api_version()
        except hydrus_api.ConnectionError as exc:
            raise HydrusConnectionError.from_hydrus_api(exc) from exc
        except hydrus_api.APIError as exc:
            raise HydrusAPIError(exc) from exc

    def search_files(self, query: FileQueryConfig) -> set[str]:
        """
        Execute one file query and return the set of matching hashes.

        Uses tag_service_key when the query specifies exactly one service;
        fans out across multiple service keys when more than one is given.
        """
        if len(query.tag_service_keys) > 1:
            hashes: set[str] = set()
            for key in query.tag_service_keys:
                sub_query = FileQueryConfig(tags=query.tags, tag_service_keys=[key])
                hashes |= self.search_files(sub_query)
            return hashes

        tag_service_key = query.tag_service_keys[0] if query.tag_service_keys else None
        try:
            data = self._client.search_files(
                tags=query.tags,
                tag_service_key=tag_service_key,
                return_hashes=True,
            )
            return set(data.get("hashes", []))
        except hydrus_api.ConnectionError as exc:
            raise HydrusConnectionError.from_hydrus_api(exc) from exc
        except hydrus_api.APIError as exc:
            raise HydrusAPIError(exc) from exc

    def collect_candidate_hashes(self, queries: list[FileQueryConfig]) -> set[str]:
        """Union results from all configured queries."""
        result: set[str] = set()
        for query in queries:
            result |= self.search_files(query)
        return result

    def filter_by_mime(
        self,
        hashes: list[str],
        progress_callback: Any | None = None,
    ) -> tuple[list[FileInfo], set[str]]:
        """
        Batch-fetch metadata for all hashes, retain only allowed MIME types.

        Returns FileInfo objects (without local_path; call resolve_paths() later).
        """
        accepted: list[FileInfo] = []
        rejected_mimes: set[str] = set()
        total = len(hashes)

        for start in range(0, total, _METADATA_BATCH):
            batch = hashes[start : start + _METADATA_BATCH]
            try:
                data = self._client.get_file_metadata(hashes=batch)
                metas: list[dict[str, Any]] = data.get("metadata", [])
            except hydrus_api.HydrusAPIException as exc:
                logger.error("Metadata fetch error for batch starting %d: %s", start, exc)
                raise HydrusAPIError from exc

            for meta in metas:
                mime: str = meta.get("mime", "")
                if mime not in ALLOWED_MIMES:
                    if mime:
                        rejected_mimes.add(mime)
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

        return accepted, rejected_mimes

    def resolve_paths(
        self,
        file_infos: list[FileInfo],
        progress_callback: Any | None = None,
    ) -> list[FileInfo]:
        """
        Resolve local file paths for each FileInfo in-place.

        Files whose path cannot be resolved are kept but with local_path=None.
        """
        for idx, info in enumerate(file_infos):
            try:
                data = self._client.get_file_path(hash_=info.file_hash)
                info.local_path = data.get("path")
            except hydrus_api.APIError as exc:
                if exc.response.status_code == 404:
                    logger.debug("No local path for %s (404)", info.file_hash)
                else:
                    logger.warning("Unexpected error resolving path for %s: %s", info.file_hash, exc)
            except hydrus_api.HydrusAPIException as exc:
                logger.error("Path resolution failed for %s: %s", info.file_hash, exc)

            if progress_callback is not None and idx % _PATH_LOG_INTERVAL == 0:
                progress_callback(idx, len(file_infos))

        return file_infos

    def add_tags(
        self,
        hashes: list[str],
        service_key: str,
        tags: list[str],
    ) -> None:
        """Apply tags to hashes on service_key using action ADD."""
        if not tags or not hashes:
            return
        try:
            self._client.add_tags(
                hashes=hashes,
                service_keys_to_actions_to_tags={service_key: {hydrus_api.TagAction.ADD: tags}},
            )
        except hydrus_api.ConnectionError as exc:
            raise HydrusConnectionError.from_hydrus_api(exc) from exc
        except hydrus_api.APIError as exc:
            raise HydrusAPIError(exc) from exc


def format_boot_time(timestamp: float) -> str:
    """Format a Hydrus boot_time value for display."""
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
