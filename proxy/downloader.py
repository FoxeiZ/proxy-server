from __future__ import annotations

import asyncio
import zipfile
from asyncio import Lock as AsyncLock
from asyncio import Semaphore
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, AsyncGenerator, Iterable, Optional

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
from .utils.xml import XMLIOWriter

if TYPE_CHECKING:
    from ._types.nhentai import NhentaiGallery


logger = get_logger(__name__)


@dataclass
class DownloadProgress:
    gallery_id: int
    total_images: int
    gallery_title: str = ""
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


class DownloadProgressWithLock:
    def __init__(self, *args, **kwargs):
        self._lock = AsyncLock()
        self._progress = DownloadProgress(*args, **kwargs)

    @asynccontextmanager
    async def context_lock(self):
        async with self._lock:
            yield self._progress

    # which one is better? both are fine i guess

    async def __aenter__(self):
        await self._lock.acquire()
        return self._progress

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._lock.release()


class DownloadPool(Singleton):
    """A pool for managing download tasks."""

    def __init__(self, max_workers: int = 5) -> None:
        super().__init__()
        self._semaphore = Semaphore(max_workers)
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._requester = Requests()
        self._progress: dict[int, DownloadProgressWithLock] = {}
        self._lock = AsyncLock()
        self._tasks: dict[int, asyncio.Task] = {}

    async def _download(
        self, progress_ctx: DownloadProgressWithLock, info: "NhentaiGallery"
    ) -> None:
        """Download images for the given gallery information."""
        async with self._semaphore:
            gallery_title = info["title"]["main_title"]
            gallery_id = info["id"]
            gallery_language = info["language"]

            if not gallery_title or not gallery_id or not gallery_language:
                logger.error(
                    "invalid gallery information: title=%s, id=%s, language=%s",
                    gallery_title,
                    gallery_id,
                    gallery_language,
                )
                return

            logger.info(
                "downloading images for gallery '%s' ID: %d", gallery_title, gallery_id
            )
            async with progress_ctx.context_lock() as progress:
                progress.status = DownloadStatus.DOWNLOADING

            gallery_path, _ = await make_gallery_path(
                gallery_title=info["title"],
                gallery_language=gallery_language,
                cache=True,
            )
            gallery_path = gallery_path / str(gallery_id)
            await asyncio.to_thread(gallery_path.mkdir, exist_ok=True, parents=True)

            download_tasks = []
            try:
                for img_idx, image in enumerate(info["images"]["pages"], start=1):
                    async with progress_ctx.context_lock() as progress:
                        if progress.status == DownloadStatus.CANCELLED:
                            break

                    image_type = IMAGE_TYPE_MAPPING.get(image.get("t", "j"))
                    url = f"https://i{{idx_server}}.nhentai.net/galleries/{info['media_id']}/{img_idx}.{image_type}"
                    path = gallery_path / f"{img_idx}.{image_type}"

                    task = self._download_image(url, path, gallery_id)
                    download_tasks.append(task)

                if download_tasks:
                    results = await asyncio.gather(
                        *download_tasks, return_exceptions=True
                    )
                    for result in results:
                        if isinstance(result, Exception):
                            logger.error("download task failed: %s", result)
            except asyncio.CancelledError:
                async with progress_ctx.context_lock() as progress:
                    progress.status = DownloadStatus.CANCELLED
                    logger.warning(
                        "download interrupted for gallery ID %d, waiting for tasks to finish",
                        progress.gallery_id,
                    )
                if download_tasks:
                    await asyncio.gather(*download_tasks, return_exceptions=True)
                raise

    async def _on_download_image_error(self, gallery_id: int, error: Exception):
        """Handle errors during image download."""
        async with self._lock:
            progress_ctx = self._progress.get(gallery_id)

        if not progress_ctx:
            return

        async with progress_ctx.context_lock() as progress:
            progress.failed_images += 1
            logger.error(
                "error downloading image for gallery ID %d: %s",
                progress.gallery_id,
                error,
            )

    async def _on_download_image_complete(self, gallery_id: int):
        """Callback for when a download task is completed."""
        async with self._lock:
            progress_ctx = self._progress.get(gallery_id)

        if not progress_ctx:
            return

        async with progress_ctx.context_lock() as progress:
            progress.downloaded_images += 1
            if (
                progress.downloaded_images + progress.failed_images
                >= progress.total_images
            ):
                if progress.failed_images == 0:
                    logger.info("all images downloaded for gallery ID %d", gallery_id)
                    progress.status = DownloadStatus.COMPLETED
                else:
                    progress.status = DownloadStatus.MISSING

    async def _download_image(self, url: str, path: Path, gallery_id: int):
        if await asyncio.to_thread(path.exists):
            logger.debug("image already exists: %s", path)
            await self._on_download_image_complete(gallery_id)
            return

        def _chunked_write(fp: Path, chunk: Iterable[bytes]):
            with open(fp, "wb") as f:
                for data in chunk:
                    f.write(data)

        for idx_server in range(1, 10):
            formatted_url = url.format(idx_server=idx_server)
            try:
                async with self._requester.stream(
                    "GET", formatted_url, timeout=30
                ) as response:
                    if response.status_code == 200:
                        chunks = []
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            chunks.append(chunk)

                        await asyncio.to_thread(_chunked_write, path, chunks)

                        logger.debug("successfully downloaded: %s", formatted_url)
                        await self._on_download_image_complete(gallery_id)
                        return
                    else:
                        logger.warning(
                            "failed to download image from %s: %s",
                            formatted_url,
                            response.status_code,
                        )
            except Exception as e:
                logger.error("error downloading from %s: %s", formatted_url, e)
                continue

        await self._on_download_image_error(
            gallery_id, Exception(f"Failed to download from all servers: {url}")
        )

    async def _update_progress_and_task(
        self,
        gallery_id: int,
        progress_ctx: DownloadProgressWithLock,
        task: asyncio.Task,
    ):
        async with self._lock:
            self._progress[gallery_id] = progress_ctx
            self._tasks[gallery_id] = task

    async def _remove_progress_and_task(self, gallery_id: int):
        async with self._lock:
            self._progress.pop(gallery_id, None)
            self._tasks.pop(gallery_id, None)

    async def shutdown(self, wait: bool = True):
        """Shutdown the download pool and cancel all running tasks."""
        logger.info("shutting down download pool...")

        async with self._lock:
            active_tasks = [task for task in self._tasks.values() if not task.done()]
            for task in active_tasks:
                task.cancel()
            self._tasks.clear()

        if wait and active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)

        logger.info("download pool shutdown complete")

    def _sync_save_cbz(
        self, info: NhentaiGallery, gallery_path: Path, remove_images: bool = True
    ):
        # gallery_path, scan_callback = make_gallery_path(
        #     gallery_title=info["title"], gallery_language=info["language"], cache=True
        # )
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

                xml_writer = XMLIOWriter()
                xml_writer.from_gallery_info(info, folder=img_dir.parent.name)
                xml_writer.write_to_file(f)

            if remove_images:
                logger.info("Removing images after conversion to CBZ.")
                for img_file in img_dir.iterdir():
                    if img_file.is_file():
                        img_file.unlink()
                img_dir.rmdir()

    async def save_cbz(self, info: "NhentaiGallery", remove_images: bool = True):
        loop = asyncio.get_running_loop()
        gallery_path, scan_callback = await make_gallery_path(
            gallery_title=info["title"], gallery_language=info["language"], cache=True
        )
        await loop.run_in_executor(
            self._executor,
            self._sync_save_cbz,
            info,
            gallery_path,
            remove_images,
        )
        await scan_callback()

    async def add(self, info: NhentaiGallery):
        """Submit a download task for the given gallery information."""
        gallery_id = info["id"]

        file_status = await check_file_status_gallery(gallery_info=info)
        if file_status == FileStatus.CONVERTED:
            logger.info(
                "gallery ID %d is already converted to CBZ, skipping download",
                gallery_id,
            )
            return
        elif file_status == FileStatus.COMPLETED:
            logger.info(
                "gallery ID %d is already downloaded, converting to CBZ", gallery_id
            )
            await self.save_cbz(info)
            return

        # is_downloading is already locked internally so we cant double lock here
        if await self.is_downloading(gallery_id):
            logger.info(
                "gallery ID %d is already being downloaded, skipping submission",
                gallery_id,
            )
            return

        total_images = len(info["images"]["pages"])
        progress_ctx = DownloadProgressWithLock(
            gallery_id=gallery_id,
            gallery_title=info["title"]["main_title"],
            total_images=total_images,
            status=DownloadStatus.PENDING,
        )

        task = asyncio.create_task(self._download_task(progress_ctx, info))
        await self._update_progress_and_task(gallery_id, progress_ctx, task)

    async def _download_task(
        self, progress_ctx: DownloadProgressWithLock, info: "NhentaiGallery"
    ):
        try:
            await self._download(progress_ctx, info)
        finally:
            async with progress_ctx.context_lock() as progress:
                if progress.status == DownloadStatus.COMPLETED:
                    await self.save_cbz(info)
                await self._remove_progress_and_task(progress.gallery_id)

    async def cancel(self, gallery_id: int) -> bool:
        async with self._lock:
            progress_ctx = self._progress.get(gallery_id, None)
            if not progress_ctx:
                return False

        async with progress_ctx.context_lock() as progress_ctx:
            if progress_ctx.status == DownloadStatus.DOWNLOADING:
                progress_ctx.status = DownloadStatus.CANCELLED

                task = self._tasks.get(gallery_id)
                if task:
                    task.cancel()

                logger.info("cancelled download for gallery ID %d", gallery_id)
                return True

            return False

    async def get_progress(self, gallery_id: int) -> Optional[DownloadProgressWithLock]:
        """Get download progress for a specific gallery."""
        async with self._lock:
            return self._progress.get(gallery_id)

    async def get_paginate_progress(
        self, page: int = 1, limit: int = 10
    ) -> AsyncGenerator[DownloadProgress]:
        """Get paginated download progress."""
        async with self._lock:
            all_progress = list(self._progress.values())
            if not all_progress:
                return

            start = (page - 1) * limit
            if start >= len(all_progress):
                return
            end = start + limit

            for progress_ctx in all_progress[start:end]:
                async with progress_ctx.context_lock() as progress:
                    yield progress

    async def is_downloading(self, gallery_id: int) -> bool:
        """Check if a gallery is currently being downloaded."""
        async with self._lock:
            progress_ctx = self._progress.get(gallery_id)

        if not progress_ctx:
            return False

        async with progress_ctx.context_lock() as progress:
            return progress.status in [
                DownloadStatus.PENDING,
                DownloadStatus.DOWNLOADING,
            ]
