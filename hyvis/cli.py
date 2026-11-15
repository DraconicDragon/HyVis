import argparse
import importlib.metadata
import subprocess
from pathlib import Path


def get_version() -> str:
    try:
        version = importlib.metadata.version("hyvis")
        version = "v" + version
    except importlib.metadata.PackageNotFoundError:
        version = "source"

    try:
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        version = f"{version} (git-{git_hash})"
    except Exception:
        pass

    return version


class _VersionAction(argparse.Action):
    """Prints the version (installed or source) and git hash, then exits."""

    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(
            option_strings=option_strings,
            dest=dest,
            nargs=0,
            **kwargs,
        )

    def __call__(self, parser, namespace, values, option_string=None):
        print(f"hyvis {get_version()}")
        parser.exit()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="hyvis",
        description="Tag files from Hydrus using image tagging models.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "config",
        nargs="?",
        default=None,
        metavar="CONFIG_PATH",
        type=Path,
        help="Path to the TOML configuration file (optional when using --resume).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an unfinished inference run using the saved snapshot in hyvis.db.",
    )
    parser.add_argument(
        "--discard-run",
        action="store_true",
        help="Discard any unfinished active run in hyvis.db and start fresh.",
    )
    parser.add_argument(
        "--db",
        default=None,
        type=Path,
        help="Override database path (useful when running --resume without CONFIG_PATH; defaults to data/hyvis.db).",
    )
    parser.add_argument(
        "--cache-db",
        default=None,
        type=Path,
        help="Override cache database path (defaults to config database.cache_path or cache.db alongside main DB).",
    )
    parser.add_argument(
        "--update-client-binding",
        action="store_true",
        help="Acknowledge and overwrite the stored Hydrus client binding in hyvis.db if URL, key, or services changed.",
    )
    parser.add_argument("--api-url", default=None, help="Override hydrus.api_url from config.")
    parser.add_argument("--api-key", default=None, help="Override hydrus.api_key from config.")
    parser.add_argument(
        "--extra-hash-file",
        default=None,
        type=Path,
        help="Path to a text file containing one sha256 hash per line. (for wd-e621-hydrus-tagger parity)",
    )
    parser.add_argument("--yes", "-y", action="store_true", help="Skip all confirmation prompts.")
    parser.add_argument("--force", "-f", action="store_true", help="Ignore the DB cache; re-process all matched files.")
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Do not wait for Hydrus to become available if it is unreachable and fail fast instead.",
    )
    parser.add_argument("--infer-only", action="store_true", help="Run inference only; do not push results to Hydrus.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run model inference and tag filtering without saving to the database or pushing to Hydrus.",
    )
    parser.add_argument(
        "--push-only", action="store_true", help="Skip inference and only drain the pending push queue to Hydrus."
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear the raw inference cache from cache.db and reclaim disk space.",
    )
    parser.add_argument("--no-preview", action="store_true", help="Skip any configured page previews.")
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: config or INFO).",
    )
    parser.add_argument("--version", action=_VersionAction, help="Show program's version number and exit.")

    args = parser.parse_args(argv)

    if args.resume and args.discard_run:
        parser.error("--resume and --discard-run cannot be used together.")
    if args.resume and args.force:
        parser.error("--resume cannot be combined with --force (uses the unfinished run's original force setting).")
    if args.resume and args.dry_run:
        parser.error("--resume cannot be combined with --dry-run.")
    if args.resume and args.extra_hash_file is not None:
        parser.error(
            "--resume cannot be combined with --extra-hash-file (targets are already locked from the original run)."
        )
    if args.resume and (args.push_only or args.clear_cache):
        parser.error("--resume cannot be combined with --push-only or --clear-cache.")
    if not args.resume and args.config is None:
        parser.error("CONFIG_PATH is required unless --resume is specified.")

    return args
