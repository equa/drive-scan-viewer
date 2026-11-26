#!/usr/bin/env python3
"""Run one ncdu scan per sub-directory of a root, writing <name>.json into a data dir.

Usage: scan-users.py [ROOT] [DATA_DIR]
Log goes to DATA_DIR/scan-users.log and to the terminal.
"""

from pathlib import Path
import logging
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

    for line in result.stdout.splitlines():
        log.info("  stdout: %s", line)
    for line in result.stderr.splitlines():
        log.warning("  stderr: %s", line)

    if result.returncode != 0:
        log.error("ncdu failed for %s (exit %d)", root, result.returncode)
        tmp_output.unlink(missing_ok=True)
        return False

    tmp_output.replace(json_output)
    log.info("Done %s in %.0f s (%.1f MB)", root, time.monotonic() - start,
             json_output.stat().st_size / 1e6)
    return True


def scan_subdirs(root: Path, data_dir: Path) -> int:
    failed = 0
    for item in sub_dirs_iter(root):
        if item.name.startswith("."):
            continue
        json_output = data_dir / (clean_name(item.name) + ".json")
        if not run_scanner(item, json_output):
            failed += 1
    return failed


def main():
    users_root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ROOT
    data_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.cwd()
    setup_logging(data_dir / "scan-users.log")

    log.info("Start: %s -> %s", users_root, data_dir)
    try:
        failed = scan_subdirs(users_root, data_dir)
    except OSError as e:
        log.error("Cannot read %s: %s", users_root, e)
        return 1
    log.info("Finished, %d failed", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
