import argparse
import logging
import sys
from typing import Any

from hyvis.cli import get_version
from hyvis.logging_utils import BOLD, CYAN, DIM, GREEN, RED, YELLOW, _c, colorize_level

logger = logging.getLogger(__name__)


def _parse_version_number(value: str) -> int | None:
    try:
        return int(str(value).strip())
    except ValueError:
        return None


# region Hydrus connection helpers


def connect_hydrus(cfg: Any, args: argparse.Namespace) -> tuple[Any, dict[str, str], str, str, str]:
    """
    Connect to Hydrus, verify the key, and return:
        (hydrus_client, service_name_by_key, hydrus_version, api_version, boot_time)
    Exits on connection failure.
    """
    from hyvis.hydrus import HydrusClient, HydrusConnectionError, HydrusError

    hydrus = HydrusClient(cfg.hydrus.api_url, cfg.hydrus.api_key)

    print(_c("Connecting to Hydrus...", DIM), end="\r", flush=True)
    try:
        hydrus.verify_connection()
    except HydrusConnectionError as exc:
        original_exc: Exception = exc.original
        logger.debug("Hydrus connection verification failed", exc_info=original_exc)
        print(" " * 40, end="\r")
        print(_c(f"ERROR: {exc}", RED), file=sys.stderr)
        sys.exit(1)
    except HydrusError as exc:
        original_exc: Exception | bool = getattr(exc, "original", True)
        logger.debug("Hydrus API validation failed", exc_info=original_exc)
        print(" " * 40, end="\r")

        if hasattr(exc, "status_code") and exc.status_code in (401, 403):
            print(
                _c(
                    f"ERROR: {exc}\n\nSuggestions:\n  • Your api_key appears to be unauthorized. Check your Hydrus API key settings.",
                    RED,
                ),
                file=sys.stderr,
            )
        else:
            print(_c(f"ERROR: Hydrus API error: {exc}", RED), file=sys.stderr)
        sys.exit(1)

    hydrus_version = api_version = boot_time = "unknown"

    try:
        version_info = hydrus.get_version_info()
        hydrus_version = str(
            version_info.get("hydrus_version")
            or version_info.get("client_version")
            or version_info.get("version")
            or "unknown"
        )
        api_version = str(version_info.get("api_version") or version_info.get("version") or "unknown")
    except Exception as exc:
        logger.warning("Hydrus version lookup failed: %s", exc)

    hydrus_version_number = _parse_version_number(hydrus_version)
    if hydrus_version_number is not None and hydrus_version_number >= 672:
        try:
            client_info = hydrus.get_client_info()
            boot_time_value = client_info.get("boot_time")
            if isinstance(boot_time_value, (int, float)):
                from hyvis.hydrus import format_boot_time

                boot_time = format_boot_time(float(boot_time_value))
        except Exception as exc:
            logger.warning("Hydrus client info lookup failed: %s", exc)
    else:
        boot_time = "(requires Hydrus v672+ / API v92+)"

    try:
        services = hydrus.get_services().get("services", {})
        service_name_by_key = {key: str(info.get("name", key)) for key, info in services.items()}
    except Exception as exc:
        logger.warning("Hydrus get_services failed: %s", exc)
        service_name_by_key = {}

    return hydrus, service_name_by_key, hydrus_version, api_version, boot_time


# region Confirmation screen


def print_confirmation(
    config: Any,
    file_count: int,
    extra_hash_count: int,
    force: bool,
    mode: str,
    service_name_by_key: dict[str, str],
    hydrus_version: str,
    api_version: str,
    boot_time: str,
) -> None:
    inf = config.inference
    hydrus = config.hydrus

    mode_label = {
        "default": "Infer + Push",
        "infer_only": "Infer only  (Hydrus push skipped)",
        "push_only": "Push only   (no inference)",
    }.get(mode, mode)

    print()
    print(_c("  ═══ HyVis | Operation Confirmation ════════════════════════════════", BOLD, CYAN))
    print()
    print(_c("  Mode", BOLD) + f"  {_c(mode_label, YELLOW, BOLD)}")
    print()

    print(
        _c(
            f"  HyVis v{get_version()} | Log level: ",
            BOLD,
        )
        + f"{colorize_level(config.hyvis.log_level, getattr(logging, config.hyvis.log_level.upper()))}"
    )
    print()

    # Hydrus
    print(_c(f"  Hydrus v{hydrus_version} | API v{api_version}", BOLD))
    print(f"    URL        {_c(hydrus.api_url, CYAN)}")
    print(f"    Boot time  {_c(boot_time, GREEN)}")
    print()

    if mode != "push_only":
        # Queries
        print(_c("  File Queries", BOLD))
        for q in hydrus.file_queries:
            if q.tag_service_keys:
                names = []
                for key in q.tag_service_keys:
                    name = service_name_by_key.get(key, "(unknown service)")
                    names.append(f"{_c(name, BOLD, CYAN)} {_c(key, DIM)}")
                svc = ", ".join(names)
            else:
                svc = _c("(all known tags)", DIM)
            print(f"    service  {svc}")

            def _format_tag(t: Any) -> str:
                if isinstance(t, list):
                    return "(" + _c(" OR ", BOLD) + "".join(_format_tag(sub) for sub in t) + ")"
                return str(t)

            for tag in q.tags:
                if isinstance(tag, list):
                    # Top-level nested list; no need for outer parentheses
                    print(f"      tag    {f'{_c(" OR ", BOLD)}'.join(_format_tag(sub) for sub in tag)}")
                else:
                    print(f"      tag    {tag}")
        print()

        if hydrus.remove_tags and mode in ("default", "push_only"):
            print(_c("  Cleanup Tags (removed after successful run)", BOLD))
            r = hydrus.remove_tags
            if r.tag_service_keys:
                names = []
                for key in r.tag_service_keys:
                    name = service_name_by_key.get(key, "(unknown service)")
                    names.append(f"{_c(name, BOLD, CYAN)} {_c(key, DIM)}")
                svc = ", ".join(names)
            else:
                svc = _c("(all tag services)", DIM)
            print(f"    service  {svc}")
            for tag in r.tags:
                print(f"      tag    {_c(tag, RED)}")
            print()

        # File count
        count_col = YELLOW if file_count == 0 else GREEN
        forced_note = "  (--force: cache bypassed)" if force else ""
        extra_note = (
            f"  (+{extra_hash_count} extra hash{'es' if extra_hash_count != 1 else ''})" if extra_hash_count else ""
        )
        print(
            f"  {_c('Files to process', BOLD)}  {_c(str(file_count), count_col, BOLD)}"
            f"{_c(extra_note, DIM)}{_c(forced_note, DIM)}"
        )
        print()

    if mode != "infer_only":
        if mode == "push_only":
            # Output services
            print(_c("  Output Tag Services (global)", BOLD))
            for svc in hydrus.output_tag_services:
                name = service_name_by_key.get(svc.key, "(unknown service)")
                print(f"    {_c(name, BOLD, CYAN)} {_c(svc.key, DIM)}")
            print()

    if mode != "push_only":
        # Models
        print(_c("  Models", BOLD))
        for i, m in enumerate(inf.models, 1):
            print(f"    {i}. {_c(m.model_id, BOLD)}")
            print(f"       source           {m.source}")
            print(
                f"                        device={m.device}  backend={m.backend or 'auto'}  precision={m.precision}  batch={m.batch_size}"
            )
            eff_svcs = config.resolved_output_tag_services(m)
            print("       output services")
            for s in eff_svcs:
                name = service_name_by_key.get(s.key, s.key)
                print(f"         {_c(name, BOLD, CYAN)} {_c(s.key, DIM)}")
            if m.output_filter is not None:
                print(f"       output_filter    (overrides: {', '.join(m.output_filter._raw_keys)})")
        print()

        # Output Filter
        of = config.output_filter
        print(_c("  Output Filter (global)", BOLD))
        print(f"    prefer TLT          {of.prefer_tag_level_thresholds}")
        print(f"    TLT offset          {of.tag_level_threshold_relative_offset}")
        print(f"    default threshold   {of.default_threshold}")
        if of.category_thresholds:
            print("    category thresholds")
            for cat, cfg_ in of.category_thresholds.items():
                tlt_note = _c(" [overrides TLT]", DIM) if cfg_.override_tlt else ""
                print(f"      {cat:<14} {cfg_.threshold:.2f}{tlt_note}")
        cats = of.output_categories
        print(f"    output categories   {', '.join(cats) if cats else '(all)'}")

        # tag inclusions/exclusions
        if of.include_tags:
            print(_c(f"    include tags        {', '.join(of.include_tags)}", GREEN))
        if of.exclude_tags:
            print(_c(f"    exclude tags        {', '.join(of.exclude_tags)}", RED))

        print()

        if of.max_tags_per_category:
            print("    max tags / category")
            for cat, max_ in of.max_tags_per_category.items():
                print(f"      {cat:<14} {max_}")
                print()
        if of.tag_prefix_mapping:
            print("    tag prefix mapping")
            for cat, prefix in of.tag_prefix_mapping.items():
                display_prefix = f"'{prefix}'" if prefix else _c("(none)", DIM)
                print(f"      {cat:<14} → {display_prefix}")
                print()

    # Backup Reminder
    print(_c("      It is strongly recommended to create/update your Hydrus backup.", RED, BOLD))
    if mode in ("infer_only", "default"):
        print(
            _c(
                "      Tip: File paths are resolved. You may close Hydrus now to free up memory if needed.\n"
                + "             While Hydrus is closed/unreachable, no tags can be pushed to Hydrus, but inference will continue.\n"
                + "             After run completion you may run hyvis again with --push-only to push any pending tags to Hydrus.",
                YELLOW,
            )
        )
    print()


# region Run summary


def print_run_summary(
    run_status: str,
    run_id: str,
    mode: str,
    total_infer_ok: int,
    total_infer_err: int,
    total_skipped: int,
    total_push_ok: int,
    total_push_err: int,
) -> None:
    print()
    print(_c("  ══ Run Complete ══════════════════════════════", BOLD))
    print(f"  Status    : {_c(run_status, GREEN if run_status == 'done' else YELLOW)}")
    print(f"  Run ID    : {_c(run_id, DIM)}")
    if mode in ("default", "infer_only"):
        print(f"  Inferred  : {total_infer_ok} ok / {total_infer_err} errors / {total_skipped} skipped")
    if mode in ("default", "push_only"):
        print(f"  Pushed    : {total_push_ok} ok / {total_push_err} errors")
        if total_push_err:
            print(_c("  Tip: run with --push-only to retry failed pushes.", DIM))
    print()
