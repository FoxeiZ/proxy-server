from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, List, Optional, cast

from bs4 import BeautifulSoup, Tag
from quart import url_for

from ..downloader import DownloadPool
from ..enums import FileStatus
from ..errors import NeedCSRF
from ..utils import (
    GalleryInfoCache,
    check_file_status,
    check_file_status_gallery,
    clean_and_parse_title,
    get_logger,
    split_and_clean,
)
from .base import ModifyRule

if TYPE_CHECKING:
    from typing import AsyncGenerator

    from .._types.nhentai import NhentaiGallery, NhentaiGalleryData


logger = get_logger(__name__)


def parse_tags_from_html(html: str) -> List[str]:
    """Parse tag names from HTML content, extracting tag names from class attributes."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        tags = []
        tag_links = soup.find_all("a", class_=re.compile(r"tag tag-\d+"))

        for tag_link in tag_links:
            if not isinstance(tag_link, Tag):
                continue

            name_span = tag_link.find("span", class_="name")
            if isinstance(name_span, Tag):
                tag_name = name_span.get_text(strip=True)
                if tag_name:
                    tags.append(tag_name)

        return tags

    except Exception as e:
        logger.error("error parsing tags from HTML: %s", e)
        return []


def parse_chapter(html: str) -> Optional[NhentaiGallery]:
    """Parse HTML content to extract gallery information from JSON data and tag details from HTML."""
    pattern = re.compile(r"window\._gallery = JSON\.parse\(\"([^\"]+)\"\);")
    match = pattern.search(html)
    if not match:
        logger.debug("no gallery JSON data found in HTML content")
        return None

    json_string = match.group(1)
    try:
        json_string = json_string.encode().decode("unicode_escape")
        gallery_data: NhentaiGalleryData = json.loads(json_string)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error("failed to parse gallery JSON data: %s", e)
        return None

    # extract and clean the title string
    parsed_cleaned_title = clean_and_parse_title(
        gallery_data["title"].get("english", "")
        or gallery_data["title"].get("japanese", "")
        or gallery_data["title"].get("pretty", "Unknown Title")
    )

    # Process tags
    original_tags = gallery_data.get("tags", []).copy()
    tags = []
    artists = []
    writers = []
    parodies = []
    characters = []
    language = "english"  # default
    category = "manga"  # default
    translated = False

    for tag in original_tags:
        if tag["type"] == "tag":
            tags.append(tag["name"])
        elif tag["type"] == "artist":
            artists.extend(split_and_clean(tag["name"]))
        elif tag["type"] == "parody":
            parodies.extend(split_and_clean(tag["name"]))
        elif tag["type"] == "language":
            if tag["name"] == "translated":
                translated = True
                continue
            language = tag["name"]
        elif tag["type"] == "category":
            category = tag["name"]
        elif tag["type"] == "group":
            writers.extend(split_and_clean(tag["name"]))
        elif tag["type"] == "character":
            characters.extend(split_and_clean(tag["name"]))
        else:
            logger.warning(
                "unknown tag type: %s with name: %s", tag["type"], tag["name"]
            )

    processed_gallery: NhentaiGallery = {
        "id": gallery_data["id"],
        "title": parsed_cleaned_title,
        "language": language,
        "category": category,
        "tags": tags,
        "artists": artists,
        "writers": writers,
        "parodies": parodies,
        "characters": characters,
        "images": gallery_data["images"],
        "media_id": gallery_data["media_id"],
        "scanlator": gallery_data.get("scanlator", ""),
        "upload_date": gallery_data["upload_date"],
        "num_pages": gallery_data["num_pages"],
        "num_favorites": gallery_data["num_favorites"],
        "page_count": gallery_data["num_pages"],
        "translated": translated,
    }

    return processed_gallery


@ModifyRule.add_html_rule(r"/g/\d+")
async def modify_chapter(
    soup: BeautifulSoup, html_content: str, *, proxy_images: bool = False
) -> None:
    """Modify nhentai chapter pages to add download functionality."""
    gallery_id_element = soup.find("h3", id="gallery_id")
    if not gallery_id_element:
        logger.warning("no gallery ID found in the HTML content")
        return
    gallery_id = gallery_id_element.text.strip().lstrip("#")

    gallery_data = parse_chapter(html_content)
    if not gallery_data:
        logger.warning("no gallery data found in the HTML content")
        return
    GalleryInfoCache().put(gallery_data["id"], gallery_data)

    btn_container = soup.find("div", class_="buttons")
    if not btn_container:
        logger.warning("No button container found in the HTML content.")
        return
    if not isinstance(btn_container, Tag):
        raise TypeError("Expected btn_container to be a BeautifulSoup Tag")

    soup.head.append(  # type: ignore
        soup.new_tag("script", src=url_for("static", filename="nhentai/mod.js"))
    )

    def create_download():
        _a = soup.new_tag(
            "a",
            attrs={
                "class": "btn btn-secondary",
                "id": "download",
                "href": f"/download/{gallery_id}",
            },
        )
        _a.string = "Download "

        _i = soup.new_tag(
            "i",
            attrs={"class": "fa fa-download"},
        )
        _a.append(_i)
        return _a

    def _create_a(attrs, button_text, button_icon, hint_text=None) -> Tag:
        _a = soup.new_tag("a", attrs=attrs)
        _a.string = f"{button_text} "

        _i = soup.new_tag(
            "i",
            attrs={"class": button_icon},
        )
        _a.append(_i)

        if hint_text:
            _top = soup.new_tag(
                "div",
                attrs={"class": "top"},
            )
            _top.append(soup.new_tag("i"))
            _top.string = hint_text
            _a.append(_top)
        return _a

    async def create_add() -> AsyncGenerator[Tag, None]:
        file_status = await check_file_status_gallery(gallery_info=gallery_data)
        pool = DownloadPool()
        is_downloading = await pool.is_downloading(gallery_data["id"])

        hint_text = ""
        attrs = {
            "id": "add",
            "style": "min-width: unset; padding: 0 0.75rem",
            "href": "#",
            "class": "btn btn-primary tooltip",
        }
        if file_status == FileStatus.CONVERTED:
            button_text = "Converted"
            button_icon = "fa fa-check"
            hint_text = "Go to the converted gallery"
            attrs["href"] = f"/galleries/chapter/{gallery_id}"
            attrs["rel"] = "noreferrer"
        elif file_status == FileStatus.COMPLETED:
            button_text = "Downloaded"
            button_icon = "fa fa-check"
            hint_text = "Click to convert to CBZ"
            attrs["class"] = "btn btn-info tooltip"
            attrs["onclick"] = f"addGallery(event, {gallery_id});"
        elif is_downloading:
            button_text = "Downloading..."
            attrs["class"] = "btn btn-primary btn-disabled"
            button_icon = "fa fa-spinner fa-spin"
        else:
            if file_status == FileStatus.IN_DIFF_LANG:
                yield _create_a(
                    attrs,
                    "In Different Language",
                    "fa fa-info-circle",
                    "Already in library in different language",
                )
            elif file_status == FileStatus.AVAILABLE:
                yield _create_a(
                    attrs,
                    "Available",
                    "fa fa-info-circle",
                    "Available in the same language in library",
                )
            elif file_status == FileStatus.MAYBE_AVALIABLE:
                yield _create_a(
                    attrs,
                    "Maybe Available",
                    "fa fa-info-circle",
                    "Might be available in library",
                )

            button_text = "Add"
            hint_text = "Click to add to download queue"
            button_icon = "fa fa-plus"
            attrs["onclick"] = f"addGallery(event, {gallery_id});"

        yield _create_a(attrs, button_text, button_icon, hint_text)

    def create_image_proxy():
        _a = soup.new_tag(
            "a",
            attrs={
                "class": "btn btn-secondary",
                "id": "image-proxy",
                "href": f"/p/nhentai.net/g/{gallery_id}?proxy_images={'0' if not proxy_images else '1'}",
            },
        )
        _a.string = "Image Proxy "

        _i = soup.new_tag(
            "i",
            attrs={"class": "fa fa-image"},
        )
        _a.append(_i)
        return _a

    btn_container.clear()
    btn_container.extend([_ async for _ in create_add()])
    btn_container.append(create_download())
    btn_container.append(soup.new_tag("br"))
    # btn_container.append(create_image_proxy())
    logger.info("Modified button to download gallery.")


def modify_cf_chl(soup: BeautifulSoup) -> bool:
    _title = soup.find("title")
    if not _title or not isinstance(_title, Tag):
        return False
    if _title.string == "Just a moment...":
        return True
    return False


@ModifyRule.add_html_rule(r"nhentai\.net")
async def modify_gallery(soup: BeautifulSoup, *args, **kwargs) -> None:
    logger.info("Modifying gallery page content")
    if modify_cf_chl(soup):
        raise NeedCSRF(
            "Cloudflare challenge detected, CSRF token is required to proceed."
        )

    remove_ads(soup)

    for gallery_div in soup.find_all("div", class_="gallery"):
        if not isinstance(gallery_div, Tag):
            continue

        a = gallery_div.find("a", class_="cover")
        caption = gallery_div.find("div", class_="caption")
        tags_id = gallery_div.get("data-tags", "")
        if not tags_id:
            logger.warning("No tags found in the gallery div.")
            continue
        if "6346" in tags_id:
            language = "japanese"
        elif "29963" in tags_id:
            language = "chinese"
        else:
            language = "english"

        if (not a or not isinstance(a, Tag)) or (
            not caption or not isinstance(caption, Tag)
        ):
            continue

        gallery_id = cast(str, a.get("href") or "").rstrip("/").split("/")[-1]
        gallery_title = clean_and_parse_title(caption.get_text(strip=True))
        if not gallery_id.isdigit():
            logger.warning("Invalid gallery ID found in the HTML content.")
            continue

        file_status = await check_file_status(
            gallery_id=int(gallery_id),
            gallery_title=gallery_title,
            gallery_language=language,
        )
        if file_status == FileStatus.NOT_FOUND:
            logger.warning(
                "Gallery %s ID %s not found in the filesystem.",
                gallery_title["main_title"],
                gallery_id,
            )
            continue

        a.img["style"] = "opacity: 0.7;"  # type: ignore
        _div = soup.new_tag(
            "div",
            attrs={
                "class": "btn btn-secondary",
                "style": "position: absolute; display: block; pointer-events: none;",
            },
        )
        if file_status == FileStatus.CONVERTED:
            _div.string = "Converted"
        elif file_status == FileStatus.COMPLETED:
            _div.string = "Downloaded"
        elif file_status == FileStatus.IN_DIFF_LANG:
            _div.string = "In different language"
            _div["style"] += "color: yellow;"
        elif file_status == FileStatus.AVAILABLE:
            _div.string = "Available"
            _div["style"] += "color: greenyellow;"
        elif file_status == FileStatus.MAYBE_AVALIABLE:
            _div.string = "Maybe available"
            _div["style"] += "color: orange;"
        elif file_status == FileStatus.MISSING:
            _div.string = "Partial | In library"
        a.append(_div)


def remove_ads(soup: BeautifulSoup) -> None:
    """Remove ads from the HTML content."""
    for ad_div in soup.find_all("section", class_="advertisement"):
        if not isinstance(ad_div, Tag):
            continue
        ad_div.decompose()
        logger.info("Removed advertisement section from the HTML content.")

    for script in soup.find_all("script"):
        if not isinstance(script, Tag):
            continue
        if script.string and "show_popunders: true" in script.string:
            script.string = script.string.replace(
                "show_popunders: true", "show_popunders: false"
            )
            logger.info("Disabled popunders in the script content.")
            break


def remove_tsyndicate_sdk(content: str) -> str:
    """Remove tsyndicate since its a ad script"""
    try:
        # Remove the specific SDK script
        return re.sub(
            r"https://cdn\.tsyndicate\.com/sdk/v1/[a-zA-Z\.]+\.js", "", content
        )
    except Exception as e:
        logger.error("Failed to remove SDK script from JS content: %s", e)
        return content


def replace_route(content: str):
    return content.replace(
        'p.route("/g/<int:id>/<int:page>/"',
        'p.route("/p/nhentai.net/g/<int:id>/<int:page>/"',
    )


@ModifyRule.add_js_rule(r"nhentai\.net/static/js/scripts.*\.js")
def modify_gallery_js(js_content: str, *args, **kwargs) -> str:
    logger.info("Modifying gallery JS content")
    js_content = remove_tsyndicate_sdk(js_content)
    js_content = replace_route(js_content)
    return js_content
