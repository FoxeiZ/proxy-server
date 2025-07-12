from __future__ import annotations

import json
import os
import re
import zipfile
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, overload

from ..config import Config
from ..enums import FileStatus
from .xml import ComicInfoDict, ComicInfoXML

if TYPE_CHECKING:
    from typing import Iterator, Literal

    from .._types.nhentai import NhentaiGallery, ParsedMangaTitle

    _Language = Literal["english", "japanese", "chinese"] | str
    _TitleDir = str


__all__ = (
    "clean_title",
    "clean_and_parse_title",
    "check_file_status",
    "check_file_status_gallery",
    "parse_manga_title",
    "make_gallery_path",
    "split_and_clean",
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
SUPPORTED_IMAGE_TYPES = tuple(IMAGE_TYPE_MAPPING.values())


class _GalleryCbzFile:
    def __init__(self, path: Path | str, force_extract: bool = False):
        self.path: Path = Path(path)
        self._id: str = self.path.stem

        self._thumbnail_dir = Path(Config.cache_path) / "thumbnails"
        self._thumbnail: Path | None = None
        self._info_file: Path = self.path.with_suffix(".info.json")
        self._info: ComicInfoDict | None = None

        if not self.path.exists():
            raise FileNotFoundError(f"File {self.path} does not exist.")
        if force_extract:
            self.__extract()

    @property
    def info(self) -> ComicInfoDict | None:
        """Get the info dictionary."""
        if self._info is None:
            if self._info_file.exists():
                with open(self._info_file, "r", encoding="utf-8") as f:
                    self._info = json.load(f)
            else:
                self._info = {}
        return self._info

    @property
    def thumbnail_dir(self) -> Path:
        """Get the directory of the thumbnail image."""
        if not self._thumbnail_dir.exists():
            self._thumbnail_dir.mkdir(parents=True, exist_ok=True)
        return self._thumbnail_dir

    @property
    def thumbnail(self) -> Path:
        """Get the thumbnail image path."""
        if self._thumbnail is None:
            thumb = next(self.thumbnail_dir.glob(f"{self._id}.*"), None)
            if thumb is None:
                thumb = self._extract_thumbnail()
            self._thumbnail = thumb
        return self._thumbnail

    def __extract(self, only_if_missing: bool = True) -> None:
        """Extract necessary files from the archive. Only called if all the files are missing."""
        if not self.path.exists():
            raise FileNotFoundError(f"File {self.path} does not exist.")

        with zipfile.ZipFile(self.path, "r") as zip_file:
            if only_if_missing and self._info_file.exists() and self.thumbnail.exists():
                return

            if not self._info_file.exists():
                self._extract_info(zip_file=zip_file)

            if not self.thumbnail.exists():
                self._extract_thumbnail(zip_file=zip_file)

    def _extract_thumbnail(self, *, zip_file: zipfile.ZipFile | None = None) -> Path:
        """Extract the first image from the CBZ file as a thumbnail."""
        if self._thumbnail:
            return self._thumbnail
        thumbnail_path = next(self.thumbnail_dir.glob(f"{self._id}.*"), None)
        if thumbnail_path:
            return thumbnail_path

        zip_close = False
        if not zip_file:
            if not self.path.exists():
                raise FileNotFoundError(f"File {self.path} does not exist.")
            zip_file = zipfile.ZipFile(self.path, "r")
            zip_close = True

        namelist = zip_file.namelist()
        names = sorted(
            name for name in namelist if name.endswith(SUPPORTED_IMAGE_TYPES)
        )
        if not names:
            raise FileNotFoundError(
                f"No supported image files found in {self.path}. Supported types: {SUPPORTED_IMAGE_TYPES}"
            )

        p = Path(names[0])
        thumbnail_path = self.thumbnail_dir / f"{self._id}{p.suffix}"
        with (
            zip_file.open(names[0]) as source,
            open(thumbnail_path, "wb") as target,
        ):
            target.write(source.read())

        if zip_close:
            zip_file.close()
        return thumbnail_path

    def _extract_info(
        self, *, zip_file: zipfile.ZipFile | None = None
    ) -> ComicInfoDict:
        if self._info:
            return self._info

        if self._info_file.exists():
            with open(self._info_file, "r", encoding="utf-8") as f:
                return json.load(f)

        close_zip = False
        if not zip_file:
            if not self.path.exists():
                raise FileNotFoundError(f"File {self.path} does not exist.")
            zip_file = zipfile.ZipFile(self.path, "r")
            close_zip = True

        info: ComicInfoDict = {}
        if "ComicInfo.xml" in zip_file.namelist():
            with zip_file.open("ComicInfo.xml") as source:
                xml_content = source.read().decode("utf-8")
            comic_info = ComicInfoXML.from_string(xml_content)
            info = comic_info.to_dict()

        if close_zip:
            zip_file.close()

        if info:
            with open(self._info_file, "w", encoding="utf-8") as f:
                json.dump(info, f, indent=4)
            return info

        raise FileNotFoundError(
            f"No ComicInfo.xml found in {self.path}. Please ensure the file is a valid CBZ archive."
        )

    def __eq__(self, value: object) -> bool:
        if not isinstance(value, _GalleryCbzFile):
            return False
        return self.path == value.path

    def __hash__(self) -> int:
        return hash(self.path)

    def __ne__(self, value: object) -> bool:
        if not isinstance(value, _GalleryCbzFile):
            return True
        return self.path != value.path


class _GalleryDir:
    def __init__(self, path: Path | str):
        self.path: Path = Path(path)
        self._title: str = self.path.name
        self._cbz_files: dict[str, _GalleryCbzFile] = {}
        self._is_scanned: bool = False

    @property
    def count(self) -> int:
        if not self._is_scanned:
            self.scan()
        return len(self._cbz_files)

    @property
    def files(self) -> list[_GalleryCbzFile]:
        """Get the list of CBZ files in the directory."""
        if not self._is_scanned:
            self.scan()
        return list(self._cbz_files.values())

    def scan(self):
        if not self.path.is_dir():
            return self

        for entry in os.scandir(self.path):
            if entry.is_file() and entry.name.endswith(".cbz"):
                self._cbz_files[entry.name] = _GalleryCbzFile(entry.path)

        self._is_scanned = True
        return self


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
        self._scanned_dirs: dict[_Language, dict[_TitleDir, _GalleryDir]] = {}

    @property
    def should_scan(self) -> bool:
        """Check if the directory should be scanned again."""
        if not self._scanned_dirs or not self.last_scanned:
            return True
        return (datetime.now() - self.last_scanned).total_seconds() > 3600

    @property
    def scanned_dirs(self) -> dict[_Language, dict[_TitleDir, _GalleryDir]]:
        """Get the scanned directories."""
        if self.should_scan:
            self.scan(self.path)
        return self._scanned_dirs

    def add_scanned_dir(self, lang: _Language, dir_name: _TitleDir) -> None:
        """Add a scanned directory to the internal storage."""
        if lang not in self._scanned_dirs:
            self._scanned_dirs[lang] = {}

        if dir_name in self._scanned_dirs[lang]:
            return

        dir_path = Path(self.path) / lang / dir_name
        if not dir_path.is_dir():
            return

        self._scanned_dirs[lang][dir_name] = _GalleryDir(dir_path).scan()

    def remove_scanned_dir(self, lang: _Language, dir_name: _TitleDir) -> None:
        """Remove a scanned directory from the internal storage."""
        if lang not in self._scanned_dirs or dir_name not in self._scanned_dirs[lang]:
            return
        del self._scanned_dirs[lang][dir_name]

    def clear_scanned_dirs(self) -> None:
        """Clear all scanned directories."""
        self._scanned_dirs.clear()
        self.last_scanned = None

    def scan(self, path: Path) -> None:
        """Scan the directory and store its path."""
        if not path.is_dir():
            return

        for lang_entry in os.scandir(path):
            if not lang_entry.is_dir():
                continue

            for sub_entry in os.scandir(lang_entry.path):
                if not sub_entry.is_dir() or sub_entry.name.startswith("."):
                    continue
                if sub_entry in self._scanned_dirs.get(lang_entry.name, {}):
                    if (
                        self.last_scanned  # yes scanned
                        and datetime.fromtimestamp(sub_entry.stat().st_mtime)
                        > self.last_scanned  # and modification time is greater than last scanned time
                    ):
                        continue

                self.add_scanned_dir(lang_entry.name, sub_entry.name)

        self.last_scanned = datetime.now()

    def contains(self, lang: _Language, dir_name: _TitleDir) -> Path | None:
        """Check if the scanned directories contain a specific file."""
        if lang not in self.scanned_dirs:
            return None

        for name_variant in (dir_name, dir_name.lower()):
            if name_variant in self.scanned_dirs[lang]:
                return self.scanned_dirs[lang][name_variant].path
        return None

    def fuzzy_contains(
        self, lang: _Language, dir_name: _TitleDir, match_threshold: float = 0.55
    ) -> list[tuple[float, Path]]:
        """Check if the scanned directories contain a specific file (fuzzy match)."""
        matched: list[tuple[float, Path]] = []
        for scanned_dir in self.scanned_dirs.get(lang, dict()).keys():
            sm = SequenceMatcher(
                lambda x: x in ("-", "_"), scanned_dir.lower(), dir_name.lower()
            )
            ratio = round(sm.ratio(), 2)
            if ratio >= match_threshold:
                matched.append((ratio, Path(self.path) / lang / scanned_dir))
        return sorted(matched, key=lambda x: x[0], reverse=True)

    def __iter__(self) -> Iterator[tuple[_Language, _GalleryDir]]:
        """Iterate over the scanned directories."""
        for lang, files in self.scanned_dirs.items():
            for file_name, gallery_dir in files.items():
                yield (lang, gallery_dir)


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


def split_and_clean(content: str) -> list[str]:
    return [t.strip() for t in content.split("|") if t.strip()]


def _make_gallery_path(
    gallery_title: ParsedMangaTitle,
    gallery_language: str,
) -> Path:
    """Create the gallery path based on the gallery information."""
    base_path = Path(Config.gallery_path) / gallery_language
    main_title = gallery_title["main_title"]

    if gallery_language not in ("english", "japanese", "chinese"):
        raise ValueError(
            f"Unsupported gallery language: {gallery_language}. "
            "Supported languages are: english, japanese, chinese."
        )

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
