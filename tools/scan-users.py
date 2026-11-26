#!/usr/bin/env python3
"""Run one ncdu scan per sub-directory of a root, writing <name>.json into a data dir.

Log goes to DATA_DIR/scan-users.log and to the terminal.
"""

from pathlib import Path
import argparse
import logging
import random
import re
import subprocess
import sys
import time
import unicodedata

log = logging.getLogger("scan-users")

# Default ROOT when none is given; set this to your NAS mount
DEFAULT_ROOT = Path("/<drive path>/Users")


def clean_name(name: str) -> str:
    # Turn accented chars into plain ASCII (é → e, ü → u), drop what can't convert
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"\s+", "_", name.strip())         # whitespace → underscore
    name = re.sub(r"[^A-Za-z0-9._-]", "", name)      # drop everything else
    name = re.sub(r"_+", "_", name).strip("._")      # collapse repeats, trim ends
    return name or "unnamed"


def sub_dirs_iter(root: Path):
    for item in sorted(root.iterdir()):
        if item.is_dir() and not item.is_symlink():
            yield item


def setup_logging(log_file: Path):
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    for handler in (logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    log.setLevel(logging.INFO)


ANSI_ESCAPE = re.compile(r"\x1b(\[[0-?]*[ -/]*[@-~]|[@-_])")


def output_lines(text: str):
    # ncdu can emit bare newlines or terminal escape codes even with -0; skip those
    for line in text.splitlines():
        line = ANSI_ESCAPE.sub("", line).strip()
        if line:
            yield line


def run_scanner(root: Path, json_output: Path) -> bool:
    # Write to a .part file and rename when done, so the web app never sees a half-written scan
    tmp_output = json_output.with_name(json_output.name + ".part")
    # -0: no progress UI, so captured stdout stays readable
    cmd = ["nice", "-19", "ncdu", "-0", "-e", "-x", "-o", str(tmp_output), str(root)]
    log.info("Scanning %s -> %s", root, json_output.name)
    start = time.monotonic()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    except OSError as e:
        log.error("Could not start ncdu for %s: %s", root, e)
        return False

    for line in output_lines(result.stdout):
        log.info("  stdout: %s", line)
    for line in output_lines(result.stderr):
        log.warning("  stderr: %s", line)

    if result.returncode != 0:
        log.error("ncdu failed for %s (exit %d)", root, result.returncode)
        tmp_output.unlink(missing_ok=True)
        return False

    tmp_output.replace(json_output)
    log.info("Done %s in %.0f s (%.1f MB)", root, time.monotonic() - start,
             json_output.stat().st_size / 1e6)
    return True


def scan_subdirs(root: Path, data_dir: Path, max_dirs: int | None = None) -> int:
    dirs = [d for d in sub_dirs_iter(root) if not d.name.startswith(".")]
    if max_dirs is not None and max_dirs < len(dirs):
        dirs = sorted(random.sample(dirs, max_dirs))
        log.info("Test mode: scanning %d random dirs: %s", max_dirs, ", ".join(d.name for d in dirs))
    failed = 0
    for item in dirs:
        json_output = data_dir / (clean_name(item.name) + ".json")
        if not run_scanner(item, json_output):
            failed += 1
    return failed


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run one ncdu scan per sub-folder of ROOT and write <name>.json into DATA_DIR. "
                    "Log goes to DATA_DIR/scan-users.log and to the terminal.")
    parser.add_argument("root", nargs="?", type=Path, default=DEFAULT_ROOT,
                        help="folder whose sub-folders are scanned (default: %(default)s)")
    parser.add_argument("data_dir", nargs="?", type=Path, default=Path.cwd(),
                        help="where the .json files and the log go (default: current directory)")
    parser.add_argument("--max-dirs", type=int, metavar="N",
                        help="test mode: scan only N randomly chosen sub-folders")
    args = parser.parse_args()
    if args.max_dirs is not None and args.max_dirs < 1:
        parser.error("--max-dirs must be at least 1")
    if not args.data_dir.is_dir():
        parser.error(f"data dir does not exist: {args.data_dir}")
    return args


def main():
    args = parse_args()
    setup_logging(args.data_dir / "scan-users.log")

    log.info("Start: %s -> %s", args.root, args.data_dir)
    try:
        failed = scan_subdirs(args.root, args.data_dir, args.max_dirs)
    except OSError as e:
        log.error("Cannot read %s: %s", args.root, e)
        return 1
    log.info("Finished, %d failed", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
