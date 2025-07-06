from __future__ import annotations

from typing import TYPE_CHECKING

from quart import Blueprint, Response, request

if TYPE_CHECKING:
    from quart import Quart


__all__ = ("register_routes",)


bp = Blueprint("root", __name__)


@bp.route("/<path:url>", methods=["GET", "POST"])
async def root(url: str):
    """Redirects relative paths to the proxy URL."""
    # If referred from a proxy request, then redirect to a URL with the proxy prefix.
    # This allows server-relative and protocol-relative URLs to work.
    referer = request.headers.get("referer")
    if not referer:
        return Response(status=404)

    netloc = referer.split("/p/")[-1].split("/")[0]
    redirect_url = f"/p/{netloc}/{url}"
    if request.query_string:
        redirect_url += f"?{request.query_string.decode('utf-8')}"
    return Response(status=302, headers={"Location": redirect_url})


def register_routes(app: Quart):
    """Register the root routes with the given Quart app."""
    app.register_blueprint(bp, url_prefix="/")
