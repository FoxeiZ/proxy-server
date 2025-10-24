from __future__ import annotations

import re
from collections import OrderedDict
from typing import Callable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from ..errors import NeedToHandle
from ..singleton import Singleton
from ..utils.logger import get_logger

__all__ = ("ModifyRule", "modify_html_content", "modify_js_content")


logger = get_logger(__name__)


class ModifyRule(Singleton):
    def __init__(self):
        super().__init__()
        self.html_modifiers: OrderedDict[
            str,
            Callable[
                [BeautifulSoup, str],
                None,
            ],
        ] = OrderedDict()
        self.js_modifiers: OrderedDict[str, Callable[[str], str]] = OrderedDict()

    @classmethod
    def add_html_rule(cls, pattern: str):
        """Add a HTML modification rule."""

        def wrapper(
            func: Callable[[BeautifulSoup, str], None],
        ) -> Callable[[BeautifulSoup, str], None]:
            instance = cls()
            if not isinstance(func, Callable):
                raise TypeError("func must be a callable")
            if pattern in instance.html_modifiers:
                raise ValueError(
                    f"HTML modification rule for pattern '{pattern}' already exists"
                )
            _ = re.compile(pattern)  # Validate the pattern

            instance.html_modifiers[pattern] = func
            logger.info(
                "Added HTML modification rule: %s -> %s", pattern, func.__name__
            )
            return func

        return wrapper

    @classmethod
    def add_js_rule(cls, pattern: str):
        """Add a JavaScript modification rule."""

        def wrapper(func: Callable[[str], str]) -> Callable[[str], str]:
            instance = cls()
            instance.js_modifiers[pattern] = func
            logger.info("Added JS modification rule: %s -> %s", pattern, func.__name__)
            return func

        return wrapper

    def _proxy_image_toggle_html(
        self, soup: BeautifulSoup, is_proxy_images: bool
    ) -> None:
        """Toggle for proxy_image request."""
        body = soup.find("body")
        if not body or not isinstance(body, Tag):
            return

        button = soup.new_tag("button")
        button.string = "Toggle Proxy Images"
        button["style"] = "position: fixed; bottom: 0; right: 0;"
        button[
            "onclick"
        ] = """window.location.href = window.location.href.includes('proxy_images=1')
                    ? window.location.href.replace('proxy_images=1', '')
                    : window.location.href + (window.location.href.includes('?') ? '&' : '?') + 'proxy_images=1';"""

        body.append(button)

    def modify_html(
        self,
        page_url: str,
        soup: BeautifulSoup,
        html_content: str,
        is_proxy_images: bool,
    ) -> str:
        """Modify HTML content using registered rules."""
        self._proxy_image_toggle_html(soup, is_proxy_images)

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
    page_url: str,
    html_content: str,
    *,
    is_proxy_images: bool = False,
) -> str:
    try:
        soup = BeautifulSoup(html_content, "html.parser")

        TAGS_TO_MODIFY = {
            "a": ["href"],
            "img": ["src", "data-src"],
            "link": ["href"],
            "script": ["src"],
            "form": ["action"],
        }

        URL_PREFIXES_TO_SKIP = (
            "#",
            "magnet:",
            "javascript:",
            "data:",
            "mailto:",
            "tel:",
        )

        for tag in soup.find_all(TAGS_TO_MODIFY.keys()):
            if not isinstance(tag, Tag):
                raise ValueError("Unexpected tag type")

            attributes = TAGS_TO_MODIFY.get(tag.name, [])
            for attr_name in attributes:
                if not tag.has_attr(attr_name):
                    continue

                original_url = tag.get(attr_name)
                if not isinstance(original_url, str) or not original_url.strip():
                    continue
                if original_url.startswith(URL_PREFIXES_TO_SKIP):
                    continue

                absolute_url = urljoin(page_url, original_url)

                # not proxied if is_proxy_images is False.
                is_image_tag = tag.name == "img"
                should_proxy = not (is_image_tag and not is_proxy_images)

                if should_proxy:
                    # make a root-relative proxied URL, e.g., /p/example.com/path/to/something
                    url_parts = urlparse(absolute_url)
                    proxied_url = f"/p/{url_parts.netloc}{url_parts.path}"
                    if url_parts.query:
                        proxied_url += f"?{url_parts.query}"
                    if url_parts.fragment:
                        proxied_url += f"#{url_parts.fragment}"

                    tag[attr_name] = proxied_url
                else:
                    # use absolute URL to avoid breaking stuff
                    tag[attr_name] = absolute_url

                logger.info("Modified %s to: %s", original_url, tag[attr_name])

        return ModifyRule().modify_html(page_url, soup, html_content, is_proxy_images)

    except NeedToHandle as e:
        raise e from None

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
