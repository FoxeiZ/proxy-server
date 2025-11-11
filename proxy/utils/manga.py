from __future__ import annotations

import asyncio
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Coroutine, Self, TypeVar, overload

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


T = TypeVar("T")
V = TypeVar("V")


class AutoDiscard[T, V]:
    _instances: set[Self] = set()
    _lock = asyncio.Lock()
    _task_started = False
    _task: asyncio.Task | None = None
    _sleeping_time: int = 60
    _logger = get_logger("AutoDiscard")

    def __init__(
        self,
        target: T,
        attr: str = "_pages",
        threshold: int = 600,
    ):
        self._target: T = target
        self._attr = attr
        self._threshold = threshold
        self._last_access = time.time()

        self._instances.add(self)
        if not self._task_started or not self._task:
            self._start_background_task()

    @property
    def threshold(self) -> int:
        return self._threshold

    @property
    def last_access(self) -> float:
        return self._last_access

    async def get(self) -> V | None:
        async with self._lock:
            if self not in self._instances:
                self._logger.debug(
                    "instance %s/%d not found in AutoDiscard instances.",
                    self,
                    id(self),
                )
                self._instances.add(self)

        self._last_access = time.time()
        return getattr(self._target, self._attr)

    async def set(self, value: V) -> None:
        self._last_access = time.time()
        setattr(self._target, self._attr, value)

    def discard(self):
        setattr(self._target, self._attr, None)

    @classmethod
    def _start_background_task(cls) -> None:
        if cls._task_started and cls._task:
            return
        cls._task_started = True

        async def run():
            cls._logger.info("AutoDiscard task started.")
            await asyncio.sleep(
                cls._sleeping_time
            )  # wait for the first run to avoid immediate discard
            while True:
                await asyncio.sleep(cls._sleeping_time)
                total_instances = len(cls._instances)
                if total_instances == 0:
                    cls._logger.info("no instances to discard.")
                    continue

                total_discarded = 0
                now = time.time()
                async with cls._lock:
                    for inst in list(cls._instances):
                        if (
                            getattr(inst._target, inst._attr, None) is not None
                            and now - inst._last_access > inst._threshold
                        ):
                            inst.discard()
                            # dereference to not keep track of the instance anymore
                            cls._instances.discard(inst)
                            total_discarded += 1

                if total_discarded > 0:
                    cls._logger.info(
                        "discarded %d/%d instances.", total_discarded, total_instances
                    )

        cls._task = asyncio.create_task(run(), name="AutoDiscardThread")

    def shutdown(self):
        self._logger.info(
            "Shutting down AutoDiscard instance %s/%d.",
            self,
            id(self),
        )
        self._instances.clear()
        if self._task:
            self._task.cancel()
            self._task = None

    def __del__(self):
        self._logger.warning(
            "AutoDiscard instance %s/%d is being deleted. "
            "This should not happen, please check your code.",
            self,
            id(self),
        )
        # This is acceptable as __del__ shouldn't be called during normal operation
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
        self._pages_discard: AutoDiscard[Self, list[CbzPage]] | None = None
        self._force_extract: bool = force_extract

    async def get_info(self) -> ComicInfoDict:
        """Get the info dictionary."""
        if self._info is None or self._force_extract:
            self._info = await self._extract_info()
        return self._info

    @property
    def thumbnail_dir(self) -> Path:
        """Get the directory of the thumbnail image."""
        if not self._thumbnail_dir.exists():
            self._thumbnail_dir.mkdir(parents=True, exist_ok=True)
        return self._thumbnail_dir

    async def get_thumbnail(self) -> Path:
        """Get the thumbnail image path."""
        if self._thumbnail is None:
            thumb = next(self.thumbnail_dir.glob(f"{self.id}.*"), None)
            if thumb is None:
                thumb = await self._extract_thumbnail()
            self._thumbnail = thumb
        return self._thumbnail

    async def get_pages(self) -> list[CbzPage]:
        """Get the list of pages in the CBZ file."""
        if self._pages_discard is None:
            self._pages_discard = AutoDiscard(self, "_pages", threshold=600)

        if self._pages is None:
            self._pages = await self._extract_pages()
            await self._pages_discard.set(self._pages)

        return await self._pages_discard.get() or []

    async def read_page(self, page: int) -> CbzPage:
        pages = await self.get_pages()
        if not (1 <= page <= len(pages)):
            raise ValueError(f"Page {page} is out of range for this CBZ file.")
        return pages[page - 1]

    async def _extract(self, only_if_missing: bool = True, force: bool = False) -> None:
        """Extract necessary files from the archive. Only called if all the files are missing."""
        if not self.path.exists():
            raise FileNotFoundError(f"File {self.path} does not exist.")

        thumbnail = await self.get_thumbnail()
        if (
            (only_if_missing and not force)
            and self._info_file.exists()
            and thumbnail.exists()
        ):
            return

        zip_file = await asyncio.to_thread(zipfile.ZipFile, self.path, "r")
        try:
            if not self._info_file.exists():
                await self._extract_info(zip_file=zip_file)

            if not thumbnail.exists():
                await self._extract_thumbnail(zip_file=zip_file)
        finally:
            await asyncio.to_thread(zip_file.close)

    async def _extract_thumbnail(
        self, *, zip_file: zipfile.ZipFile | None = None
    ) -> Path:
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
            zip_file = await asyncio.to_thread(zipfile.ZipFile, self.path, "r")
            zip_close = True

        def _get_names():
            namelist = zip_file.namelist()
            return sorted(
                name for name in namelist if name.endswith(SUPPORTED_IMAGE_TYPES)
            )

        names = await asyncio.to_thread(_get_names)
        if not names:
            raise FileNotFoundError(
                f"No supported image files found in {self.path}. Supported types: {SUPPORTED_IMAGE_TYPES}"
            )

        p = Path(names[0])
        thumbnail_path = self.thumbnail_dir / f"{self.id}{p.suffix}"

        def _write_thumbnail():
            with (
                zip_file.open(names[0]) as source,
                open(thumbnail_path, "wb") as target,
            ):
                target.write(source.read())

        await asyncio.to_thread(_write_thumbnail)

        if zip_close:
            await asyncio.to_thread(zip_file.close)
        return thumbnail_path

    async def _extract_info(
        self, *, zip_file: zipfile.ZipFile | None = None
    ) -> ComicInfoDict:
        if self._info:
            return self._info

        if self._info_file.exists():

            def _read_json():
                with open(self._info_file, "r", encoding="utf-8") as f:
                    return json.load(f)

            return await asyncio.to_thread(_read_json)

        close_zip = False
        if not zip_file:
            if not self.path.exists():
                raise FileNotFoundError(f"File {self.path} does not exist.")
            zip_file = await asyncio.to_thread(zipfile.ZipFile, self.path, "r")
            close_zip = True

        def _read_xml():
            info: ComicInfoDict = {}
            if "ComicInfo.xml" in zip_file.namelist():
                with zip_file.open("ComicInfo.xml") as source:
                    xml_content = source.read().decode("utf-8")
                comic_info = ComicInfoXML.from_string(xml_content)
                info = comic_info.to_dict()
            return info

        info = await asyncio.to_thread(_read_xml)

        if close_zip:
            await asyncio.to_thread(zip_file.close)

        if info:

            def _write_json():
                with open(self._info_file, "w", encoding="utf-8") as f:
                    json.dump(info, f, indent=4)

            await asyncio.to_thread(_write_json)
            return info

        raise FileNotFoundError(
            f"No ComicInfo.xml found in {self.path}. Please ensure the file is a valid CBZ archive."
        )

    async def _extract_pages(self) -> list[CbzPage]:
        if self._pages is not None:
            return self._pages

        def _read_pages():
            with zipfile.ZipFile(self.path, "r") as zip_file:
                namelist = zip_file.namelist()
                namelist.remove("ComicInfo.xml")
                pages = list(
                    CbzPage(n, zip_file.read(n))
                    for n in namelist
                    if n.endswith(SUPPORTED_IMAGE_TYPES)
                )
                return sorted(pages, key=lambda p: p.page)

        self._pages = await asyncio.to_thread(_read_pages)
        return self._pages

    def __len__(self) -> int:
        """
        Note: This cannot be async, so it returns 0 if pages not loaded

        #### Callers should use: len(await gallery.get_pages()) or `self.get_page_count()` instead
        """
        if self._pages is None:
            return 0
        return len(self._pages)

    async def get_page_count(self) -> int:
        """Get the number of pages in the CBZ file."""
        pages = await self.get_pages()
        return len(pages)

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

    async def gallery_dirs(
        self,
    ) -> dict[_Language, dict[_TitleDir, list[GalleryCbzFile]]]:
        """Get the scanned directories."""
        if self.should_scan:
            await self.scan(self.path)
        return self._gallery_dirs

    async def chapter_files(self) -> dict[int, GalleryCbzFile]:
        """Get the scanned chapter files, relative to gallery dir."""
        if self.should_scan:
            await self.scan(self.path)
        return self._chapter_files

    async def _scan_gallery_dir(self, path: str | Path) -> list[GalleryCbzFile]:
        path = Path(path)
        if not path.is_dir():
            return []

        def _scan():
            cbz_files_data = []
            for entry in os.scandir(path):
                if entry.is_file() and entry.name.endswith(".cbz"):
                    cbz_files_data.append(entry.path)
            return cbz_files_data

        paths = await asyncio.to_thread(_scan)
        cbz_files = []
        for entry_path in paths:
            cbz = GalleryCbzFile(entry_path)
            self._chapter_files[cbz.id] = cbz
            cbz_files.append(cbz)
        return cbz_files

    def add_gallery_dir(
        self, lang: _Language, dir_name: _TitleDir
    ) -> Callable[[], Coroutine]:
        """Only an entry for the directory for future use."""
        if lang not in self._gallery_dirs:
            self._gallery_dirs[lang] = {}

        if dir_name not in self._gallery_dirs[lang]:
            self._gallery_dirs[lang][dir_name] = []

        return lambda: self.scan_gallery_dir(lang, dir_name, sort=False)

    async def scan_gallery_dir(
        self, lang: _Language, dir_name: _TitleDir, *, sort: bool = True
    ) -> None:
        """Add a scanned directory to the internal storage."""
        if lang not in self._gallery_dirs:
            self._gallery_dirs[lang] = {}

        dir_path = Path(self.path) / lang / dir_name
        if not dir_path.is_dir():
            return

        chapter_files = await self._scan_gallery_dir(dir_path)
        if not chapter_files:
            self.remove_gallery_dir(lang, dir_name)
            return

        async def _sort_info(
            chapter_files: list[GalleryCbzFile],
        ) -> list[GalleryCbzFile]:
            infos = await asyncio.gather(*(g.get_info() for g in chapter_files))
            gallery_info_pairs = list(zip(chapter_files, infos))
            gallery_info_pairs.sort(
                key=lambda pair: pair[1].get("number") or pair[0].id or 0
            )
            return [pair[0] for pair in gallery_info_pairs]

        chapter_files = await _sort_info(chapter_files)
        # chapter_files = sorted(chapter_files, key=lambda g: g.id or 0)
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

    async def scan(self, path: Path) -> None:
        """Scan the directory and store its path."""
        if not path.is_dir():
            return

        try:

            def _scan_languages():
                entries = []
                for lang_entry in os.scandir(path):
                    le_name = lang_entry.name.lower()
                    if lang_entry.is_dir() and le_name in (
                        "english",
                        "japanese",
                        "chinese",
                    ):
                        entries.append((le_name, lang_entry.path))
                return entries

            lang_entries = await asyncio.to_thread(_scan_languages)

            for le_name, le_path in lang_entries:
                try:
                    # Wrap subdirectory scanning
                    def _scan_subdirs(lang_path):
                        entries = []
                        for sub_entry in os.scandir(lang_path):
                            se_name = sub_entry.name.lower()
                            if sub_entry.is_dir() and not se_name.startswith("."):
                                entries.append((se_name, sub_entry.stat().st_mtime))
                        return entries

                    sub_entries = await asyncio.to_thread(_scan_subdirs, le_path)

                    for se_name, mtime in sub_entries:
                        if se_name in self._gallery_dirs.get(le_name, {}):
                            if (
                                self.last_scanned  # yes scanned
                                and datetime.fromtimestamp(mtime)
                                <= self.last_scanned  # and modification time is NOT greater than last scanned time
                            ):
                                continue
                        await self.scan_gallery_dir(le_name, se_name, sort=False)

                except (OSError, PermissionError):
                    continue  # skip dir if no access
        except (OSError, PermissionError):
            has_dirs = await asyncio.to_thread(
                lambda: any(entry.is_dir() for entry in os.scandir(path))
            )
            if not has_dirs:
                raise FileNotFoundError(f"No directories found in {path}.")
            if not self._gallery_dirs:
                raise FileNotFoundError(f"No galleries found in {path}.")

        for lang_entry in self._gallery_dirs:
            self._gallery_dirs[lang_entry] = dict(
                sorted(self._gallery_dirs[lang_entry].items(), key=lambda item: item[0])
            )

        self.last_scanned = datetime.now()

    async def contains(
        self, lang: _Language, dir_name: _TitleDir
    ) -> _GalleryDir | None:
        """Check if the scanned directories contain a specific file."""
        gallery_dirs = await self.gallery_dirs()
        if not gallery_dirs or lang not in gallery_dirs:
            return None

        for dir_name_variant in (dir_name, dir_name.lower()):
            if gallery_dir := gallery_dirs[lang].get(dir_name_variant):
                return _GalleryDir(
                    path=Path(self.path) / lang / dir_name_variant,
                    files=gallery_dir,
                )
        return None

    async def fuzzy_contains(
        self, lang: _Language, dir_name: _TitleDir, match_threshold: float = 0.55
    ) -> list[tuple[float, _GalleryDir]]:
        """Check if the scanned directories contain a specific file (fuzzy match)."""
        matched: list[tuple[float, _GalleryDir]] = []
        gallery_dirs = await self.gallery_dirs()
        for gallery_dir, files in gallery_dirs.get(lang, dict()).items():
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

    async def get_gallery_paginate(
        self, lang: _Language, limit: int = 20, page: int = 1
    ) -> _GalleryPaginate:
        """Get paginated gallery files for a specific language."""
        gallery_dirs = await self.gallery_dirs()
        galleries = gallery_dirs.get(lang, {})
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

    async def get_chapter_file(self, gallery_id: int) -> GalleryCbzFile | None:
        """Get a chapter file by its ID."""
        return (await self.chapter_files()).get(gallery_id)

    async def get_gallery_series(self, name: str) -> list[GalleryCbzFile]:
        """Get a list of gallery files that match the series name."""
        if not name:
            return []

        name = name.lower().strip()
        for _, dirs in (await self.gallery_dirs()).items():
            if series := dirs.get(name):
                return series

        return []

    async def _get_relative(
        self, *, gallery: GalleryCbzFile | None = None, gallery_id: int | None = None
    ) -> tuple[list[GalleryCbzFile], int]:
        if not gallery and not gallery_id:
            raise ValueError
        if gallery_id:
            gallery = await self.get_chapter_file(gallery_id)
        if not gallery:
            raise ValueError

        info = await gallery.get_info()
        series_name = (info.get("folder") or "").lower().strip()
        if not series_name:
            raise ValueError
        series = await self.get_gallery_series(series_name)
        if not series or gallery not in series:
            raise ValueError

        # we already sorted when import, so we can just find the index
        # and not worry about sorting again
        # series = sorted(series, key=lambda g: g.info.get("number") or g.id or 0)
        current_index = series.index(gallery)
        return series, current_index

    async def get_next_chapter(
        self, *, gallery: GalleryCbzFile | None = None, gallery_id: int | None = None
    ) -> GalleryCbzFile | None:
        """Get the next chapter file after the given gallery."""
        try:
            series, current_index = await self._get_relative(
                gallery=gallery, gallery_id=gallery_id
            )
            if current_index == -1 or current_index >= len(series) - 1:
                return None
            return series[current_index + 1]
        except ValueError:
            return None

    async def get_prev_chapter(
        self, *, gallery: GalleryCbzFile | None = None, gallery_id: int | None = None
    ) -> GalleryCbzFile | None:
        """Get the previous chapter file before the given gallery."""
        try:
            series, current_index = await self._get_relative(
                gallery=gallery, gallery_id=gallery_id
            )
            if current_index <= 0:
                return None
            return series[current_index - 1]
        except ValueError:
            return None


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


async def _make_gallery_path(
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
        gallery_dir = await GalleryScanner.contains(gallery_language, path_variant)
        if gallery_dir:
            return gallery_dir.path

        matched = await GalleryScanner.fuzzy_contains(
            gallery_language, path_variant, match_threshold=0.7
        )
        if matched:
            return matched[0][1].path

    return base_path / clean_title


@overload
async def make_gallery_path(
    *,
    gallery_title: ParsedMangaTitle,
    gallery_language: str,
    cache: Literal[False] = False,
) -> Path: ...


@overload
async def make_gallery_path(
    *,
    gallery_title: ParsedMangaTitle,
    gallery_language: str,
    cache: Literal[True],
) -> tuple[Path, Callable[[], Coroutine]]: ...


async def make_gallery_path(
    *,
    gallery_title: ParsedMangaTitle,
    gallery_language: str,
    cache: bool = False,
) -> Path | tuple[Path, Callable[[], Coroutine]]:
    """Create the gallery path based on the gallery information."""
    ret = await _make_gallery_path(gallery_title, gallery_language)
    if cache:
        return ret, GalleryScanner.add_gallery_dir(gallery_language, ret.name)
    return ret


async def _check_file_status(
    gallery_id: int,
    gallery_path: Path,
    gallery_title: ParsedMangaTitle | None = None,
    gallery_language: str | None = None,
) -> FileStatus:
    if not gallery_path.exists():
        return FileStatus.NOT_FOUND

    cbz_path = gallery_path / f"{gallery_id}.cbz"
    if cbz_path.exists():
        return FileStatus.CONVERTED

    gallery_path = gallery_path / str(gallery_id)
    if not gallery_path.exists():
        if gallery_title and gallery_language:
            main_title = gallery_title["main_title"]
            clean_title = remove_special_characters(main_title).lower()

            matched = await GalleryScanner.fuzzy_contains(
                gallery_language, clean_title, match_threshold=0.78
            )
            # print([f"{clean_title} / {a[0]}, {a[1].path}" for a in matched])

            for ratio, gallery_dir in matched:
                if gallery_dir.files:
                    if ratio >= 0.9:
                        return FileStatus.AVAILABLE
                    return FileStatus.MAYBE_AVALIABLE

        return FileStatus.NOT_FOUND

    return FileStatus.MISSING


@overload
async def check_file_status(
    gallery_id: int,
    *,
    gallery_title: ParsedMangaTitle,
    gallery_language: str,
    gallery_path: None = None,
) -> FileStatus: ...


@overload
async def check_file_status(
    gallery_id: int,
    *,
    gallery_path: Path,
    gallery_title: None = None,
    gallery_language: None = None,
) -> FileStatus: ...


async def check_file_status(
    gallery_id: int,
    *,
    gallery_title: ParsedMangaTitle | None = None,
    gallery_language: str | None = None,
    gallery_path: Path | None = None,
) -> FileStatus:
    """Check if a gallery is already downloaded based on its ID and title."""
    if not gallery_path:
        if not gallery_language or not gallery_title:
            raise ValueError(
                "gallery_language and gallery_title must be provided if gallery_path is not."
            )
        gallery_path = await make_gallery_path(
            gallery_title=gallery_title, gallery_language=gallery_language
        )

    result = await _check_file_status(
        gallery_id, gallery_path, gallery_title, gallery_language
    )

    if (
        result == FileStatus.NOT_FOUND
        and gallery_title
        and gallery_language
        and await _check_other_languages(gallery_title, gallery_language)
    ):
        return FileStatus.IN_DIFF_LANG

    return result


async def check_file_status_gallery(gallery_info: NhentaiGallery) -> FileStatus:
    """Check if a gallery is already downloaded based on its information."""
    gallery_path = await make_gallery_path(
        gallery_title=gallery_info["title"], gallery_language=gallery_info["language"]
    )

    result = await _check_file_status(
        gallery_info["id"],
        gallery_path=gallery_path,
        gallery_title=gallery_info["title"],
        gallery_language=gallery_info["language"],
    )

    if result == FileStatus.MISSING and gallery_path.exists() and gallery_path.is_dir():
        expected_files = [
            gallery_path / f"{img_idx}.{IMAGE_TYPE_MAPPING.get(image['t'], 'jpg')}"
            for img_idx, image in enumerate(gallery_info["images"]["pages"], start=1)  # type: ignore
        ]

        if any(f.exists() for f in expected_files):
            return FileStatus.MISSING
        elif all(f.exists() for f in expected_files):
            return FileStatus.COMPLETED

    elif (
        result == FileStatus.NOT_FOUND
        and gallery_info["title"]
        and gallery_info["language"]
        and await _check_other_languages(
            gallery_info["title"], gallery_info["language"]
        )
    ):
        return FileStatus.IN_DIFF_LANG

    return result


async def _check_other_languages(
    gallery_title: ParsedMangaTitle, current_language: str
) -> bool:
    """Helper function to check if gallery exists in other languages."""
    main_title = gallery_title["main_title"]
    clean_title = remove_special_characters(main_title).lower()
    main_title_lower = main_title.lower()

    other_languages = [
        lang for lang in ("english", "japanese", "chinese") if lang != current_language
    ]

    for lang in other_languages:
        if await GalleryScanner.contains(
            lang, clean_title
        ) or await GalleryScanner.contains(lang, main_title_lower):
            return True

    return False
