"""Streaming loader for ncdu JSON exports (`ncdu -o file.json`).

The export is a nested array: ``[major, minor, {meta}, [dirinfo, child, ...]]``
where a child is either an info object (file) or another array (directory).
Large scans do not fit comfortably in memory as Python dicts, so the file is
tokenized incrementally and every entry is packed into flat arrays.

Entries are stored in depth-first pre-order, which gives two useful properties:
a parent always has a lower id than its children, and the subtree of node ``n``
is exactly the id range ``[n, n + items[n])``.
"""

from __future__ import annotations

import gzip
import json
import re
from array import array
from pathlib import Path

F_DIR = 1
F_ERROR = 2  # read_error somewhere at this entry
F_EXCLUDED = 4  # excluded / other filesystem / kernfs
F_HARDLINK = 8  # extra hardlink, size counted elsewhere
F_SPECIAL = 16  # not a regular file (symlink, device, ...)

_SKIP = re.compile(r"[\s,]*")
_CHUNK = 1 << 20
_decoder = json.JSONDecoder()


def _open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="surrogateescape")
    return open(path, "rt", encoding="utf-8", errors="surrogateescape")


class Scan:
    """One loaded ncdu export, packed into flat arrays."""

    def __init__(self, path: Path):
        self.path = path
        self.meta: dict = {}
        self._blob = bytearray()
        self._name_off = array("Q", [0])
        self.parent = array("i")
        self.first_child = array("i")
        self.next_sibling = array("i")
        self.dsize = array("q")
        self.items = array("q")  # entries in subtree, including the node itself
        self.mtime = array("q")  # newest mtime in subtree
        self.flags = array("B")
        self.ext = array("H")  # index into self.exts, 0 = none / directory
        self.exts: list[str] = [""]
        self._load()

    # -- loading ---------------------------------------------------------

    def _add(self, info: dict, parent: int, is_dir: bool, ext_ids: dict) -> int:
        nid = len(self.parent)
        name = info.get("name", "?")
        self._blob += name.encode("utf-8", "surrogateescape")
        self._name_off.append(len(self._blob))
        self.parent.append(parent)
        self.first_child.append(-1)
        self.next_sibling.append(-1)
        if parent >= 0:
            # Prepend; children order is irrelevant, we sort on demand.
            self.next_sibling[nid] = self.first_child[parent]
            self.first_child[parent] = nid
        flags = F_DIR if is_dir else 0
        if info.get("read_error"):
            flags |= F_ERROR
        if info.get("excluded"):
            flags |= F_EXCLUDED
        if info.get("notreg"):
            flags |= F_SPECIAL
        self.dsize.append(int(info.get("dsize", 0)))
        self.items.append(1)
        self.mtime.append(int(info.get("mtime", 0)))
        e = 0
        if not is_dir:
            dot = name.rfind(".")
            if 0 < dot and len(name) - dot <= 12:
                key = name[dot + 1 :].lower()
                e = ext_ids.get(key)
                if e is None:
                    e = ext_ids[key] = len(self.exts)
                    self.exts.append(key)
        self.ext.append(e)
        self.flags.append(flags)
        return nid

    def _load(self) -> None:
        stack: list[int] = []  # open directory node ids
        dev_stack: list[int] = []
        seen_inodes: set[tuple[int, int]] = set()
        ext_ids: dict[str, int] = {"": 0}
        depth = 0
        pending_dir = False
        with _open_text(self.path) as fp:
            buf = fp.read(_CHUNK)
            pos = 0
            eof = not buf
            while True:
                if not eof and len(buf) - pos < 65536:
                    more = fp.read(_CHUNK)
                    eof = not more
                    buf = buf[pos:] + more
                    pos = 0
                pos = _SKIP.match(buf, pos).end()
                if pos >= len(buf):
                    if eof:
                        break
                    continue
                c = buf[pos]
                if c == "[":
                    depth += 1
                    pos += 1
                    pending_dir = depth >= 2
                elif c == "]":
                    if depth >= 2 and stack:
                        stack.pop()
                        dev_stack.pop()
                    depth -= 1
                    pos += 1
                else:
                    obj, pos = _decoder.raw_decode(buf, pos)
                    if depth == 1:
                        if isinstance(obj, dict):
                            self.meta = obj
                        continue
                    if not isinstance(obj, dict):
                        continue
                    parent = stack[-1] if stack else -1
                    if pending_dir:
                        nid = self._add(obj, parent, True, ext_ids)
                        stack.append(nid)
                        dev_stack.append(int(obj.get("dev", dev_stack[-1] if dev_stack else 0)))
                        pending_dir = False
                    else:
                        nid = self._add(obj, parent, False, ext_ids)
                        if obj.get("hlnkc") or int(obj.get("nlink", 1)) > 1:
                            key = (dev_stack[-1] if dev_stack else 0, int(obj.get("ino", -nid)))
                            if key in seen_inodes:
                                self.dsize[nid] = 0
                                self.flags[nid] |= F_HARDLINK
                            else:
                                seen_inodes.add(key)
        if not self.parent:
            raise ValueError("no entries found - is this an ncdu JSON export?")
        self._aggregate()

    def _aggregate(self) -> None:
        parent, ds, it, mt, fl = self.parent, self.dsize, self.items, self.mtime, self.flags
        for n in range(len(parent) - 1, 0, -1):
            p = parent[n]
            if p < 0:
                continue
            ds[p] += ds[n]
            it[p] += it[n]
            if mt[n] > mt[p]:
                mt[p] = mt[n]
            if fl[n] & F_ERROR:
                fl[p] |= F_ERROR

    # -- queries ---------------------------------------------------------

    def __len__(self) -> int:
        return len(self.parent)

    def name(self, n: int) -> str:
        return self._blob[self._name_off[n] : self._name_off[n + 1]].decode("utf-8", "surrogateescape")

    def is_dir(self, n: int) -> bool:
        return bool(self.flags[n] & F_DIR)

    def children(self, n: int) -> list[int]:
        out = []
        c = self.first_child[n]
        while c >= 0:
            out.append(c)
            c = self.next_sibling[c]
        return out

    def ancestors(self, n: int) -> list[int]:
        """Ids from the scan root down to n."""
        out = []
        while n >= 0:
            out.append(n)
            n = self.parent[n]
        out.reverse()
        return out

    def full_path(self, n: int) -> str:
        names = [self.name(i) for i in self.ancestors(n)]
        root = names[0].rstrip("/")
        return "/".join([root, *names[1:]]) if len(names) > 1 else names[0]

    def find_child(self, n: int, name: str) -> int:
        for c in self.children(n):
            if self.name(c) == name:
                return c
        return -1
