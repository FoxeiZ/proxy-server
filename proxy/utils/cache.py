from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from ..singleton import Singleton

if TYPE_CHECKING:
    from .._types.nhentai import NhentaiGallery  # noqa: F401

V = TypeVar("V")
K = TypeVar("K")

__all__ = ("GalleryInfoCache", "ResourceCache", "LRUCache")


class LRUCache(OrderedDict[K, V]):
    def __init__(self, max_size: int):
        super().__init__()
        self.max_size = max_size

    def get(self, key: K) -> V | None:  # type: ignore
        if key not in self:
            return None
        else:
            self.move_to_end(key)
            return self[key]

    def put(self, key: K, value: V) -> None:
        self[key] = value
        self.move_to_end(key)
        if len(self) > self.max_size:
            self.popitem(last=False)

    def remove(self, key: K) -> None:
        if key in self:
            del self[key]


class GalleryInfoCache(LRUCache[int, "NhentaiGallery"], Singleton):
    """Cache for BeautifulSoup objects to avoid re-parsing the same HTML content."""

    def __init__(self):
        super().__init__(max_size=10)


class ResourceCache(LRUCache[str, tuple[dict, bytes]], Singleton):
    """Cache for resources to avoid re-fetching the same content."""

    def __init__(self):
        super().__init__(max_size=100)


class ThumbnailCache(LRUCache[str, bytes], Singleton):
    """Cache for thumbnail images to avoid re-fetching the same content."""

    def __init__(self):
        super().__init__(max_size=50)

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
