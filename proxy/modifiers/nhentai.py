# TODO: Add a download manager for progress tracking and management
# TODO: Add float notification for download progress
from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, List, Optional, cast

from bs4 import BeautifulSoup, Tag

from ..downloader import DownloadPool
from ..enums import FileStatus
from ..utils import (
    GalleryInfoCache,
    check_file_status,
    check_file_status_gallery,
    clean_and_parse_title,
    clean_and_split,
    get_logger,
)
from .base import ModifyRule

if TYPE_CHECKING:
    from .._types.nhentai import NhentaiGallery, NhentaiGalleryData


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
JS_MOD = ""
with open(os.path.join(CURRENT_DIR, "n", "mod.js"), "r", encoding="utf-8") as f:
    JS_MOD = f.read()


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
        print(f"Error parsing tags from HTML: {e}")
        return []


def parse_chapter(html: str) -> Optional[NhentaiGallery]:
    """Parse HTML content to extract gallery information from JSON data and tag details from HTML."""
    pattern = re.compile(r"window\._gallery = JSON\.parse\(\"([^\"]+)\"\);")
    match = pattern.search(html)
    if not match:
        return None

    json_string = match.group(1)
    json_string = json_string.encode().decode("unicode_escape")
    gallery_data: NhentaiGalleryData = json.loads(json_string)

    # Extract and clean the title string
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
            artists.extend(clean_and_split(tag["name"]))
        elif tag["type"] == "parody":
            parodies.extend(clean_and_split(tag["name"]))
        elif tag["type"] == "language":
            if tag["name"] == "translated":
                translated = True
            language = tag["name"]
        elif tag["type"] == "category":
            category = tag["name"]
        elif tag["type"] == "group":
            writers.extend(clean_and_split(tag["name"]))
        elif tag["type"] == "character":
            characters.extend(clean_and_split(tag["name"]))
        else:
            logger.warning(f"Unknown tag type: {tag['type']} with name: {tag['name']}")

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
def modify_chapter(
    soup: BeautifulSoup, html_content: str, *, proxy_images: bool = False
) -> None:
    gallery_id_h3 = soup.find("h3", id="gallery_id")
    if not gallery_id_h3:
        logger.warning("No gallery ID found in the HTML content.")
        return
    gallery_id = gallery_id_h3.text.strip().lstrip("#")

    gallery_data = parse_chapter(html_content)
    if not gallery_data:
        logger.warning("No gallery data found in the HTML content.")
        return
    GalleryInfoCache().put(gallery_data["id"], gallery_data)

    btn_container = soup.find("div", class_="buttons")
    if not btn_container:
        logger.warning("No button container found in the HTML content.")
        return
    if not isinstance(btn_container, Tag):
        raise TypeError("Expected btn_container to be a BeautifulSoup Tag")

    soup.head.append(soup.new_tag("script", string=JS_MOD))  # type: ignore

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

    def create_add():
        file_status = check_file_status_gallery(gallery_info=gallery_data)
        pool = DownloadPool()
        is_downloading = pool.is_downloading(gallery_data["id"])

        # Determine button state based on file status and download status
        if file_status == FileStatus.CONVERTED:
            button_text = "Converted"
            button_class = "btn btn-primary btn-disabled"
            button_icon = "fa fa-check"
            is_disabled = True
        elif file_status == FileStatus.COMPLETED:
            button_text = "Downloaded"
            button_class = "btn btn-info btn-disabled"
            button_icon = "fa fa-check"
            is_disabled = True
        elif is_downloading:
            button_text = "Downloading..."
            button_class = "btn btn-primary btn-disabled"
            button_icon = "fa fa-spinner fa-spin"
            is_disabled = True
        else:
            button_text = "Add"
            button_class = "btn btn-primary"
            button_icon = "fa fa-plus"
            is_disabled = False

        _a = soup.new_tag(
            "a",
            attrs={
                "class": button_class,
                "id": "add",
                "style": "min-width: unset; padding: 0 0.75rem",
                **(
                    {
                        "href": "#",
                        "onclick": f"addGallery(event, {gallery_id});",
                    }
                    if not is_disabled
                    else {}
                ),
            },
        )
        _a.string = f"{button_text} "

        _i = soup.new_tag(
            "i",
            attrs={"class": button_icon},
        )
        _a.append(_i)
        return _a

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
    btn_container.append(create_add())
    btn_container.append(create_download())
    btn_container.append(soup.new_tag("br"))
    btn_container.append(create_image_proxy())
    logger.info("Modified button to download gallery.")


@ModifyRule.add_html_rule(r"nhentai\.net")
def modify_gallery(soup: BeautifulSoup, *args, **kwargs) -> None:
    logger.info("Modifying gallery page content")

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

        file_status = check_file_status(
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


@ModifyRule.add_js_rule(r"nhentai\.net/static/js/scripts.*\.js")
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
