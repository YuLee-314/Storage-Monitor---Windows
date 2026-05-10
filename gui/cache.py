"""Shared in-memory cache for directory tree data — 30min TTL, cleared on close."""

import time
from collections import OrderedDict

TTL = 30 * 60  # 30 minutes
MAX_ENTRIES = 200


class DirCache:
    """Thread-safe size cache with TTL expiry."""

    def __init__(self):
        self._data: OrderedDict[str, dict] = OrderedDict()  # path → {ts, total, children}
        self._total_bytes: dict[str, int] = {}  # drive_letter → total_bytes

    def set_total(self, drive: str, total_bytes: int):
        self._total_bytes[drive] = total_bytes

    def get_total(self, drive: str) -> int:
        return self._total_bytes.get(drive, 1)

    def put(self, path: str, entries: list, total: int):
        """Store directory listing results from a SizeFiller/scan."""
        self._evict()
        # Build children dict: name → {size, is_dir}
        children = {}
        for e in entries:
            children[e.name] = {
                "size": e.size_bytes,
                "is_dir": e.is_dir,
                "path": e.path,
            }
        self._data[path] = {
            "ts": time.time(),
            "total": total,
            "children": children,
        }
        # Evict oldest if over limit
        while len(self._data) > MAX_ENTRIES:
            self._data.popitem(last=False)

    def get(self, path: str) -> dict | None:
        """Get cached data for a path. Returns None if missing or expired."""
        self._evict()
        entry = self._data.get(path)
        if entry is None:
            return None
        if time.time() - entry["ts"] > TTL:
            del self._data[path]
            return None
        return entry

    def get_or_none(self, path: str) -> dict | None:
        """Get without eviction check (for polling)."""
        entry = self._data.get(path)
        if entry is None:
            return None
        if time.time() - entry["ts"] > TTL:
            return None
        return entry

    def has(self, path: str) -> bool:
        return self.get_or_none(path) is not None

    def _evict(self):
        """Remove expired entries."""
        now = time.time()
        expired = [k for k, v in self._data.items() if now - v["ts"] > TTL]
        for k in expired:
            del self._data[k]

    def clear(self):
        self._data.clear()
        self._total_bytes.clear()

    def all_paths(self) -> list[str]:
        """Return all non-expired cached paths."""
        self._evict()
        return list(self._data.keys())

    def children_sorted(self, path: str) -> list[dict]:
        """Return children of a path sorted by size descending."""
        entry = self.get(path)
        if not entry:
            return []
        kids = list(entry["children"].values())
        kids.sort(key=lambda x: x["size"], reverse=True)
        return kids


# Singleton
_cache: DirCache | None = None


def get_cache() -> DirCache:
    global _cache
    if _cache is None:
        _cache = DirCache()
    return _cache
