from __future__ import annotations

from typing import TYPE_CHECKING

from quart import Blueprint, Response, redirect, request

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
        return redirect("/next/" + url.lstrip("/"))

    referer_parts = referer.split("/p/", maxsplit=1)
    if len(referer_parts) < 2:
        return redirect("/next/" + url.lstrip("/"))

    url = url.lstrip("/").replace("..", "")  # sanitize
    netloc = referer_parts[-1].split("/")[0]
    redirect_url = f"/p/{netloc}/{url}"
    if request.query_string:
        redirect_url += f"?{request.query_string.decode('utf-8')}"
    return Response(status=302, headers={"Location": redirect_url})


def register_routes(app: Quart):
    """Register the root routes with the given Quart app."""
    app.register_blueprint(bp, url_prefix="/")
