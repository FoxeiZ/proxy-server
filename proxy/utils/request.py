from typing import Any

from cloudscraper import CloudScraper

from ..singleton import Singleton

__all__ = ("Requests",)


class Requests(Singleton, CloudScraper):
    def __init__(self):
        super(Requests, self).__init__(
            browser={
                "browser": "chrome",
                "platform": "windows",
                "mobile": False,
            },
            delay=10,
            debug=False,
        )

    def _clean_headers(self, headers: dict[str, Any]) -> None:
        """Remove headers that may cause issues with proxying."""
        # Remove headers that are not needed or may cause issues
        headers.pop("Host", None)
        headers.pop("Accept-Encoding", None)
        headers.pop("Content-Length", None)

    def request(self, *args, **kwargs):
        if "headers" in kwargs:
            headers = kwargs["headers"]
            if isinstance(headers, dict):
                self._clean_headers(headers)

        return super().request(*args, **kwargs)
