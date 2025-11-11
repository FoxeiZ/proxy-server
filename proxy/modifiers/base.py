from __future__ import annotations

import inspect
import re
from collections import OrderedDict
from typing import Callable, Coroutine, Protocol
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag
from quart import url_for

from ..errors import NeedToHandle
from ..singleton import Singleton
from ..utils.logger import get_logger

__all__ = (
    "ModifyRule",
    "modify_html_content",
    "modify_js_content",
)


logger = get_logger(__name__)


class HtmlModifierProtocol(Protocol):
    def __call__(
        self,
        soup: BeautifulSoup,
        html_content: str,
        /,
        *,
        proxy_images: bool = False,
    ) -> None | Coroutine[None, None, None]: ...


class ModifyRule(Singleton):
    def __init__(self):
        super().__init__()
        self.html_modifiers: OrderedDict[
            str,
            HtmlModifierProtocol,
        ] = OrderedDict()
        self.js_modifiers: OrderedDict[str, Callable[[str], str]] = OrderedDict()

    @classmethod
    def add_html_rule(cls, pattern: str):
        """Add a HTML modification rule."""

        def wrapper(
            func: HtmlModifierProtocol,
        ) -> HtmlModifierProtocol:
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
                "Added HTML modification rule: %s -> %s",
                pattern,
                getattr(func, "__name__", repr(func)),
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

    def _proxy_image_toggle_html(self, soup: BeautifulSoup) -> None:
        """Toggle for proxy_image request."""
        body = soup.find("body")
        if not body or not isinstance(body, Tag):
            return

        button = soup.new_tag("button")
        button.string = "Toggle Proxy Images"
        button["style"] = "position: fixed; bottom: 0; right: 0;"
        button["onclick"] = (
            """window.location.href = window.location.href + (window.location.href.includes('?') ? '&' : '?') + 'proxy_images=toggle';"""
        )

        body.append(button)

    def _inject_dom_observer(self, soup: BeautifulSoup) -> None:
        """Inject DOM observer script for dynamic content handling."""
        # always inject DOM observer to handle lazy loading, regardless of proxy_images setting
        existing_scripts = soup.find_all("script", src=True)
        has_observer = False
        for script in existing_scripts:
            if isinstance(script, Tag):
                src_attr = script.get("src")
                if isinstance(src_attr, str) and "proxy-dom-observer.js" in src_attr:
                    has_observer = True
                    break

        if not has_observer:
            observer_script = soup.new_tag(
                "script", src=url_for("static", filename="base/proxy-dom-observer.js")
            )
            # ensure head element exists before appending
            if soup.head is not None:
                soup.head.append(observer_script)  # type: ignore
            else:
                logger.warning(
                    "no <head> element found, cannot inject DOM observer script"
                )

    async def modify_html(
        self,
        page_url: str,
        soup: BeautifulSoup,
        html_content: str,
        is_proxy_images: bool,
    ) -> str:
        """Modify HTML content using registered rules."""
        try:
            self._proxy_image_toggle_html(soup)
            self._inject_dom_observer(soup)

            for pattern, func in self.html_modifiers.items():
                if re.search(pattern, page_url):
                    logger.info("applying rule: %s", pattern)
                    try:
                        result = func(soup, html_content, proxy_images=is_proxy_images)
                        if inspect.iscoroutine(result):
                            await result
                        elif callable(func) and not inspect.iscoroutinefunction(func):
                            pass
                    except TypeError:
                        result = func(soup, html_content)
                        if inspect.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error("error applying rule %s: %s", pattern, e)
        except Exception as e:
            logger.error("error modifying HTML content: %s", e)
        return str(soup)

    def modify_js(self, page_url: str, html_content: str) -> str:
        """Modify JavaScript content using registered rules."""
        modified_content = html_content
        for pattern, func in self.js_modifiers.items():
            if re.search(pattern, page_url):
                logger.info("Applying JS rule: %s", pattern)
                modified_content = func(modified_content)
        return modified_content


async def modify_html_content(
    request_url: str,
    page_url: str,
    html_content: str,
    base_url: str,
    proxy_base: str,
    *,
    is_proxy_images: bool = False,
) -> str:
    """Modify HTML content to inject custom elements and fix relative URLs"""
    page_url_parts = urlparse(page_url)

    try:
        soup = BeautifulSoup(html_content, "html.parser")
        tags_attr = [
            ("a", "href"),
            ("img", "src"),
            ("link", "href"),
            ("script", "src"),
            ("form", "action"),
        ]
        # if is_proxy_images:
        #     tags_attr.append(("img", "src"))
        #     tags_attr.append(("img", "data-src"))

        for tag_name, attr_name in tags_attr:
            for tag in soup.find_all(tag_name, {attr_name: True}):
                if not isinstance(tag, Tag):
                    continue

                url = tag.get(attr_name)
                if not url or not isinstance(url, str):
                    continue

                # skip certain URL types
                if url.startswith(
                    (
                        "javascript:",
                        "data:",
                        "mailto:",
                        "tel:",
                        "..",
                    )
                ):
                    continue

                try:
                    url_parts = urlparse(url)
                except Exception as e:
                    logger.debug("failed to parse URL %s: %s", url, e)
                    continue

                if url.startswith(("http://", "https://")):
                    if tag_name == "img" and not is_proxy_images:
                        continue

                    tag[attr_name] = (
                        f"{proxy_base}p/{url_parts.netloc}/{url_parts.path.lstrip('/')}"
                        f"{'?' + url_parts.query if url_parts.query else ''}"
                        f"{'#' + url_parts.fragment if url_parts.fragment else ''}"
                    )

                elif url.startswith("//"):
                    if tag_name == "img" and not is_proxy_images:
                        tag[attr_name] = f"{page_url_parts.scheme}:{url}"
                    else:
                        tag[attr_name] = f"/p/{url.lstrip('/')}"

                elif not url.startswith("/"):
                    if tag_name == "a":
                        path_segments = page_url_parts.path.lstrip("/").split("/")
                        if path_segments:
                            path_segments.pop()
                        tag[attr_name] = (
                            f"/p/{page_url_parts.netloc}/{'/'.join(path_segments)}/{url.lstrip('/')}"
                        )
                    elif tag_name == "img" and not is_proxy_images:
                        tag[attr_name] = (
                            f"{page_url_parts.scheme}://{page_url_parts.netloc}{url}"
                        )
                    else:
                        tag[attr_name] = f"{request_url.rstrip('/')}/{url.lstrip('/')}"

                else:
                    if tag_name == "img" and not is_proxy_images:
                        tag[attr_name] = (
                            f"{page_url_parts.scheme}://{page_url_parts.netloc}{url}"
                        )
                    else:
                        tag[attr_name] = f"/p/{base_url}/{url.lstrip('/')}"

                logger.debug("modified %s to: %s", url, tag[attr_name])

        if soup.head:
            meta_tag = soup.new_tag(
                "meta",
                attrs={
                    "name": "x-proxy-image",
                    "content": "1" if is_proxy_images else "0",
                },
            )
            soup.head.insert(0, meta_tag)
        else:
            logger.warning(
                "no <head> element found, cannot inject proxy image meta tag"
            )
        return await ModifyRule().modify_html(
            page_url, soup, html_content, is_proxy_images
        )

    except NeedToHandle as e:
        raise e from None

    except Exception as e:
        logger.error("failed to parse HTML content: %s", e)
        return html_content


def modify_js_content(page_url: str, content: str) -> str:
    """Modify JavaScript content to inject custom elements and fix relative URLs"""
    try:
        return ModifyRule().modify_js(page_url, content)
    except Exception as e:
        logger.error("failed to parse JS content: %s", e)
        return content
