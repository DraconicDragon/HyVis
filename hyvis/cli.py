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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="hyvis",
        description="Tag files from Hydrus using image tagging models.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("config", metavar="CONFIG_PATH", type=Path, help="Path to the TOML configuration file.")
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
    parser.add_argument("--infer-only", action="store_true", help="Run inference only; do not push results to Hydrus.")
    parser.add_argument("--no-preview", action="store_true", help="Skip any configured page previews.")
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: config or WARNING).",
    )
    parser.add_argument("--version", action=_VersionAction, help="Show program's version number and exit.")
    return parser.parse_args()
