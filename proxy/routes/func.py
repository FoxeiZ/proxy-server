from __future__ import annotations

from typing import TYPE_CHECKING

from quart import Blueprint
from quart.utils import run_sync

from ..downloader import DownloadPool
from ..enums import FileStatus
from ..modifiers.nhentai import parse_chapter
from ..utils import GalleryInfoCache, Requests, check_file_status_gallery

if TYPE_CHECKING:
    from quart import Quart


__all__ = ("register_routes",)


bp = Blueprint("func", __name__)
pool = DownloadPool()
gallery_info_cache = GalleryInfoCache()


@bp.route("/add/<int:_id>", methods=["GET"])
async def add(_id: int):
    if not _id:
        return "No ID provided for add.", 400

    info = gallery_info_cache.get(_id)
    if not info:
        r = await run_sync(lambda: Requests().get(f"https://nhentai.net/g/{_id}"))()
        if r.status_code != 200:
            return {"error": "Gallery not found"}, 404
        html_content = r.text
        info = parse_chapter(html_content)
        if not info:
            return {"error": "Failed to parse gallery information"}, 500

    file_status = check_file_status_gallery(gallery_info=info)
    if file_status in [FileStatus.CONVERTED, FileStatus.COMPLETED]:
        return {
            "message": f"Gallery already {file_status.value}",
            "gallery_id": _id,
            "status": file_status.value,
        }, 200

    if pool.is_downloading(_id):
        return {
            "message": "Gallery is already being downloaded",
            "gallery_id": _id,
        }, 200

    pool.add(info)

    return {
        "message": "Download started",
        "gallery_id": _id,
        "total_images": info["num_pages"],
    }, 200


@bp.route("/progress/<int:gallery_id>", methods=["GET"])
async def get_progress(gallery_id: int):
    """Get download progress for a specific gallery."""
    progress = pool.get_progress(gallery_id)

    if not progress:
        return {"error": "No download progress found for this gallery"}, 404

    return {
        "gallery_id": progress.gallery_id,
        "total_images": progress.total_images,
        "downloaded_images": progress.downloaded_images,
        "failed_images": progress.failed_images,
        "status": progress.status.value,
        "progress_percentage": progress.progress_percentage,
        "is_complete": progress.is_complete,
    }


@bp.route("/cancel/<int:gallery_id>", methods=["GET"])
async def cancel_download(gallery_id: int):
    """Cancel the download for a specific gallery."""
    if not pool.cancel(gallery_id):
        return {"error": "No download found for this gallery"}, 404

    return {"message": "Download cancelled successfully", "gallery_id": gallery_id}, 200


def register_routes(app: Quart):
    """Register the function routes with the given Quart app."""
    app.register_blueprint(bp, url_prefix="/func")
