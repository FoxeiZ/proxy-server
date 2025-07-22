from __future__ import annotations

import os
from typing import TYPE_CHECKING

from quart import Blueprint, render_template, request, send_file
from quart.utils import run_sync

from ..config import Config
from ..downloader import DownloadPool
from ..utils.cache import ThumbnailCache
from ..utils.manga import GalleryScanner

if TYPE_CHECKING:
    from quart import Quart


__all__ = ("register_routes",)


bp = Blueprint("galleries", __name__)


@bp.route("/")
async def galleries_index():
    """Main galleries page."""
    page = request.args.get("page", 1, type=int)
    limit = request.args.get("limit", 20, type=int)
    language = request.args.get("language", "english", type=str)

    if page < 1:
        page = 1
    if limit < 1:
        limit = 20
    return await render_template(
        "nhentai/galleries.jinja2",
        page=page,
        cbz_files=GalleryScanner.get_gallery_paginate(language, limit=limit, page=page),
    )


@bp.route("/series/<name>")
async def gallery_series(name: str):
    """Gallery series page."""
    series = GalleryScanner.get_gallery_series(name)
    if not series:
        return "", 404
    return await render_template(
        "nhentai/gallery_series.jinja2",
        series=series,
    )


@bp.route("chapter/<int:gallery_id>")
async def gallery_detail(gallery_id: int):
    """Gallery detail page."""
    gallery = GalleryScanner.get_chapter_file(gallery_id)
    if not gallery:
        return "", 404
    return await render_template(
        "nhentai/chapter.jinja2",
        gallery=gallery,
    )


@bp.route("/thumbnail/<filename>")
async def gallery_thumbnail(filename: str):
    """Serve gallery thumbnails."""
    path = os.path.join(Config.cache_path, "thumbnails", filename)
    try:
        return await run_sync(lambda: ThumbnailCache().read(path))()
    except FileNotFoundError:
        return "", 404
    except ValueError:
        return "", 500


@bp.route("/styles.css")
async def serve_styles():
    """Serve the shared CSS file."""
    return await send_file("templates/nhentai/styles.css", mimetype="text/css")


@bp.route("/scripts.js")
async def serve_scripts():
    """Serve the shared JavaScript file."""
    return await send_file(
        "templates/nhentai/scripts.js", mimetype="application/javascript"
    )


@bp.route("/download-manager")
async def gallery_download_manager():
    """Gallery download manager."""
    return await render_template("nhentai/download_manager.jinja2")


@bp.route("/download-manager/progress")
async def gallery_download_progress():
    """Get the download progress."""
    progress = DownloadPool().get_all_progress()
    json_progress = [
        {
            "gallery_id": p.gallery_id,
            "total_images": p.total_images,
            "downloaded_images": p.downloaded_images,
            "failed_images": p.failed_images,
            "status": p.status.value,
            "progress_percentage": p.progress_percentage,
            "is_complete": p.is_complete,
        }
        for p in progress.values()
    ]
    return json_progress


def register_routes(app: Quart):
    """Register the galleries routes with the given Quart app."""
    app.register_blueprint(bp, url_prefix="/galleries")
