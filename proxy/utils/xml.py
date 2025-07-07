from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

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
        self.add_element("Genre", ", ".join(gallery_info["parodies"]))
        self.add_element("SeriesGroup", ", ".join(gallery_info["characters"]))
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
