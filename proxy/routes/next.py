from __future__ import annotations

from typing import TYPE_CHECKING

from quart import Blueprint, Response

if TYPE_CHECKING:
    from quart import Quart


__all__ = ("register_routes",)


bp = Blueprint("next", __name__)


@bp.route("/favicon.ico")
def favicon():
    """Serve the favicon."""
    return Response(status=404)


@bp.route("/<path:url>", methods=["GET", "POST"])
async def wildcard(url: str):
    return Response(status=404)


def register_routes(app: Quart):
    """Register the next routes with the given Quart app."""
    app.register_blueprint(bp, url_prefix="/next")
