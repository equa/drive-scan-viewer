#!/usr/bin/env python3
"""Move folders and files into the drive's archive or trash, keeping their path.

The drive root is the nearest folder, from the current directory upwards, that
holds both 0_ARCHIVE___ and 0_TRASH___. Paths are kept relative to that root, so
running in <root>/Users/x/Projects/A:

    g-drive-cleanup.py --archive cfd docs

moves cfd and docs to <root>/0_ARCHIVE___/Users/x/Projects/A/. This works the
same whatever the drive is mounted as (/mnt/q, G:\\, ...). --restore moves them
back. Every move is appended to cleanup.log in the archive or trash folder.
"""

from datetime import datetime
from pathlib import Path
import argparse
import getpass
import os
import sys

ARCHIVE = "0_ARCHIVE___"
TRASH = "0_TRASH___"
MARKERS = (ARCHIVE, TRASH)
LOG_NAME = "cleanup.log"


class CleanupError(Exception):
    pass


def absolute(path: Path) -> Path:
    # Not resolve(): on Windows that turns a mapped drive G:\ into \\server\share
    return Path(os.path.normpath(path.absolute()))


def find_root(start: Path) -> Path:
    for folder in (start, *start.parents):
        if all((folder / m).is_dir() for m in MARKERS):
            return folder
    raise CleanupError(f"no folder above {start} holds both {ARCHIVE} and {TRASH}")


def relative_to_root(path: Path, root: Path) -> Path:
    try:
        rel = path.relative_to(root)
    except ValueError:
        raise CleanupError(f"{path} is outside the drive root {root}") from None
    if not rel.parts:
        raise CleanupError("refusing to move the drive root itself")
    if rel.parts[0].casefold() in (m.casefold() for m in MARKERS):
        raise CleanupError(f"{path} is already inside {rel.parts[0]}")
    return rel


def free_name(path: Path, keep_both: bool) -> Path:
    if not path.exists() and not path.is_symlink():
        return path
    if not keep_both:
        raise CleanupError(f"{path} already exists (use --keep-both to add a timestamp)")
    stamp = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    return path.with_name(f"{path.name}.{stamp}")


def write_log(marker_dir: Path, op: str, src: Path, dst: Path, root: Path):
    line = "\t".join([datetime.now().isoformat(timespec="seconds"), getpass.getuser(), op,
                      src.relative_to(root).as_posix(), dst.relative_to(root).as_posix()])
    with open(marker_dir / LOG_NAME, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def move(src: Path, dst: Path, dry_run: bool):
    print(f"{src}\n  -> {dst}")
    if dry_run:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    # os.rename, not shutil.move: it fails instead of copying when src and dst are
    # on different drives, so a move is always a quick rename on the server
    try:
        os.rename(src, dst)
    except OSError as e:
        raise CleanupError(f"could not move {src}: {e}") from None


def put_away(path: Path, root: Path, marker: str, args) -> None:
    rel = relative_to_root(path, root)
    if not path.exists() and not path.is_symlink():
        raise CleanupError(f"{path} does not exist")
    if absolute(Path.cwd()).is_relative_to(path):
        raise CleanupError(f"{path} holds the current directory; cd out of it first")
    dst = free_name(root / marker / rel, args.keep_both)
    move(path, dst, args.dry_run)
    if not args.dry_run:
        write_log(root / marker, marker.strip("0_"), path, dst, root)


def restore(path: Path, root: Path, args) -> None:
    rel = relative_to_root(path, root)
    found = [m for m in MARKERS if (root / m / rel).exists() or (root / m / rel).is_symlink()]
    if not found:
        raise CleanupError(f"{rel.as_posix()} is in neither {ARCHIVE} nor {TRASH}")
    if len(found) > 1:
        if not args.source:
            raise CleanupError(f"{rel.as_posix()} is in both; choose with --from archive|trash")
        found = [ARCHIVE if args.source == "archive" else TRASH]
    marker = found[0]
    src = root / marker / rel
    dst = free_name(path, args.keep_both)
    move(src, dst, args.dry_run)
    if not args.dry_run:
        write_log(root / marker, "RESTORE", src, dst, root)
        prune_empty(src.parent, root / marker)


def prune_empty(folder: Path, stop: Path):
    # Drop the now empty parent folders that were only made to hold the moved item
    while folder != stop and folder.is_relative_to(stop):
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def parse_args():
    parser = argparse.ArgumentParser(
        description=f"Move folders/files into {ARCHIVE} or {TRASH} at the drive root, keeping "
                    "their path below the root, or move them back.")
    op = parser.add_mutually_exclusive_group(required=True)
    op.add_argument("--archive", action="store_const", dest="op", const="archive",
                    help=f"move PATHs to {ARCHIVE}")
    op.add_argument("--trash", action="store_const", dest="op", const="trash",
                    help=f"move PATHs to {TRASH}")
    op.add_argument("--restore", action="store_const", dest="op", const="restore",
                    help="move PATHs back from the archive or trash (give them as they were "
                         "before, e.g. run in the folder they were moved from)")
    parser.add_argument("paths", nargs="+", type=Path, metavar="PATH")
    parser.add_argument("--from", dest="source", choices=("archive", "trash"),
                        help="with --restore: where to take PATH from if it is in both")
    parser.add_argument("--keep-both", action="store_true",
                        help="if the target exists, add a timestamp to the name instead of failing")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="only show what would be moved")
    return parser.parse_args()


def main():
    args = parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # keep stdout and stderr in order when piped
    try:
        root = find_root(absolute(Path.cwd()))
    except CleanupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"Drive root: {root}")

    failed = 0
    for path in args.paths:
        try:
            if args.op == "restore":
                restore(absolute(path), root, args)
            else:
                put_away(absolute(path), root, ARCHIVE if args.op == "archive" else TRASH, args)
        except CleanupError as e:
            print(f"error: {e}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
