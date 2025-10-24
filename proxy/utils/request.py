from __future__ import annotations

import json
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, override
from urllib.parse import urlparse

from cloudscraper import CloudScraper
from requests.utils import cookiejar_from_dict

from ..config import Config
from ..singleton import Singleton
from .cache import AutoDiscardBase, ResourceCache

__all__ = ("CFSession", "SessionStore")


class CFSession(CloudScraper):
    def __init__(self, session_id: str):
        super(CFSession, self).__init__(
            browser={
                # "browser": "firefox",
                # "platform": "windows",
                # "mobile": False,
                "custom": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
            },
            delay=10,
            debug=False,
            interpreter="js2py",
        )
        self.session_id = session_id
        self.resource_cache = ResourceCache()

        cookies_path = Path(Config.cache_path) / f"cookies_{self.session_id}.json"
        if cookies_path.exists():
            with cookies_path.open("r", encoding="utf-8") as f:
                cookies_dict = json.load(f)
            cookiejar_from_dict(cookies_dict, cookiejar=self.cookies, overwrite=True)

    def _clean_headers(self, url: str, headers: dict[str, Any]) -> None:
        """Remove headers that may cause issues with proxying."""
        # Remove headers that are not needed or may cause issues
        headers.pop("Host", None)
        headers.pop("User-Agent", None)
        headers.pop("Accept-Encoding", None)
        headers.pop("Content-Length", None)
        headers.pop("Content-Security-Policy", None)
        headers.pop("X-Content-Security-Policy", None)
        headers.pop("Remote-Addr", None)
        headers.pop("X-Forwarded-For", None)

        cookies = headers.pop("Cookie", None)
        if cookies:
            cookie = SimpleCookie(cookies)
            parse_url = urlparse(url)
            for key, morsel in cookie.items():
                if key not in self.cookies:
                    self.cookies.set(
                        key,
                        morsel.value,
                        domain=parse_url.netloc,
                        path=parse_url.path,
                    )

    def request(self, method, url, *args, **kwargs):
        if headers := kwargs.get("headers"):
            if isinstance(headers, dict):
                self._clean_headers(url, headers)

        return super().request(method, url, *args, **kwargs)


class SessionStoreAutoDiscard(AutoDiscardBase["SessionStore", Any]):
    def _get_value(self) -> "CFSession":
        return self._target.get(self._attr)

    def _set_value(self, value: Any | None, name: str | None = None) -> None:
        key = name or self._attr
        if value is None:
            if key in self._target:
                del self._target[key]

            if key in self._target._discard_trackers:
                del self._target._discard_trackers[key]
        else:
            self._target[key] = value


class SessionStore(Singleton, dict[str, "CFSession"]):
    _discard_trackers: dict[str, SessionStoreAutoDiscard]

    def __init__(self, discard_after: int = 1800):
        super().__init__()
        self._discard_after = discard_after
        self._discard_trackers = {}

    def _create_tracker(self, key: str):
        tracker = SessionStoreAutoDiscard(
            target=self, attr=key, threshold=self._discard_after
        )
        self._discard_trackers[key] = tracker

    @override
    def get(self, key: str) -> CFSession:  # type: ignore
        session_exists = key in self
        session = self.setdefault(key, CFSession(key))

        if not session_exists:
            self._create_tracker(key)
        else:
            if tracker := self._discard_trackers.get(key):
                tracker.get()
            else:
                self._create_tracker(key)
        return session

    @override
    def __delitem__(self, key: str):
        """Ensures the tracker is cleaned up when a session is manually deleted."""
        super().__delitem__(key)
        if key in self._discard_trackers:
            tracker = self._discard_trackers.pop(key)
            with SessionStoreAutoDiscard._lock:
                SessionStoreAutoDiscard._instances.discard(tracker)
