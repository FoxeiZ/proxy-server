from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, TypedDict

if TYPE_CHECKING:
    from typing import IO

    from .._types.nhentai import NhentaiGallery


class XMLElement(TypedDict):
    """Represents an XML element with its tag, text, and attributes."""

    tag: str
    text: str | int | None
    attributes: dict[str, str] | None


class XMLWriter:
    def __init__(self):
        self.root_tag: str = "Root"
        self.root_attributes: dict = {}
        self.elements: list[XMLElement] = []

    def create_root(self, tag_name: str, attributes: dict | None = None):
        """Create a new root element"""
        self.root_tag = tag_name
        self.root_attributes = attributes or {}
        return self

    def add_element(
        self,
        tag_name: str,
        text: str | int | None = None,
        attributes: dict | None = None,
    ):
        """Add a child element"""
        element: XMLElement = {
            "tag": tag_name,
            "text": text or "",
            "attributes": attributes or {},
        }
        self.elements.append(element)
        return self

    def to_string(self, pretty_print: bool = False, *, indent: int = 2) -> str:
        """Convert XML to string"""
        if not hasattr(self, "root_tag"):
            return ""

        def _build_attrs(attrs_dict):
            """Helper to build attribute string"""
            if not attrs_dict:
                return ""
            return " " + " ".join(f'{k}="{v}"' for k, v in attrs_dict.items())

        def _build_element(element, indent=""):
            """Helper to build element string"""
            attrs = _build_attrs(element["attributes"])
            if element["text"]:
                return f"{indent}<{element['tag']}{attrs}>{element['text']}</{element['tag']}>"
            else:
                return f"{indent}<{element['tag']}{attrs}/>"

        # Build XML
        header = '<?xml version="1.0" encoding="utf-8"?>'
        root_attrs = _build_attrs(self.root_attributes)

        if pretty_print:
            lines = [header, f"<{self.root_tag}{root_attrs}>"]
            lines.extend(
                _build_element(element, " " * indent) for element in self.elements
            )
            lines.append(f"</{self.root_tag}>")
            return "\n".join(lines)
        else:
            elements = "".join(_build_element(element) for element in self.elements)
            return f"{header}<{self.root_tag}{root_attrs}>{elements}</{self.root_tag}>"

    def from_gallery_info(self, gallery_info: NhentaiGallery):
        self.create_root(
            "ComicInfo",
            {
                "xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
                "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            },
        )
        self.add_element("Title", gallery_info["title"]["chapter_title"])
        self.add_element(
            "Series",
            gallery_info["title"]["english_title"]
            or gallery_info["title"]["main_title"],
        )
        self.add_element("Number", gallery_info["title"]["chapter_number"])
        self.add_element(
            "LanguageISO",
            (
                "ja"
                if gallery_info["language"] == "japanese"
                else "zh"
                if gallery_info["language"] == "chinese"
                else "en"
            ),
        )
        self.add_element("PageCount", gallery_info["page_count"])
        self.add_element("Penciller", ", ".join(gallery_info["artists"]))
        self.add_element("Writer", ", ".join(gallery_info["writers"]))
        self.add_element("Translator", gallery_info["scanlator"])
        self.add_element("Tags", ", ".join(gallery_info["tags"]))
        self.add_element("SeriesGroup", ", ".join(gallery_info["parodies"]))
        self.add_element("Genre", ", ".join(gallery_info["characters"]))
        self.add_element("Characters", ", ".join(gallery_info["characters"]))
        self.add_element("Web", f"https://nhentai.net/g/{gallery_info['id']}")
        self.add_element("Translated", "Yes" if gallery_info["translated"] else "No")
        self.add_element(
            "BlackAndWhite", "No" if "full color" in gallery_info["tags"] else "Yes"
        )


class XMLIOWriter(XMLWriter):
    def write_to_file(self, file: IO[bytes], pretty_print: bool = False):
        """Write XML to a file-like object"""
        xml_string = self.to_string(pretty_print=pretty_print)
        file.write(xml_string.encode("utf-8"))

    def save(self, file_path: str, pretty_print: bool = False):
        """Save XML to a file"""
        with open(file_path, "wb") as file:
            self.write_to_file(file, pretty_print=pretty_print)


class XMLReader:
    def __init__(self):
        self.root: ET.Element[str] | None = None
        self.root_tag: None | str = None
        self.root_attributes = {}
        self.elements: list[XMLElement] = []

    @classmethod
    def from_string(cls, xml_string: str):
        self = cls()

        self.root = ET.fromstring(xml_string)
        self.root_tag = self._strip_namespace(self.root.tag)
        self.root_attributes = self.root.attrib
        self.elements = []

        for child in self.root:
            tag = self._strip_namespace(child.tag)
            self.elements.append(
                {
                    "tag": tag,
                    "text": child.text.strip() if child.text else "",
                    "attributes": child.attrib,
                }
            )

        return self

    @classmethod
    def parse_file(cls, file_path: str | Path):
        """Parse XML content from a file."""
        self = cls()
        path = Path(file_path)
        tree = ET.parse(path)
        self.root = tree.getroot()
        self.root_tag = self._strip_namespace(self.root.tag)
        self.root_attributes = self.root.attrib
        self.elements = []

        for child in self.root:
            tag = self._strip_namespace(child.tag)
            self.elements.append(
                {
                    "tag": tag,
                    "text": child.text.strip() if child.text else "",
                    "attributes": child.attrib,
                }
            )

        return self

    def _strip_namespace(self, tag: str) -> str:
        """Remove namespace from tag if present."""
        if "}" in tag:
            return tag.split("}")[1]
        return tag

    def get_element(self, tag_name: str) -> XMLElement | None:
        """Get an element by tag name."""
        for element in self.elements:
            if element["tag"] == tag_name:
                return element
        return None

    def get_element_text(self, tag_name: str) -> str | None:
        """Get text content of an element."""
        element = self.get_element(tag_name)
        return str(element["text"]) if element else None

    def get_element_int(self, tag_name: str) -> int | None:
        """Get text content of an element as integer."""
        text = self.get_element_text(tag_name)
        if text and text.isdigit():
            return int(text)
        return None

    def get_element_list(self, tag_name: str, separator: str = ",") -> list[str]:
        """Get text content of an element as a list of strings."""
        text = self.get_element_text(tag_name)
        if not text:
            return []
        return [item.strip() for item in text.split(separator) if item.strip()]

    def dump(self) -> Dict[str, Any]:
        """Convert parsed XML to a dictionary."""
        result = {
            "root_tag": self.root_tag,
            "root_attributes": self.root_attributes,
            "elements": {},
        }

        for element in self.elements:
            result["elements"][element["tag"]] = {
                "text": element["text"],
                "attributes": element["attributes"],
            }

        return result


class ComicInfoDict(TypedDict, total=False):
    """Type definition for ComicInfo XML data."""

    title: str | None
    series: str | None
    number: int | None
    language: str | None
    page_count: int | None
    penciller: str | None
    writer: str | None
    translator: str | None
    tags: list[str]
    genre: list[str]
    characters: list[str]
    series_group: list[str]
    web: str | None


class ComicInfoXML(XMLReader):
    """Specialized XML reader for ComicInfo XML files."""

    def __init__(self):
        super().__init__()

    @property
    def title(self) -> str | None:
        """Get the comic title."""
        return self.get_element_text("Title")

    @property
    def series(self) -> str | None:
        """Get the series name."""
        return self.get_element_text("Series")

    @property
    def number(self) -> int | None:
        """Get the chapter/issue number."""
        return self.get_element_int("Number")

    @property
    def language(self) -> str | None:
        """Get the language ISO code."""
        return self.get_element_text("LanguageISO")

    @property
    def page_count(self) -> int | None:
        """Get the page count."""
        return self.get_element_int("PageCount")

    @property
    def penciller(self) -> str | None:
        """Get the penciller/artist name."""
        return self.get_element_text("Penciller")

    @property
    def writer(self) -> str | None:
        """Get the writer name."""
        return self.get_element_text("Writer")

    @property
    def translator(self) -> str | None:
        """Get the translator name."""
        return self.get_element_text("Translator")

    @property
    def tags(self) -> list[str]:
        """Get the tags as a list."""
        return self.get_element_list("Tags")

    @property
    def genre(self) -> list[str]:
        """Get the genre as a list."""
        return self.get_element_list("Genre")

    @property
    def characters(self) -> list[str]:
        """Get the characters as a list."""
        return self.get_element_list("Characters")

    @property
    def series_group(self) -> list[str]:
        """Get the series group (parodies) as a list."""
        return self.get_element_list("SeriesGroup")

    @property
    def web(self) -> str | None:
        """Get the web URL."""
        return self.get_element_text("Web")

    @property
    def translated(self) -> bool:
        """Check if the comic is translated."""
        text = self.get_element_text("Translated")
        return text.lower() == "yes" if text else False

    @property
    def black_and_white(self) -> bool:
        """Check if the comic is black and white."""
        text = self.get_element_text("BlackAndWhite")
        return text.lower() == "yes" if text else True

    def to_dict(self) -> ComicInfoDict:
        return {
            "title": self.title,
            "series": self.series,
            "number": self.number,
            "language": self.language,
            "page_count": self.page_count,
            "penciller": self.penciller,
            "writer": self.writer,
            "translator": self.translator,
            "tags": self.tags,
            "genre": self.genre,
            "characters": self.characters,
            "series_group": self.series_group,
            "web": self.web,
        }
