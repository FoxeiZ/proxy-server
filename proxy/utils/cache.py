from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Optional, TypeVar
from urllib.parse import urlparse

from .._types.nhentai import NhentaiGallery
from ..config import Config
from ..singleton import Singleton
from ..utils.logger import get_logger

V = TypeVar("V")
K = TypeVar("K")

__all__ = ("GalleryInfoCache", "ResourceCache", "LRUCache", "ThumbnailCache")

logger = get_logger(__name__)


def extract_top_level_domain(url: str) -> str:
    try:
        parsed = urlparse(
            f"https://{url}" if not url.startswith(("http://", "https://")) else url
        )
        hostname = parsed.hostname or parsed.netloc

        if not hostname:
            return url

        # Handle IP addresses and localhost - return as-is
        if re.match(r"^(\d{1,3}\.){3}\d{1,3}$", hostname) or hostname in (
            "localhost",
            "127.0.0.1",
        ):
            return parsed.netloc or hostname

        # Split domain parts
        parts = hostname.split(".")
        if len(parts) < 2:
            return parsed.netloc or hostname

        # Extract top-level domain (last 2 parts for most cases)
        # Handle special cases like .co.uk, .com.au, etc.
        if len(parts) >= 3 and parts[-2] in (
            "co",
            "com",
            "net",
            "org",
            "gov",
            "edu",
            "ac",
        ):
            top_domain = ".".join(parts[-3:])
        else:
            top_domain = ".".join(parts[-2:])

        return top_domain

    except Exception:
        return url


def generate_cache_keys(url: str) -> tuple[str, str]:
    try:
        if url.startswith(("http://", "https://")):
            parsed = urlparse(url)
            domain = extract_top_level_domain(parsed.netloc)
            path = parsed.path
        else:
            # format: netloc/path
            parts = url.split("/", 1)
            if len(parts) == 1:
                domain = extract_top_level_domain(parts[0])
                path = "/"
            else:
                domain = extract_top_level_domain(parts[0])
                path = "/" + parts[1]

        domain_key = f"{domain}{path}"
        return domain_key, url

    except Exception:
        # fallback
        return url, url


@dataclass
class CacheEntry:
    data: bytes
    headers: dict
    size: int
    created_at: float
    last_accessed: float
    access_count: int
    content_type: Optional[str] = None

    def is_expired(self, ttl: float) -> bool:
        return time.time() - self.created_at > ttl

    def touch(self) -> None:
        self.last_accessed = time.time()
        self.access_count += 1


class CacheStats(NamedTuple):
    hits: int
    misses: int
    domain_hits: int
    url_hits: int
    size: int
    memory_usage: int
    hit_rate: float
    domain_hit_rate: float


class LRUCache(OrderedDict[K, V]):
    """Thread-safe LRU cache with configurable size limit."""

    def __init__(self, max_size: int):
        super().__init__()
        self.max_size = max_size
        self._lock = threading.Lock()

    def get(self, key: K) -> V | None:  # type: ignore
        with self._lock:
            if key not in self:
                return None
            else:
                self.move_to_end(key)
                return self[key]

    def put(self, key: K, value: V) -> None:
        with self._lock:
            self[key] = value
            self.move_to_end(key)
            if len(self) > self.max_size:
                self.popitem(last=False)

    def remove(self, key: K) -> None:
        with self._lock:
            if key in self:
                del self[key]

    def clear(self) -> None:
        with self._lock:
            super().clear()


class ResourceCache(Singleton):
    def __init__(self):
        super().__init__()
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()

        self._max_items = Config.cache_max_items
        self._max_memory_mb = Config.cache_max_memory_mb
        self._default_ttl = Config.cache_ttl_seconds
        self._max_item_size_mb = Config.cache_max_item_size_mb

        self._hits = 0
        self._misses = 0
        self._domain_hits = 0
        self._url_hits = 0
        self._current_memory = 0

        self._cleanup_thread = None
        self._start_cleanup_thread()

        logger.info(
            "initialized %s: max_items=%d, max_memory=%dMB, ttl=%ds",
            self.__class__.__name__,
            self._max_items,
            self._max_memory_mb,
            self._default_ttl,
        )

    def _start_cleanup_thread(self) -> None:
        if self._cleanup_thread is not None:
            return

        def cleanup_worker():
            import time

            while True:
                try:
                    time.sleep(300)  # cleanup every 5 minutes
                    expired_count = self.cleanup_expired()
                    if expired_count > 0:
                        stats = self.get_stats()
                        logger.info(
                            "cache cleanup completed: removed %d expired entries, %d items, %.1fMB, %.1f%% hit rate",
                            expired_count,
                            stats.size,
                            stats.memory_usage / 1024 / 1024,
                            stats.hit_rate,
                        )
                except Exception as e:
                    logger.error("error in cache cleanup task: %s", e)

        self._cleanup_thread = threading.Thread(
            target=cleanup_worker, daemon=True, name="CacheCleanup"
        )
        self._cleanup_thread.start()
        logger.info("started cache cleanup background thread")

    def get(self, key: str) -> Optional[tuple[dict, bytes]]:
        """Get a cached resource using smart cache key strategy.

        Tries domain-level cache first, then falls back to full URL cache.
        """
        domain_key, url_key = generate_cache_keys(key)

        with self._lock:
            entry = self._cache.get(domain_key)
            if entry is not None and not entry.is_expired(self._default_ttl):
                # cache hit for domain eky
                entry.touch()
                self._cache.move_to_end(domain_key)
                self._hits += 1
                self._domain_hits += 1

                logger.debug(
                    "domain cache hit for key: %s (size=%d, access_count=%d)",
                    domain_key,
                    entry.size,
                    entry.access_count,
                )
                return (entry.headers, entry.data)

            if entry is not None:
                logger.debug("domain cache entry expired for key: %s", domain_key)
                del self._cache[domain_key]
                self._current_memory -= entry.size

            # fallback
            if url_key != domain_key:
                entry = self._cache.get(url_key)
                if entry is not None:
                    if entry.is_expired(self._default_ttl):
                        logger.debug("url cache entry expired for key: %s", url_key)
                        del self._cache[url_key]
                        self._current_memory -= entry.size
                    else:
                        entry.touch()
                        self._cache.move_to_end(url_key)
                        self._hits += 1
                        self._url_hits += 1

                        logger.debug(
                            "url cache hit for key: %s (size=%d, access_count=%d)",
                            url_key,
                            entry.size,
                            entry.access_count,
                        )
                        return (entry.headers, entry.data)

            self._misses += 1
            return None

    def put(
        self,
        key: str,
        headers: dict,
        data: bytes,
        content_type: Optional[str] = None,
    ) -> bool:
        """Put a resource in the cache using domain-level key strategy."""
        data_size = len(data)

        if data_size > self._max_item_size_mb * 1024 * 1024:
            logger.debug(
                "skipping cache for large item: %s (size=%.1fMB)",
                key,
                data_size / 1024 / 1024,
            )
            return False

        if content_type and not self._should_cache_content_type(content_type):
            logger.debug("skipping cache for content type: %s", content_type)
            return False

        domain_key, url_key = generate_cache_keys(key)
        cache_key = domain_key  # use domain-level key

        with self._lock:
            now = time.time()
            entry = CacheEntry(
                data=data,
                headers=headers.copy(),
                size=data_size,
                created_at=now,
                last_accessed=now,
                access_count=0,
                content_type=content_type,
            )

            for existing_key in [cache_key, url_key]:
                if existing_key in self._cache:
                    old_entry = self._cache[existing_key]
                    self._current_memory -= old_entry.size
                    del self._cache[existing_key]

            self._cache[cache_key] = entry
            self._cache.move_to_end(cache_key)
            self._current_memory += data_size

            self._discard_old()

            logger.debug(
                "cached resource: %s (domain-level, size=%d, total_memory=%.1fMB)",
                cache_key,
                data_size,
                self._current_memory / 1024 / 1024,
            )
            return True

    def _should_cache_content_type(self, content_type: str) -> bool:
        """Determine if content type should be cached."""
        cacheable_types = {
            "text/plain",
            "text/html",
            "text/css",
            "text/javascript",
            "application/javascript",
            "application/json",
            "image/",
            "font/",
            "application/font",
        }
        return any(content_type.startswith(ct) for ct in cacheable_types)

    def _discard_old(self) -> None:
        max_memory_bytes = self._max_memory_mb * 1024 * 1024
        evicted_count = 0

        while (
            self._current_memory > max_memory_bytes
            or len(self._cache) > self._max_items
        ) and self._cache:
            _, entry = self._cache.popitem(last=False)
            self._current_memory -= entry.size
            evicted_count += 1

        if evicted_count > 0:
            logger.debug(
                f"evicted {evicted_count} cache entries "
                f"(memory={self._current_memory / 1024 / 1024:.1f}MB, "
                f"items={len(self._cache)})"
            )

    def cleanup_expired(self) -> int:
        """Remove expired entries and return count of removed items."""
        with self._lock:
            expired_keys = []

            for key, entry in self._cache.items():
                if entry.is_expired(self._default_ttl):
                    expired_keys.append(key)

            for key in expired_keys:
                entry = self._cache.pop(key)
                self._current_memory -= entry.size

            if expired_keys:
                logger.debug("cleaned up %d expired cache entries", len(expired_keys))

            return len(expired_keys)

    def get_stats(self) -> CacheStats:
        with self._lock:
            total_requests = self._hits + self._misses
            hit_rate = (
                (self._hits / total_requests * 100) if total_requests > 0 else 0.0
            )
            domain_hit_rate = (
                (self._domain_hits / self._hits * 100) if self._hits > 0 else 0.0
            )

            return CacheStats(
                hits=self._hits,
                misses=self._misses,
                domain_hits=self._domain_hits,
                url_hits=self._url_hits,
                size=len(self._cache),
                memory_usage=self._current_memory,
                hit_rate=hit_rate,
                domain_hit_rate=domain_hit_rate,
            )

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._current_memory = 0
            logger.info("cleared all cache entries")

    def remove(self, key: str) -> bool:
        with self._lock:
            if key in self._cache:
                entry = self._cache.pop(key)
                self._current_memory -= entry.size
                logger.debug("removed cache entry: %s", key)
                return True
            return False


class GalleryInfoCache(LRUCache[int, NhentaiGallery], Singleton):
    """Cache for gallery information to avoid re-parsing the same data."""

    def __init__(self):
        super().__init__(max_size=getattr(Config, "gallery_cache_size", 100))


class ThumbnailCache(LRUCache[str, bytes], Singleton):
    """Cache for thumbnail images to avoid re-fetching the same content."""

    def __init__(self):
        super().__init__(max_size=getattr(Config, "thumbnail_cache_size", 200))

    def read(self, key: Path | str) -> bytes:
        key = Path(key)
        if content := self.get(key.name):
            return content

        if not key.exists():
            raise FileNotFoundError(f"Path {key} does not exist.")
        if not key.is_file():
            raise ValueError(f"Path {key} is not a file.")

        content = key.read_bytes()
        self.put(key.name, content)
        return content
