"""
push_pending.py  Entry point for `hyvis-push-pending`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="hyvis-push-pending",
        description="Push unpushed inference results and retry failed tag cleanups from the HyVis database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db-path",
        default=None,
        type=Path,
        help="Path to the HyVis SQLite database (default: data/hyvis.db relative to cwd).",
    )
    parser.add_argument("--api-url", default=None, help="Override hydrus.api_url from config.")
    parser.add_argument("--api-key", default=None, help="Override hydrus.api_key from config.")
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Do not wait for Hydrus to become available if it is offline.",
    )
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt.")
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    from hyvis.logging_utils import RED, _c
    from hyvis.push_service import run_push_and_cleanup

    db_path = args.db_path or Path.cwd() / "data" / "hyvis.db"
    if not db_path.exists():
        print(_c(f"ERROR: Database not found at {db_path}", RED), file=sys.stderr)
        return 1

    return await run_push_and_cleanup(
        db_path=db_path,
        api_url_override=args.api_url,
        api_key_override=args.api_key,
        wait_for_hydrus=not args.no_wait,
        wait_interval=5.0,
        skip_confirm=args.yes,
    )


def cli() -> None:
    args = _parse_args()
    from hyvis.logging_utils import setup_logging

    setup_logging(args.log_level)
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    cli()
