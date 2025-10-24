from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Generic, Set, TypeVar, overload

from ..singleton import Singleton
from .logger import get_logger

if TYPE_CHECKING:
    from .._types.nhentai import NhentaiGallery  # noqa: F401

V = TypeVar("V")
K = TypeVar("K")

__all__ = (
    "GalleryInfoCache",
    "ResourceCache",
    "LRUCache",
    "ThumbnailCache",
    "AutoDiscardObject",
    "AutoDiscardDict",
)


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


class ResourceCache(LRUCache[str, tuple[dict, bytes]]):
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


T = TypeVar("T")
V = TypeVar("V")


class AutoDiscardBase(ABC, Generic[T, V]):
    """
    Abstract Base Class for auto-discarding attributes.
    """

    _instances: Set["AutoDiscardBase"] = set()
    _lock = threading.Lock()
    _thread: threading.Thread | None = None
    _sleep_time: int = 5
    _logger = get_logger("AutoDiscard")

    def __init__(
        self,
        target: T,
        attr: str,
        threshold: int = 600,
        also_discard: list[str] | None = None,
    ):
        self._target: T = target
        self._attr: str = attr
        self._threshold = threshold
        self._last_access = time.time()
        self._also_discard = also_discard

        with self._lock:
            self._instances.add(self)
            if not self._thread or not self._thread.is_alive():
                self._start_thread()

    @property
    def threshold(self) -> int:
        return self._threshold

    @property
    def last_access(self) -> float:
        return self._last_access

    def get(self) -> V | None:
        """
        Retrieves the value of the target attribute and updates the last access time.
        """
        with self._lock:
            if self not in self._instances:
                self._logger.debug(
                    "Instance %s/%d not found in AutoDiscard instances.",
                    self,
                    id(self),
                )
                self._instances.add(self)

        self._last_access = time.time()
        return self._get_value()

    def set(self, value: V) -> None:
        """
        Sets the value of the target attribute and updates the last access time.
        """
        self._last_access = time.time()
        self._set_value(value)

    def discard(self):
        """
        Discards the value of the target attribute by setting it to None.
        """
        if self._also_discard:
            for attr in self._also_discard:
                self._set_value(None, name=attr)

        self._set_value(None)

    @abstractmethod
    def _get_value(self) -> V | None:
        """
        Abstract method to get the value from the target.
        To be implemented by subclasses.
        """
        pass

    @overload
    def _set_value(self, value: None) -> None: ...

    @overload
    def _set_value(self, value: V) -> None: ...

    @overload
    def _set_value(self, value: None, name: str | None = None) -> None: ...

    @overload
    def _set_value(self, value: V, name: str | None = None) -> None: ...

    @abstractmethod
    def _set_value(self, value: V | None, name: str | None = None) -> None:
        """
        Abstract method to set the value on the target.
        To be implemented by subclasses.
        """
        pass

    @classmethod
    def _start_thread(cls) -> None:
        # maybe remove this check cuz we already do it in init, idk.
        # if cls._thread and cls._thread.is_alive():
        #     return

        def run():
            cls._logger.info("AutoDiscard thread started.")
            time.sleep(cls._sleep_time)
            while True:
                time.sleep(cls._sleep_time)
                total_instances = len(cls._instances)
                if total_instances == 0:
                    cls._logger.info("No instances to discard.")
                    continue

                total_discarded = 0
                now = time.time()
                with cls._lock:
                    for inst in list(cls._instances):
                        if (
                            inst._get_value() is not None
                            and now - inst.last_access > inst.threshold
                        ):
                            inst.discard()
                            cls._instances.discard(inst)
                            total_discarded += 1

                if total_discarded > 0:
                    cls._logger.info(
                        "Discarded %d/%d instances.", total_discarded, total_instances
                    )

        cls._thread = threading.Thread(
            target=run, daemon=True, name="AutoDiscardThread"
        )
        cls._thread.start()

    def __del__(self):
        self._logger.warning(
            "AutoDiscard instance %s/%d is being deleted.", self, id(self)
        )
        with self._lock:
            self._instances.discard(self)


class AutoDiscardObject(AutoDiscardBase[T, V]):
    """
    Auto-discards an attribute of a class-like object.
    """

    def _get_value(self) -> V | None:
        return getattr(self._target, self._attr, None)

    def _set_value(self, value: V | None, name: str | None = None) -> None:
        setattr(self._target, name or self._attr, value)


class AutoDiscardDict(AutoDiscardBase[Dict[str, Any], V]):
    """
    Auto-discards a key in a dictionary.
    """

    def _get_value(self) -> V | None:
        return self._target.get(self._attr, None)

    def _set_value(self, value: V | None, name: str | None = None) -> None:
        self._target[name or self._attr] = value
