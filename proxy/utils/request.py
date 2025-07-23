import json
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cloudscraper import CloudScraper
from requests.utils import cookiejar_from_dict

from ..config import Config
from ..singleton import Singleton

__all__ = ("Requests",)


class Requests(Singleton, CloudScraper):
    def __init__(self):
        super(Requests, self).__init__(
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
        cookies_path = Path(Config.cache_path) / "cookies.json"
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
