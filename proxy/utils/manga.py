from __future__ import annotations

import json
import os
import re
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Self, TypeVar, overload

from ..config import Config
from ..enums import FileStatus
from .logger import get_logger
from .xml import ComicInfoDict, ComicInfoXML

if TYPE_CHECKING:
    from typing import Literal

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
    "GalleryCbzFile",
)

IMAGE_TYPE_MAPPING = {
    "j": "jpg",
    "p": "png",
    "w": "webp",
    "g": "gif",
}
SUPPORTED_IMAGE_TYPES = (
    "jpg",
    "png",
    "webp",
    "gif",
)
IMAGE_MIME_MAPPING = {
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@dataclass(eq=False, repr=False, slots=True)
class _GalleryPaginate:
    """Class to represent a paginated gallery."""

    page: int
    limit: int
    galleries: list[GalleryCbzFile]
    total: int


class CbzPage:
    def __init__(self, name: str, data: bytes):
        p = Path(name)
        self.page: int = int(p.stem) if p.stem.isdigit() else 0
        self.mime = IMAGE_MIME_MAPPING.get(p.suffix, "application/octet-stream")
        self.data: bytes = data

    def __len__(self) -> int:
        return len(self.data)

    def __del__(self):
        del self.data


V = TypeVar("V")


class AutoDiscard[V]:
    _instances: set[Self] = set()
    _lock = threading.Lock()
    _thread_started = False
    _logger = get_logger("AutoDiscard")

    def __init__(self, target: Any, attr: str = "_pages", threshold: int = 600):
        self._target = target
        self._attr = attr
        self._threshold = threshold
        self._last_access = time.time()

        with self._lock:
            self._instances.add(self)
            if not self._thread_started:
                self._start_thread()

    def get(self) -> V | None:
        self._last_access = time.time()
        return getattr(self._target, self._attr)

    def set(self, value: V) -> None:
        self._last_access = time.time()
        setattr(self._target, self._attr, value)

    def discard(self):
        setattr(self._target, self._attr, None)

    @classmethod
    def _start_thread(cls):
        if cls._thread_started:
            return
        cls._thread_started = True

        def run():
            cls._logger.info("AutoDiscard thread started.")
            time.sleep(60)  # wait for the first run to avoid immediate discard
            while True:
                time.sleep(60)
                total_instances = len(cls._instances)
                if total_instances == 0:
                    cls._logger.debug("No instances to discard.")
                    continue

                total_discarded = 0
                now = time.time()
                with cls._lock:
                    for inst in list(cls._instances):
                        if (
                            getattr(inst._target, inst._attr, None) is not None
                            and now - inst._last_access > inst._threshold
                        ):
                            inst.discard()
                            total_discarded += 1

                if total_discarded > 0:
                    cls._logger.info(
                        f"Discarded {total_discarded}/{total_instances} instances."
                    )

        t = threading.Thread(target=run, daemon=True, name="AutoDiscardThread")
        t.start()

    def __del__(self):
        with self._lock:
            self._instances.discard(self)


class GalleryCbzFile:
    def __init__(self, path: Path | str, force_extract: bool = False):
        self.path: Path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"File {self.path} does not exist.")

        if not self.path.stem.isdigit():
            raise ValueError(
                f"Filename stem '{self.path.stem}' is not numeric and cannot be used as an ID."
            )
        self.id: int = int(self.path.stem)

        self._thumbnail_dir = Path(Config.cache_path) / "thumbnails"
        self._thumbnail: Path | None = None
        self._info_file: Path = self.path.with_suffix(".info.json")
        self._info: ComicInfoDict | None = None
        self._pages: list[CbzPage] | None = None
        self._pages_discard: AutoDiscard[list[CbzPage]] | None = None

        if force_extract:
            self._extract()

    @property
    def info(self) -> ComicInfoDict:
        """Get the info dictionary."""
        if self._info is None:
            self._info = self._extract_info()
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
            thumb = next(self.thumbnail_dir.glob(f"{self.id}.*"), None)
            if thumb is None:
                thumb = self._extract_thumbnail()
            self._thumbnail = thumb
        return self._thumbnail

    @property
    def pages(self) -> list[CbzPage]:
        """Get the list of pages in the CBZ file."""
        if self._pages is None or self._pages_discard is None:
            self._pages = self._extract_pages()
            self._pages_discard = AutoDiscard(self, "_pages", threshold=10)
            self._pages_discard.set(self._pages)

        return self._pages_discard.get()  # type: ignore[return-value]

    def _extract(self, only_if_missing: bool = True, force: bool = False) -> None:
        """Extract necessary files from the archive. Only called if all the files are missing."""
        if not self.path.exists():
            raise FileNotFoundError(f"File {self.path} does not exist.")

        if (
            (only_if_missing and not force)
            and self._info_file.exists()
            and self.thumbnail.exists()
        ):
            return

        with zipfile.ZipFile(self.path, "r") as zip_file:
            if not self._info_file.exists():
                self._extract_info(zip_file=zip_file)

            if not self.thumbnail.exists():
                self._extract_thumbnail(zip_file=zip_file)

    def _extract_thumbnail(self, *, zip_file: zipfile.ZipFile | None = None) -> Path:
        """Extract the first image from the CBZ file as a thumbnail."""
        if self._thumbnail:
            return self._thumbnail
        thumbnail_path = next(self.thumbnail_dir.glob(f"{self.id}.*"), None)
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
        thumbnail_path = self.thumbnail_dir / f"{self.id}{p.suffix}"
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

    def _extract_pages(self) -> list[CbzPage]:
        if self._pages is not None:
            return self._pages

        self._pages = []
        with zipfile.ZipFile(self.path, "r") as zip_file:
            namelist = zip_file.namelist()
            namelist.remove("ComicInfo.xml")
            pages = list(
                CbzPage(n, zip_file.read(n))
                for n in namelist
                if n.endswith(SUPPORTED_IMAGE_TYPES)
            )
            self._pages = sorted(pages, key=lambda p: p.page)

        return self._pages

    def __len__(self) -> int:
        return len(self.pages)

    def __eq__(self, value: object) -> bool:
        if not isinstance(value, GalleryCbzFile):
            return False
        return self.path == value.path

    def __hash__(self) -> int:
        return hash(self.path)

    def __ne__(self, value: object) -> bool:
        if not isinstance(value, GalleryCbzFile):
            return True
        return self.path != value.path


@dataclass(eq=False, repr=False, slots=True)
class _GalleryDir:
    path: Path
    files: list[GalleryCbzFile]

    @property
    def count(self) -> int:
        """Get the number of files in the directory."""
        return len(self.files)


class _GalleryScanner:
    __slots__ = (
        "last_scanned",
        "path",
        "_gallery_dirs",
        "_chapter_files",
    )

    def __init__(self, init_path: Path | str):
        """Initialize the directory scanner."""
        self.last_scanned: datetime | None = None
        self.path: Path = init_path if isinstance(init_path, Path) else Path(init_path)

        self._gallery_dirs: dict[_Language, dict[_TitleDir, list[GalleryCbzFile]]] = {}
        self._chapter_files: dict[int, GalleryCbzFile] = {}

    @property
    def should_scan(self) -> bool:
        """Check if the directory should be scanned again."""
        if not self._gallery_dirs or not self.last_scanned:
            return True
        return (datetime.now() - self.last_scanned).total_seconds() > 3600

    @property
    def gallery_dirs(self) -> dict[_Language, dict[_TitleDir, list[GalleryCbzFile]]]:
        """Get the scanned directories."""
        if self.should_scan:
            self.scan(self.path)
        return self._gallery_dirs

    def _scan_gallery_dir(self, path: str | Path) -> list[GalleryCbzFile]:
        path = Path(path)
        if not path.is_dir():
            return []

        cbz_files = []
        for entry in os.scandir(path):
            if entry.is_file() and entry.name.endswith(".cbz"):
                cbz = GalleryCbzFile(entry.path)
                self._chapter_files[cbz.id] = cbz
                cbz_files.append(cbz)
        return cbz_files

    def add_gallery_dir(
        self, lang: _Language, dir_name: _TitleDir
    ) -> Callable[[], None]:
        """Only an entry for the directory for future use."""
        if lang not in self._gallery_dirs:
            self._gallery_dirs[lang] = {}

        if dir_name not in self._gallery_dirs[lang]:
            self._gallery_dirs[lang][dir_name] = []

        return lambda: self.scan_gallery_dir(lang, dir_name, sort=False)

    def scan_gallery_dir(
        self, lang: _Language, dir_name: _TitleDir, *, sort: bool = True
    ) -> None:
        """Add a scanned directory to the internal storage."""
        if lang not in self._gallery_dirs:
            self._gallery_dirs[lang] = {}

        dir_path = Path(self.path) / lang / dir_name
        if not dir_path.is_dir():
            return

        chapter_files = self._scan_gallery_dir(dir_path)
        if not chapter_files:
            self.remove_gallery_dir(lang, dir_name)
            return
        self._gallery_dirs[lang][dir_name] = chapter_files

        if sort:
            self._gallery_dirs[lang] = dict(
                sorted(self._gallery_dirs[lang].items(), key=lambda item: item[0])
            )

    def remove_gallery_dir(self, lang: _Language, dir_name: _TitleDir) -> bool:
        """Remove a gallery directory from the internal storage."""
        if lang not in self._gallery_dirs:
            return False

        if dir_name not in self._gallery_dirs[lang]:
            return False

        for file in self._gallery_dirs[lang][dir_name]:
            if file.id in self._chapter_files:
                del self._chapter_files[file.id]

        del self._gallery_dirs[lang][dir_name]
        return True

    def clear_gallery_dirs(self) -> None:
        """Clear all gallery directories from the internal storage."""
        self._gallery_dirs.clear()
        self._chapter_files.clear()
        self.last_scanned = None

    def scan(self, path: Path) -> None:
        """Scan the directory and store its path."""
        if not path.is_dir():
            return

        try:
            for lang_entry in os.scandir(path):
                le_name = lang_entry.name.lower()
                if not lang_entry.is_dir() or le_name not in (
                    "english",
                    "japanese",
                    "chinese",
                ):
                    continue

                try:
                    for sub_entry in os.scandir(lang_entry.path):
                        se_name = sub_entry.name.lower()
                        if not sub_entry.is_dir() or se_name.startswith("."):
                            continue
                        if se_name in self._gallery_dirs.get(le_name, {}):
                            if (
                                self.last_scanned  # yes scanned
                                and datetime.fromtimestamp(sub_entry.stat().st_mtime)
                                <= self.last_scanned  # and modification time is NOT greater than last scanned time
                            ):
                                continue
                        self.scan_gallery_dir(le_name, se_name, sort=False)

                except (OSError, PermissionError):
                    continue  # skip dir if no access
        except (OSError, PermissionError):
            if not any(entry.is_dir() for entry in os.scandir(path)):
                raise FileNotFoundError(f"No directories found in {path}.")
            if not self._gallery_dirs:
                raise FileNotFoundError(f"No galleries found in {path}.")

        for lang_entry in self._gallery_dirs:
            self._gallery_dirs[lang_entry] = dict(
                sorted(self._gallery_dirs[lang_entry].items(), key=lambda item: item[0])
            )

        self.last_scanned = datetime.now()

    def contains(self, lang: _Language, dir_name: _TitleDir) -> _GalleryDir | None:
        """Check if the scanned directories contain a specific file."""
        if lang not in self.gallery_dirs:
            return None

        for dir_name_variant in (dir_name, dir_name.lower()):
            if gallery_dir := self.gallery_dirs[lang].get(dir_name_variant):
                return _GalleryDir(
                    path=Path(self.path) / lang / dir_name_variant,
                    files=gallery_dir,
                )
        return None

    def fuzzy_contains(
        self, lang: _Language, dir_name: _TitleDir, match_threshold: float = 0.55
    ) -> list[tuple[float, _GalleryDir]]:
        """Check if the scanned directories contain a specific file (fuzzy match)."""
        matched: list[tuple[float, _GalleryDir]] = []
        for gallery_dir, files in self.gallery_dirs.get(lang, dict()).items():
            sm = SequenceMatcher(
                lambda x: x in ("-", "_"), gallery_dir.lower(), dir_name.lower()
            )
            ratio = round(sm.ratio(), 2)
            if ratio >= match_threshold:
                matched.append(
                    (
                        ratio,
                        _GalleryDir(
                            path=Path(self.path) / lang / gallery_dir, files=files
                        ),
                    )
                )
        return sorted(matched, key=lambda x: x[0], reverse=True)

    def get_gallery_paginate(
        self, lang: _Language, limit: int = 20, page: int = 1
    ) -> _GalleryPaginate:
        """Get paginated gallery files for a specific language."""
        galleries = self.gallery_dirs.get(lang, {})
        if not galleries:
            return _GalleryPaginate(page=page, limit=limit, galleries=[], total=0)

        paginated_galleries = islice(
            galleries.items(), (page - 1) * limit, page * limit
        )
        total = len(galleries)

        return _GalleryPaginate(
            page=page,
            limit=limit,
            galleries=[files[0] for _, files in paginated_galleries if files],
            total=total,
        )

    def get_chapter_file(self, gallery_id: int) -> GalleryCbzFile | None:
        """Get a chapter file by its ID."""
        return self._chapter_files.get(gallery_id)

    def get_gallery_series(self, name: str) -> list[GalleryCbzFile]:
        """Get a list of gallery files that match the series name."""
        if not name:
            return []

        name = name.lower().strip()
        for _, dirs in self.gallery_dirs.items():
            if series := dirs.get(name):
                return series

        return []


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


def remove_special_characters(text):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", text)
    # keep only Unicode letters, digits, spaces, and CJK characters
    cleaned = re.sub(r"[^\w\s\u4e00-\u9fff\u3040-\u30ff]", "", cleaned)
    # remove leading and trailing spaces and dots
    return cleaned.rstrip(" .")


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
    for path_variant in (clean_title, main_title.lower()):
        gallery_dir = GalleryScanner.contains(gallery_language, path_variant)
        if gallery_dir:
            return gallery_dir.path

        matched = GalleryScanner.fuzzy_contains(
            gallery_language, path_variant, match_threshold=0.58
        )
        if matched:
            return matched[0][1].path

    return base_path / clean_title


@overload
def make_gallery_path(
    *,
    gallery_title: ParsedMangaTitle,
    gallery_language: str,
    cache: Literal[False] = False,
) -> Path: ...


@overload
def make_gallery_path(
    *,
    gallery_title: ParsedMangaTitle,
    gallery_language: str,
    cache: Literal[True],
) -> tuple[Path, Callable[[], None]]: ...


def make_gallery_path(
    *,
    gallery_title: ParsedMangaTitle,
    gallery_language: str,
    cache: bool = False,
) -> Path | tuple[Path, Callable[[], None]]:
    """Create the gallery path based on the gallery information."""
    ret = _make_gallery_path(gallery_title, gallery_language)
    if cache:
        return ret, GalleryScanner.add_gallery_dir(gallery_language, ret.name)
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
        gallery_path = make_gallery_path(
            gallery_title=gallery_title, gallery_language=gallery_language
        )

    return _check_file_status(gallery_id, gallery_path)


def check_file_status_gallery(
    gallery_info: NhentaiGallery,
) -> FileStatus:
    """Check if a gallery is already downloaded based on its information."""
    gallery_path = make_gallery_path(
        gallery_title=gallery_info["title"], gallery_language=gallery_info["language"]
    )
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
