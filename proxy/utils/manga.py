from __future__ import annotations

import os
import re
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, overload

from ..config import Config
from ..enums import FileStatus

if TYPE_CHECKING:
    from typing import Iterator

    from .._types.nhentai import NhentaiGallery, ParsedMangaTitle


__all__ = (
    "clean_title",
    "clean_and_parse_title",
    "check_file_status",
    "check_file_status_gallery",
    "parse_manga_title",
    "make_gallery_path",
    "clean_and_split",
    "remove_special_characters",
    "IMAGE_TYPE_MAPPING",
    "SUPPORTED_IMAGE_TYPES",
    "GalleryScanner",
)

IMAGE_TYPE_MAPPING = {
    "j": "jpg",
    "p": "png",
    "w": "webp",
    "g": "gif",
}
SUPPORTED_IMAGE_TYPES = set(IMAGE_TYPE_MAPPING.values())


class _GalleryScanner:
    __slots__ = (
        "last_scanned",
        "path",
        "_scanned_dirs",
    )

    def __init__(self, init_path: Path | str):
        """Initialize the directory scanner."""
        self.last_scanned: datetime | None = None
        self.path: Path = init_path if isinstance(init_path, Path) else Path(init_path)
        self._scanned_dirs: dict[str, set[str]] = {}

    @property
    def should_scan(self) -> bool:
        """Check if the directory should be scanned again."""
        if not self._scanned_dirs or not self.last_scanned:
            return True
        return (datetime.now() - self.last_scanned).total_seconds() > 3600

    @property
    def scanned_dirs(self) -> dict[str, set[str]]:
        """Get the scanned directories."""
        if self.should_scan:
            self.scan(self.path)
        return self._scanned_dirs

    def add_scanned_dir(self, lang: str, dir_name: str) -> None:
        """Add a scanned directory to the internal storage."""
        self._scanned_dirs.setdefault(lang, set()).add(dir_name)

    def remove_scanned_dir(self, lang: str, dir_name: str) -> None:
        """Remove a scanned directory from the internal storage."""
        if lang in self._scanned_dirs:
            self._scanned_dirs[lang].discard(dir_name)
            if not self._scanned_dirs[lang]:
                del self._scanned_dirs[lang]

    def clear_scanned_dirs(self) -> None:
        """Clear all scanned directories."""
        self._scanned_dirs.clear()
        self.last_scanned = None

    def scan(self, path: Path) -> None:
        """Scan the directory and store its path."""
        if not path.is_dir():
            return

        for entry in os.scandir(path):
            if not entry.is_dir():
                continue

            for sub_entry in os.scandir(entry.path):
                if sub_entry.is_dir():
                    self.add_scanned_dir(entry.name, sub_entry.name)

        self.last_scanned = datetime.now()

    def contains(self, lang: str, dir_name: str) -> Path | None:
        """Check if the scanned directories contain a specific file."""
        if lang not in self.scanned_dirs:
            return None

        for scanned_dir in self.scanned_dirs[lang]:
            if scanned_dir.lower() == dir_name.lower():
                return Path(self.path) / lang / scanned_dir

    def fuzzy_contains(
        self, lang: str, dir_name: str, match_threshold: float = 0.55
    ) -> list[tuple[float, Path]]:
        """Check if the scanned directories contain a specific file (fuzzy match)."""
        matched: list[tuple[float, Path]] = []
        for scanned_dir in self.scanned_dirs.get(lang, set()):
            sm = SequenceMatcher(
                lambda x: x in ("-", "_"), scanned_dir.lower(), dir_name.lower()
            )
            ratio = round(sm.ratio(), 2)
            if ratio >= match_threshold:
                matched.append((ratio, Path(self.path) / lang / scanned_dir))
        return sorted(matched, key=lambda x: x[0], reverse=True)

    def __iter__(self) -> Iterator[tuple[str, str]]:
        """Iterate over the scanned directories."""
        if self.should_scan:
            self.scan(self.path)
        for lang, files in self.scanned_dirs.items():
            for file_name in files:
                yield (lang, file_name)


GalleryScanner = _GalleryScanner(Config.gallery_path)


def clean_title(manga_title):
    edited_title = re.sub(r"\[.*?]", "", manga_title).strip()
    edited_title = re.sub(r"\(.*?\)", "", edited_title).strip()
    edited_title = re.sub(r"\{.*?\}", "", edited_title).strip()

    # while True:
    #     if "|" in edited_title:
    #         edited_title = re.sub(r".*\|", "", edited_title).strip()
    #     else:
    #         break

    return edited_title


def remove_special_characters(title: str) -> str:
    """Remove special characters from the title."""
    return re.sub(r"[.\,;:!?\-()]|[^\x00-\x7F]", "_", title).strip()


def parse_manga_title(title: str) -> ParsedMangaTitle:
    pattern = r"^(.*?)(?:\s*[-+=]?(\d+)[-+=]?)?(?:\s*~([^~]+)~)?(?:\s*\|\s*(.+?)(?:\s*[-+=]?(\d+)[-+=]?)?)?$"

    match = re.match(pattern, title.strip())
    if match:
        main_title = match.group(1).strip()
        chapter_number_main: str | None = match.group(2)
        chapter_title: str = match.group(3).strip() if match.group(3) else "Chapter"
        english_title: str | None = match.group(4).strip() if match.group(4) else None
        chapter_number_end: str | None = match.group(5)

        chapter_number = chapter_number_main or chapter_number_end or "1"

        return {
            "main_title": main_title,
            "chapter_number": int(chapter_number),
            "chapter_title": chapter_title,
            "english_title": english_title,
        }

    return {
        "main_title": title.strip(),
        "chapter_number": 1,
        "chapter_title": "Chapter",
        "english_title": None,
    }


def clean_and_parse_title(title: str) -> ParsedMangaTitle:
    """Clean and parse the manga title to extract structured information."""
    cleaned_title = clean_title(title)
    return parse_manga_title(cleaned_title)


def clean_and_split(content: str) -> list[str]:
    return [t.strip() for t in content.split("|") if t.strip()]


def _make_gallery_path(
    gallery_title: ParsedMangaTitle,
    gallery_language: str,
) -> Path:
    """Create the gallery path based on the gallery information."""
    base_path = Path(Config.gallery_path) / gallery_language
    main_title = gallery_title["main_title"]

    clean_title = remove_special_characters(main_title).lower()
    if gallery_path := GalleryScanner.contains(gallery_language, clean_title):
        return gallery_path

    if gallery_path := GalleryScanner.contains(gallery_language, main_title.lower()):
        return gallery_path

    for path_variant in (clean_title, main_title.lower()):
        matched = GalleryScanner.fuzzy_contains(
            gallery_language, path_variant, match_threshold=0.58
        )
        if matched:
            return matched[0][1]

    return base_path / clean_title


def make_gallery_path(
    gallery_title: ParsedMangaTitle,
    gallery_language: str,
    *,
    cache: bool = True,
) -> Path:
    """Create the gallery path based on the gallery information."""
    ret = _make_gallery_path(gallery_title, gallery_language)
    if cache:
        GalleryScanner.add_scanned_dir(gallery_language, ret.name)
    return ret


def _check_file_status(
    gallery_id: int,
    gallery_path: Path,
) -> FileStatus:
    if not gallery_path.exists():
        return FileStatus.NOT_FOUND

    cbz_path = gallery_path / f"{gallery_id}.cbz"
    if cbz_path.exists():
        return FileStatus.CONVERTED

    gallery_path = gallery_path / str(gallery_id)
    if not gallery_path.exists():
        return FileStatus.NOT_FOUND

    return FileStatus.MISSING


@overload
def check_file_status(
    gallery_id: int,
    *,
    gallery_title: ParsedMangaTitle,
    gallery_language: str,
    gallery_path: None = None,
) -> FileStatus: ...


@overload
def check_file_status(
    gallery_id: int,
    *,
    gallery_path: Path,
    gallery_title: None = None,
    gallery_language: None = None,
) -> FileStatus: ...


def check_file_status(
    gallery_id: int,
    *,
    gallery_title: ParsedMangaTitle | None = None,
    gallery_language: str | None = None,
    gallery_path: Path | None = None,
) -> FileStatus:
    """Check if a gallery is already downloaded based on its ID and title."""
    if not gallery_path:
        if not gallery_language:
            raise ValueError(
                "gallery_language must be provided if gallery_path is not."
            )
        if not gallery_title:
            raise ValueError("gallery_title must be provided if gallery_path is not.")
        gallery_path = make_gallery_path(gallery_title, gallery_language)

    return _check_file_status(gallery_id, gallery_path)


def check_file_status_gallery(
    gallery_info: NhentaiGallery,
) -> FileStatus:
    """Check if a gallery is already downloaded based on its information."""
    gallery_path = make_gallery_path(gallery_info["title"], gallery_info["language"])
    if not gallery_path.exists():
        return FileStatus.NOT_FOUND

    ret = _check_file_status(gallery_info["id"], gallery_path=gallery_path)
    if ret == FileStatus.MISSING and gallery_path.exists() and gallery_path.is_dir():
        expected_files = tuple(
            f"{img_idx}.{IMAGE_TYPE_MAPPING.get(image['t'], 'jpg')}"
            for img_idx, image in enumerate(gallery_info["images"]["pages"], start=1)  # type: ignore
        )
        result = tuple((gallery_path / name).exists() for name in expected_files)
        if all(result):
            return FileStatus.COMPLETED
        elif any(result):
            return FileStatus.MISSING
    return ret
