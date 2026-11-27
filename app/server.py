"""NAS usage browser - serves ncdu JSON exports to a sunburst web UI.

Stdlib only. Configuration via environment:

  DATA_DIR     directory with *.json / *.json.gz ncdu exports (default ./data)
  HOST, PORT   listen address (default 0.0.0.0:8080)
  MAX_LOADED   max scans kept in memory, least recently used dropped (default 0 = all)
  PRELOAD      "1" to parse all scans in the background at startup (default 1)
  PATH_MAP     rewrite scan paths for display, e.g. "/<drive path>=\\\\nas\\q"
               (several separated by ";")
"""

from __future__ import annotations

import heapq
from bisect import bisect_right
import json
import logging
import mimetypes
import os
import threading
import time
from collections import OrderedDict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ncdu import F_DIR, F_ERROR, F_EXCLUDED, F_HARDLINK, Scan

log = logging.getLogger("nasbrowser")

DATA_DIR = Path(os.environ.get("DATA_DIR", "data")).resolve()
STATIC_DIR = Path(__file__).with_name("static")
MAX_LOADED = int(os.environ.get("MAX_LOADED", "0"))
PATH_MAP = [
    tuple(part.split("=", 1))
    for part in os.environ.get("PATH_MAP", "").split(";")
    if "=" in part
]
SUFFIXES = (".json", ".json.gz")
MAX_CHILDREN = 2000
DAY = 86400
YEAR = 365.25 * DAY
AGE_YEARS = (1, 2, 4, 8, 10)  # age bucket edges, shared with the UI legend


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# -- scan registry -------------------------------------------------------


class Registry:
    """Lazily loads scans, reloads them when the file changes, evicts LRU."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._loaded: OrderedDict[str, tuple[float, Scan]] = OrderedDict()
        self._errors: dict[str, tuple[float, str]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def files(self) -> dict[str, Path]:
        out = {}
        if self.data_dir.is_dir():
            for p in sorted(self.data_dir.rglob("*")):
                if p.is_file() and p.name.endswith(SUFFIXES):
                    out[p.relative_to(self.data_dir).as_posix()] = p
        return out

    def status(self, name: str) -> tuple[Scan | None, str | None]:
        with self._lock:
            entry = self._loaded.get(name)
            err = self._errors.get(name)
        return (entry[1] if entry else None), (err[1] if err else None)

    def get(self, name: str) -> Scan:
        path = self.files().get(name)
        if path is None:
            raise ApiError(HTTPStatus.NOT_FOUND, f"unknown scan: {name}")
        mtime = path.stat().st_mtime
        with self._lock:
            lock = self._locks.setdefault(name, threading.Lock())
        with lock:
            with self._lock:
                entry = self._loaded.get(name)
                if entry and entry[0] == mtime:
                    self._loaded.move_to_end(name)
                    return entry[1]
                err = self._errors.get(name)
                if err and err[0] == mtime:
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, f"{name}: {err[1]}")
            t0 = time.monotonic()
            try:
                scan = Scan(path)
            except Exception as exc:  # malformed file - remember, don't retry
                log.exception("failed to load %s", name)
                with self._lock:
                    self._errors[name] = (mtime, str(exc))
                raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, f"{name}: {exc}") from exc
            log.info("loaded %s: %d entries in %.1fs", name, len(scan), time.monotonic() - t0)
            with self._lock:
                self._errors.pop(name, None)
                self._loaded[name] = (mtime, scan)
                self._loaded.move_to_end(name)
                while MAX_LOADED and len(self._loaded) > MAX_LOADED:
                    self._loaded.popitem(last=False)
            _cache.clear()
            return scan

    def preload(self) -> None:
        for name in list(self.files())[: MAX_LOADED or None]:
            try:
                self.get(name)
            except ApiError:
                pass


registry = Registry(DATA_DIR)
_cache: OrderedDict[tuple, object] = OrderedDict()
_cache_lock = threading.Lock()


def cached(key: tuple, fn):
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    val = fn()
    with _cache_lock:
        _cache[key] = val
        while len(_cache) > 64:
            _cache.popitem(last=False)
    return val


# -- node model ----------------------------------------------------------
#
# A node reference ("ref") is "<scan name>:<id>". The empty ref is a virtual
# root above all selected scans; with a single selected scan it resolves to
# that scan's root.


class View:
    def __init__(self, scans: list[str]):
        if not scans:
            raise ApiError(HTTPStatus.BAD_REQUEST, "no scans selected")
        self.names = scans
        self.scans = {name: registry.get(name) for name in scans}
        self.metric = "dsize"  # disk usage, as du reports it

    def parse(self, ref: str) -> tuple[Scan | None, int]:
        if not ref:
            if len(self.names) == 1:
                return self.scans[self.names[0]], 0
            return None, -1
        name, _, sid = ref.rpartition(":")
        scan = self.scans.get(name)
        try:
            nid = int(sid)
        except ValueError:
            nid = -1
        if scan is None or not 0 <= nid < len(scan):
            raise ApiError(HTTPStatus.NOT_FOUND, f"unknown node: {ref}")
        return scan, nid

    def ref(self, scan: Scan, n: int) -> str:
        return f"{self.name_of(scan)}:{n}"

    def name_of(self, scan: Scan) -> str:
        for name, s in self.scans.items():
            if s is scan:
                return name
        raise KeyError

    def size(self, scan: Scan, n: int) -> int:
        return getattr(scan, self.metric)[n]

    def node(self, scan: Scan | None, n: int) -> dict:
        if scan is None:
            roots = [(s, 0) for s in self.scans.values()]
            return {
                "r": "",
                "n": "All selected scans",
                "s": sum(self.size(s, 0) for s, _ in roots),
                "i": sum(s.items[0] for s, _ in roots),
                "m": max(s.mtime[0] for s, _ in roots),
                "d": True,
                "k": len(roots),
            }
        fl = scan.flags[n]
        out = {
            "r": self.ref(scan, n),
            "n": scan.name(n),
            "s": self.size(scan, n),
            "i": scan.items[n],
            "m": scan.mtime[n],
            "d": bool(fl & F_DIR),
        }
        if fl & F_DIR:
            out["k"] = int(scan.first_child[n] >= 0)
        if fl & F_ERROR:
            out["e"] = 1
        if fl & F_EXCLUDED:
            out["x"] = 1
        if fl & F_HARDLINK:
            out["h"] = 1
        return out

    def children(self, scan: Scan | None, n: int) -> list[tuple[Scan, int]]:
        if scan is None:
            return [(s, 0) for s in self.scans.values()]
        return [(scan, c) for c in scan.children(n)]

    def sorted_children(self, scan: Scan | None, n: int) -> list[tuple[Scan, int]]:
        kids = self.children(scan, n)
        kids.sort(key=lambda sc: self.size(*sc), reverse=True)
        return kids

    def crumbs(self, scan: Scan | None, n: int) -> list[dict]:
        out = []
        if len(self.names) > 1:
            out.append({"r": "", "n": "All scans"})
        if scan is not None:
            for i in scan.ancestors(n):
                out.append({"r": self.ref(scan, i), "n": scan.name(i)})
        return out

    def subtrees(self, scan: Scan | None, n: int):
        """(scan, start, stop) id ranges covering the subtree."""
        if scan is None:
            return [(s, 0, len(s)) for s in self.scans.values()]
        return [(scan, n, n + scan.items[n])]


def display_path(scan: Scan, n: int) -> str:
    p = scan.full_path(n)
    for src, dst in PATH_MAP:
        if p == src or p.startswith(src.rstrip("/") + "/"):
            rest = p[len(src.rstrip("/")) :]
            if "\\" in dst:
                rest = rest.replace("/", "\\")
            return dst.rstrip("\\/") + rest
    return p


# -- API endpoints -------------------------------------------------------


def api_scans(_q: dict) -> dict:
    out = []
    for name, path in registry.files().items():
        st = path.stat()
        scan, err = registry.status(name)
        item = {"name": name, "bytes": st.st_size, "fileMtime": int(st.st_mtime), "loaded": bool(scan)}
        if scan:
            item.update(
                root=display_path(scan, 0),
                timestamp=scan.meta.get("timestamp"),
                entries=len(scan),
                dsize=scan.dsize[0],
            )
        if err:
            item["error"] = err
        out.append(item)
    return {"scans": out, "pathMap": bool(PATH_MAP)}


def api_node(q: dict) -> dict:
    """A node, its breadcrumb and its (size sorted) children."""
    v = view(q)
    scan, n = v.parse(q.get("ref", ""))
    kids = v.sorted_children(scan, n)
    rest = kids[MAX_CHILDREN:]
    out = {
        "node": v.node(scan, n),
        "crumbs": v.crumbs(scan, n),
        "children": [v.node(*k) for k in kids[:MAX_CHILDREN]],
    }
    if scan is not None:
        out["path"] = display_path(scan, n)
        out["scanned"] = scan.meta.get("timestamp")
    if rest:
        out["more"] = {"count": len(rest), "s": sum(v.size(*k) for k in rest)}
    return out


def api_ring(q: dict) -> dict:
    """Nested children for the sunburst, small slices folded together."""
    v = view(q)
    scan, n = v.parse(q.get("ref", ""))
    depth = max(1, min(int(q.get("depth", 6)), 12))
    root = v.node(scan, n)
    threshold = max(root["s"] * float(q.get("min", 0.0025)), 1)

    def build(scan, n, level):
        node = v.node(scan, n)
        if level >= depth or not node["d"]:
            return node
        kids, other, count = [], 0, 0
        for c in v.sorted_children(scan, n):
            s = v.size(*c)
            if s >= threshold:
                kids.append(build(*c, level + 1))
            else:
                other += s
                count += 1
        if other >= threshold:
            kids.append({"n": f"{count} smaller items", "s": other, "o": 1, "i": count})
        node["c"] = kids
        return node

    return build(scan, n, 0)


def api_stats(q: dict) -> dict:
    """Size per file type and per last-modified year, for the subtree."""
    v = view(q)
    scan, n = v.parse(q.get("ref", ""))

    def compute():
        by_ext: dict[tuple[int, int], list[int]] = {}
        by_year: dict[int, list[int]] = {}
        # cutoffs oldest first; bucket 0 = newer than 1 year ... 5 = older than 10
        cutoffs = [time.time() - y * YEAR for y in reversed(AGE_YEARS)]
        nb = len(AGE_YEARS)
        ages = [0] * (nb + 1)
        files = 0
        for s, a, b in v.subtrees(scan, n):
            size, flags, ext, mt = getattr(s, v.metric), s.flags, s.ext, s.mtime
            ext_acc: dict[int, list[int]] = {}
            for i in range(a, b):
                if flags[i] & F_DIR:
                    continue
                files += 1
                sz = size[i]
                e = ext_acc.get(ext[i])
                if e is None:
                    ext_acc[ext[i]] = [sz, 1]
                else:
                    e[0] += sz
                    e[1] += 1
                y = time.gmtime(mt[i]).tm_year if mt[i] > 0 else 0
                yy = by_year.get(y)
                if yy is None:
                    by_year[y] = [sz, 1]
                else:
                    yy[0] += sz
                    yy[1] += 1
                ages[nb - bisect_right(cutoffs, mt[i])] += sz
            for e, val in ext_acc.items():
                key = s.exts[e]
                acc = by_ext.setdefault(key, [0, 0])
                acc[0] += val[0]
                acc[1] += val[1]
        exts = sorted(by_ext.items(), key=lambda kv: kv[1][0], reverse=True)
        top, rest = exts[:24], exts[24:]
        ext_list = [{"ext": k, "s": s, "count": c} for k, (s, c) in top]
        if rest:
            ext_list.append({
                "ext": f"{len(rest)} other types", "o": 1,
                "s": sum(x[1][0] for x in rest), "count": sum(x[1][1] for x in rest),
            })
        years = [{"year": y, "s": s, "count": c} for y, (s, c) in sorted(by_year.items())]
        return {"files": files, "exts": ext_list, "years": years, "ages": ages}

    key = ("stats", tuple(v.names), q.get("ref", ""), v.metric, tuple(id(s) for s in v.scans.values()))
    return cached(key, compute)


def api_find(q: dict) -> dict:
    """Largest files, or largest folders untouched for `older` days."""
    v = view(q)
    scan, n = v.parse(q.get("ref", ""))
    kind = q.get("kind", "files")
    limit = max(1, min(int(q.get("limit", 200)), 2000))
    older = float(q.get("older", 0) or 0)
    cutoff = time.time() - older * DAY if older > 0 else None
    text = q.get("q", "").strip().lower()
    want_ext = {e.strip().lstrip(".").lower() for e in q.get("ext", "").split(",") if e.strip()}

    def candidates():
        for s, a, b in v.subtrees(scan, n):
            size, flags, mt, parent = getattr(s, v.metric), s.flags, s.mtime, s.parent
            ext_ok = None
            if want_ext:
                ext_ok = {i for i, e in enumerate(s.exts) if e in want_ext}
            for i in range(a, b):
                is_dir = flags[i] & F_DIR
                if kind == "dirs":
                    if not is_dir or cutoff is None or mt[i] >= cutoff:
                        continue
                    p = parent[i]
                    # only the topmost stale folder of a stale branch
                    if i != a and p >= 0 and mt[p] < cutoff:
                        continue
                else:
                    if is_dir:
                        continue
                    if cutoff is not None and mt[i] >= cutoff:
                        continue
                    if ext_ok is not None and s.ext[i] not in ext_ok:
                        continue
                if text and text not in s.name(i).lower():
                    continue
                yield size[i], s, i

    key = ("find", tuple(v.names), q.get("ref", ""), v.metric, kind, limit, older, text,
           tuple(sorted(want_ext)), tuple(id(s) for s in v.scans.values()))

    def compute():
        total = count = 0
        heap: list[tuple[int, int, Scan, int]] = []
        tie = 0
        for sz, s, i in candidates():
            total += sz
            count += 1
            tie += 1
            item = (sz, tie, s, i)
            if len(heap) < limit:
                heapq.heappush(heap, item)
            elif sz > heap[0][0]:
                heapq.heapreplace(heap, item)
        heap.sort(reverse=True, key=lambda t: (t[0], t[1]))
        rows = []
        for _, _, s, i in heap:
            row = v.node(s, i)
            row["p"] = display_path(s, i)
            if s.parent[i] >= 0:
                row["pr"] = v.ref(s, s.parent[i])
            rows.append(row)
        return {"rows": rows, "total": total, "count": count}

    return cached(key, compute)


def api_resolve(q: dict) -> dict:
    """Map "<scan>:<path relative to scan root>" to a ref (for bookmarks)."""
    v = view(q)
    target = q.get("path", "")
    name, _, rel = target.partition(":")
    scan = v.scans.get(name)
    if scan is None:
        return {"ref": ""}
    n = 0
    for part in [p for p in rel.split("/") if p]:
        c = scan.find_child(n, part)
        if c < 0:
            break
        n = c
    return {"ref": v.ref(scan, n)}


def view(q: dict) -> View:
    scans = [s for s in q.get("scans", "").split(",") if s]
    return View(scans)


ROUTES = {
    "/api/scans": api_scans,
    "/api/node": api_node,
    "/api/ring": api_ring,
    "/api/stats": api_stats,
    "/api/find": api_find,
    "/api/resolve": api_resolve,
}


# -- HTTP ----------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "nasbrowser/1.0"

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path in ROUTES:
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                body = json.dumps(ROUTES[url.path](q), separators=(",", ":")).encode()
                self._send(HTTPStatus.OK, body, "application/json")
            except ApiError as exc:
                self._send(exc.status, json.dumps({"error": str(exc)}).encode(), "application/json")
            except (ValueError, KeyError) as exc:
                self._send(HTTPStatus.BAD_REQUEST, json.dumps({"error": str(exc)}).encode(), "application/json")
            return
        if url.path == "/healthz":
            self._send(HTTPStatus.OK, b"ok", "text/plain")
            return
        rel = "index.html" if url.path in ("", "/") else url.path.lstrip("/")
        f = (STATIC_DIR / rel).resolve()
        if not f.is_relative_to(STATIC_DIR) or not f.is_file():
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            return
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        self._send(HTTPStatus.OK, f.read_bytes(), ctype)

    def _send(self, status, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/j")) else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    log.info("serving scans from %s on http://%s:%d", DATA_DIR, host, port)
    if os.environ.get("PRELOAD", "1") == "1":
        threading.Thread(target=registry.preload, daemon=True).start()
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
