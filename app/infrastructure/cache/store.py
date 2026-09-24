"""Namespaced TTL cache backed by SQLite.

Separate namespaces (with their own TTLs) for web search, reverse image
search, page parsing, downloaded-image metadata and face embeddings. Keys are
deterministic hashes of *all* inputs that influence the cached value (see
:func:`cache_key`).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.config import PIPELINE_VERSION
from app.utils.canonical import stable_hash

NAMESPACES = ("web_search", "reverse_image", "page", "face_embedding", "profile")


def cache_key(namespace: str, **inputs: Any) -> str:
    return stable_hash({"ns": namespace, "v": PIPELINE_VERSION, **inputs}, length=40)


class CacheStore:
    def __init__(self, path: Path | str | None):
        self._lock = threading.Lock()
        target = ":memory:" if path is None else str(path)
        self._conn = sqlite3.connect(target, check_same_thread=False)
        if path is not None:
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cache (namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,"
            " expires_at REAL NOT NULL, PRIMARY KEY (namespace, key))"
        )
        self._conn.commit()
        self.hits: Counter[str] = Counter()
        self.misses: Counter[str] = Counter()

    def get(self, namespace: str, key: str) -> Any | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value, expires_at FROM cache WHERE namespace=? AND key=?", (namespace, key)
            ).fetchone()
        if not row or row[1] < time.time():
            self.misses[namespace] += 1
            return None
        self.hits[namespace] += 1
        return json.loads(row[0])

    def set(self, namespace: str, key: str, value: Any, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        blob = json.dumps(value, default=str, separators=(",", ":"))
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache (namespace, key, value, expires_at) VALUES (?, ?, ?, ?)",
                (namespace, key, blob, time.time() + ttl_seconds),
            )
            self._conn.commit()

    def purge_expired(self) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM cache WHERE expires_at < ?", (time.time(),))
            self._conn.commit()
            return cur.rowcount

    def clear(self, namespace: str | None = None) -> int:
        with self._lock:
            if namespace:
                cur = self._conn.execute("DELETE FROM cache WHERE namespace=?", (namespace,))
            else:
                cur = self._conn.execute("DELETE FROM cache")
            self._conn.commit()
            return cur.rowcount

    def stats(self) -> dict[str, dict[str, int]]:
        return {ns: {"hits": self.hits[ns], "misses": self.misses[ns]} for ns in NAMESPACES}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
