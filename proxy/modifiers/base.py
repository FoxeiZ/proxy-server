from __future__ import annotations

import re
from collections import OrderedDict
from typing import Callable
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from ..singleton import Singleton
from ..utils.logger import get_logger

__all__ = ("ModifyRule", "modify_html_content", "modify_js_content")


logger = get_logger(__name__)


class ModifyRule(Singleton):
    def __init__(self):
        super().__init__()
        self.html_modifiers: OrderedDict[str, Callable[[BeautifulSoup, str], None]] = (
            OrderedDict()
        )
        self.js_modifiers: OrderedDict[str, Callable[[str], str]] = OrderedDict()

    @classmethod
    def add_html_rule(cls, pattern: str):
        """Add a modification rule to the cache."""

        def wrapper(
            func: Callable[[BeautifulSoup, str], None],
        ) -> Callable[[BeautifulSoup, str], None]:
            instance = cls()
            instance.html_modifiers[pattern] = func
            logger.info(
                "Added HTML modification rule: %s -> %s", pattern, func.__name__
            )
            return func

        return wrapper

    @classmethod
    def add_js_rule(cls, pattern: str):
        """Add a JavaScript modification rule to the cache."""

        def wrapper(func: Callable[[str], str]) -> Callable[[str], str]:
            instance = cls()
            instance.js_modifiers[pattern] = func
            logger.info("Added JS modification rule: %s -> %s", pattern, func.__name__)
            return func

        return wrapper

    def modify_html(self, page_url: str, soup: BeautifulSoup, html_content: str) -> str:
        """Modify HTML content using registered rules."""
        for pattern, func in self.html_modifiers.items():
            if re.search(pattern, page_url):
                logger.info("Applying rule: %s", pattern)
                func(soup, html_content)
        return str(soup)

    def modify_js(self, page_url: str, html_content: str) -> str:
        """Modify JavaScript content using registered rules."""
        modified_content = html_content
        for pattern, func in self.js_modifiers.items():
            if re.search(pattern, page_url):
                logger.info("Applying JS rule: %s", pattern)
                modified_content = func(modified_content)
        return modified_content


def modify_html_content(
    request_url: str,
    page_url: str,
    html_content: str,
    base_url: str,
    proxy_base: str,
    *,
    proxy_images: bool = False,
) -> str:
    """Modify HTML content to inject custom elements and fix relative URLs"""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        tags_attr = [
            ("a", "href"),
            # ("img", "src"),
            # ("img", "data-src"),
            ("link", "href"),
            ("script", "src"),
            ("form", "action"),
        ]
        if proxy_images:
            tags_attr.append(("img", "src"))
            tags_attr.append(("img", "data-src"))

        for tag_name, attr_name in (("img", "src"), ("img", "data-src")):
            for tag in soup.find_all(tag_name, {attr_name: True}):
                if not isinstance(tag, Tag):
                    continue
                tag[attr_name] = ""

        for tag_name, attr_name in tags_attr:
            for tag in soup.find_all(tag_name, {attr_name: True}):
                if not isinstance(tag, Tag):
                    continue
                url = tag[attr_name]
                if (
                    not url
                    or not isinstance(url, str)
                    or url.startswith(
                        (
                            "javascript:",
                            "data:",
                            "mailto:",
                            "tel:",
                            "..",
                        )
                    )
                ):
                    continue

                if url.startswith(("http://", "https://")):
                    parts = urlparse(url)
                    tag[attr_name] = (
                        f"{proxy_base}p/{parts.netloc}/{parts.path.lstrip('/')}"
                        f"{parts.query and '?' + parts.query or ''}"
                        f"{parts.fragment and '#' + parts.fragment or ''}"
                    )
                elif not url.startswith("/"):
                    if tag_name == "a":
                        parts = urlparse(page_url)
                        path_segments = parts.path.lstrip("/").split("/")
                        if path_segments:
                            path_segments.pop()
                        tag[attr_name] = (
                            f"/p/{parts.netloc}/{'/'.join(path_segments)}/{url.lstrip('/')}"
                        )
                    else:
                        tag[attr_name] = f"{request_url.rstrip('/')}/{url.lstrip('/')}"
                else:
                    tag[attr_name] = f"/p/{base_url}/{url.lstrip('/')}"
                logger.info("Modified %s to: %s", url, tag[attr_name])

        return ModifyRule().modify_html(page_url, soup, html_content)

    except Exception as e:
        logger.error("Failed to parse HTML content: %s", e)
        return html_content


def modify_js_content(page_url: str, content: str) -> str:
    """Modify JavaScript content to inject custom elements and fix relative URLs"""
    try:
        return ModifyRule().modify_js(page_url, content)
    except Exception as e:
        logger.error("Failed to parse JS content: %s", e)
        return content
