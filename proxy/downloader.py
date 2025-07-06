# TODO: Add support for multi-volume galleries
from __future__ import annotations

import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from time import sleep
from typing import TYPE_CHECKING, Optional

import xmltodict

from .enums import DownloadStatus, FileStatus
from .singleton import Singleton
from .utils import (
    IMAGE_TYPE_MAPPING,
    SUPPORTED_IMAGE_TYPES,
    Requests,
    check_file_status_gallery,
    get_logger,
    make_gallery_path,
)
from .utils.manga import GalleryScanner  # noqa: F401

if TYPE_CHECKING:
    from ._types.nhentai import NhentaiGallery


logger = get_logger(__name__)


@dataclass
class DownloadProgress:
    gallery_id: int
    total_images: int
    downloaded_images: int = 0
    failed_images: int = 0
    status: DownloadStatus = DownloadStatus.PENDING

    @property
    def progress_percentage(self) -> float:
        if self.total_images == 0:
            return 0.0
        return (self.downloaded_images / self.total_images) * 100

    @property
    def is_complete(self) -> bool:
        return self.downloaded_images == self.total_images


class DownloadPool(Singleton):
    """A pool for managing download tasks."""

    def __init__(self, max_workers: int = 5):
        super().__init__()
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._requester = Requests()
        self._progress: dict[int, DownloadProgress] = dict()
        self._progress_lock = Lock()

    def _download(self, progress: DownloadProgress, info: "NhentaiGallery") -> None:
        """Download images for the given gallery information.

        :return: True if download was successful, False if stopped.
        """
        gallery_title = info["title"]["main_title"]
        gallery_id = info["id"]
        gallery_language = info["language"]
        if not gallery_title or not gallery_id or not gallery_language:
            logger.error(
                "Invalid gallery information: title=%s, id=%s, language=%s",
                gallery_title,
                gallery_id,
                gallery_language,
            )
            return
        # gallery_number =

        logger.info(
            "Downloading images for gallery '%s' ID: %d", gallery_title, gallery_id
        )
        progress.status = DownloadStatus.DOWNLOADING

        gallery_path = make_gallery_path(
            info["title"], gallery_language=gallery_language
        ) / str(gallery_id)
        if not gallery_path.exists():
            gallery_path.mkdir(parents=True, exist_ok=True)

        for img_idx, image in enumerate(info["images"]["pages"], start=1):
            if progress.status == DownloadStatus.CANCELLED:
                return

            image_type = IMAGE_TYPE_MAPPING.get(image.get("t", "j"))
            try:
                self._download_image(
                    f"https://i{{idx_server}}.nhentai.net/galleries/{info['media_id']}/{img_idx}.{image_type}",
                    gallery_path / f"{img_idx}.{image_type}",
                )
                self._on_download_image_complete(gallery_id)
            except Exception as e:
                self._on_download_image_error(gallery_id, e)

        GalleryScanner.add_scanned_dir(
            gallery_language,
            gallery_path.parent.name,
        )

    def _on_download_image_error(self, gallery_id: int, error: Exception):
        """Handle errors during image download."""
        with self._progress_lock:
            progress = self._progress.get(gallery_id)
            if progress:
                progress.failed_images += 1
                logger.error(
                    "Error downloading image for gallery ID %d: %s",
                    progress.gallery_id,
                    error,
                )

    def _on_download_image_complete(self, gallery_id: int):
        """Callback for when a download task is completed."""
        with self._progress_lock:
            progress = self._progress.get(gallery_id)
            if progress:
                progress.downloaded_images += 1
                if (
                    progress.downloaded_images + progress.failed_images
                    >= progress.total_images
                ):
                    if progress.failed_images == 0:
                        logger.info(
                            "All images downloaded for gallery ID %d",
                            gallery_id,
                        )
                        progress.status = DownloadStatus.COMPLETED
                    else:
                        progress.status = DownloadStatus.MISSING

    def _download_image(self, url: str, path: Path):
        if path.exists():
            logger.info("Image already exists: %s", path)
            return

        for idx_server in range(1, 10):
            formatted_url = url.format(idx_server=idx_server)
            logger.info("Downloading image from %s to %s", formatted_url, path)
            try:
                r = self._requester.get(formatted_url, stream=True, timeout=30)
                if r.status_code == 200:
                    with open(path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    logger.info("Successfully downloaded: %s", formatted_url)
                    sleep(0.1)
                    return
                else:
                    logger.warning(
                        "Failed to download image from %s: %s",
                        formatted_url,
                        r.status_code,
                    )
            except Exception as e:
                logger.error("Error downloading from %s: %s", formatted_url, e)
                continue

        raise Exception(f"Failed to download from all servers: {url}")

    def shutdown(self, wait: bool = True):
        """Shutdown the download pool and wait for all tasks to complete."""
        logger.info("Shutting down download pool...")
        self._pool.shutdown(wait=wait)
        logger.info("Download pool shutdown complete.")

    def save_cbz(self, info: "NhentaiGallery", remove_images: bool = True):
        gallery_path = make_gallery_path(
            info["title"], gallery_language=info["language"]
        )
        if not gallery_path.exists():
            logger.error("Gallery path does not exist: %s", gallery_path)
            return

        file_path = gallery_path / f"{info['id']}.cbz"
        if file_path.exists():
            logger.info("CBZ file already exists: %s", file_path)
            return

        img_dir = gallery_path / str(info["id"])
        with zipfile.ZipFile(file_path, "w") as cbz_zip:
            total_images = 0
            for img_file in img_dir.iterdir():
                if (
                    img_file.is_file()
                    and img_file.suffix.lower().lstrip(".") in SUPPORTED_IMAGE_TYPES
                ):
                    cbz_zip.write(img_file, img_file.name)
                    total_images += 1

            with cbz_zip.open("ComicInfo.xml", "w") as f:
                if info["characters"]:
                    info["characters"].insert(0, "#field-characters")
                    info["characters"].append("#end-field-characters")

                f.write(
                    xmltodict.unparse(
                        {
                            "ComicInfo": {
                                "@xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
                                "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                                "Title": info["title"]["chapter_title"],
                                "Series": (
                                    info["title"]["english_title"]
                                    or info["title"]["main_title"]
                                ),
                                "Number": info["title"]["chapter_number"],
                                "LanguageISO": (
                                    "ja"
                                    if info["language"] == "japanese"
                                    else "zh"
                                    if info["language"] == "chinese"
                                    else "en"
                                ),
                                "PageCount": total_images,
                                "Penciller": ", ".join(info["artists"]),
                                "Writer": ", ".join(info["writers"]),
                                "Translator": info["scanlator"],
                                "Tags": ", ".join(info["tags"]),
                                "Genre": ", ".join(
                                    info["characters"]
                                ),  # since characters are not available in ComicInfo.xml, use them as genre
                                "SeriesGroup": ", ".join(info["parodies"]),
                                "Web": f"https://nhentai.net/g/{info['id']}",
                            }
                        },
                        pretty=True,
                    ).encode("utf-8")
                )
            if remove_images:
                logger.info("Removing images after conversion to CBZ.")
                for img_file in img_dir.iterdir():
                    if img_file.is_file():
                        img_file.unlink()
                img_dir.rmdir()

    def add(self, info: "NhentaiGallery"):
        """Submit a download task for the given gallery information."""
        gallery_id = info["id"]

        file_status = check_file_status_gallery(gallery_info=info)
        if file_status == FileStatus.CONVERTED:
            logger.info(
                "Gallery ID %d is already converted to CBZ, skipping download.",
                gallery_id,
            )
            return

        if file_status == FileStatus.COMPLETED:
            logger.info(
                "Gallery ID %d is already downloaded, converting to CBZ.", gallery_id
            )
            # No need to track progress, just convert
            self._pool.submit(self.save_cbz, info)
            return

        # Check if already downloading
        if self.is_downloading(gallery_id):
            logger.info(
                "Gallery ID %d is already being downloaded, skipping submission.",
                gallery_id,
            )
            return

        total_images = len(info["images"]["pages"])
        progress = DownloadProgress(
            gallery_id=gallery_id,
            total_images=total_images,
            status=DownloadStatus.PENDING,
        )
        fut = self._pool.submit(self._download, progress, info)
        fut.add_done_callback(
            lambda _, info=info: self._pool.submit(self.save_cbz, info)
            if progress.status == DownloadStatus.COMPLETED
            else None
        )
        fut.add_done_callback(
            lambda _: self.remove_progress(gallery_id)
            if progress.status
            in [
                DownloadStatus.COMPLETED,
                DownloadStatus.MISSING,
                DownloadStatus.CANCELLED,
            ]
            else None
        )
        with self._progress_lock:
            self._progress[gallery_id] = progress

    def cancel(self, gallery_id: int):
        """Cancel the download for a specific gallery."""
        with self._progress_lock:
            if gallery_id not in self._progress:
                return False

            progress = self._progress[gallery_id]
            if progress.status == DownloadStatus.DOWNLOADING:
                progress.status = DownloadStatus.CANCELLED
                logger.info("Cancelled download for gallery ID %d", gallery_id)
                return True

            return False

    def get_progress(self, gallery_id: int) -> Optional[DownloadProgress]:
        """Get download progress for a specific gallery."""
        with self._progress_lock:
            return self._progress.get(gallery_id)

    def get_all_progress(self) -> dict[int, DownloadProgress]:
        """Get download progress for all galleries."""
        with self._progress_lock:
            return self._progress.copy()

    def remove_progress(self, gallery_id: int):
        """Remove progress tracking for a gallery (useful for cleanup)."""
        with self._progress_lock:
            self._progress.pop(gallery_id, None)

    def is_downloading(self, gallery_id: int) -> bool:
        """Check if a gallery is currently being downloaded."""
        with self._progress_lock:
            progress = self._progress.get(gallery_id)
            return progress is not None and progress.status in [
                DownloadStatus.PENDING,
                DownloadStatus.DOWNLOADING,
            ]
