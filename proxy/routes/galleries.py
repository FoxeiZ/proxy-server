from __future__ import annotations

import os
from typing import TYPE_CHECKING

from quart import Blueprint, render_template
from quart.utils import run_sync

from ..config import Config
from ..utils.cache import ThumbnailCache
from ..utils.manga import GalleryScanner

if TYPE_CHECKING:
    from quart import Quart


__all__ = ("register_routes",)


bp = Blueprint("galleries", __name__)


@bp.route("/")
async def galleries_index(page: int = 1, limit: int = 15):
    """Main galleries page."""
    if page < 1:
        page = 1
    if limit < 1:
        limit = 15
    return await render_template(
        "nhentai/galleries.jinja2",
        page=page,
        cbz_files=GalleryScanner.iter_gallery("english", limit=limit, page=page),
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


def register_routes(app: Quart):
    """Register the galleries routes with the given Quart app."""
    app.register_blueprint(bp, url_prefix="/galleries")
