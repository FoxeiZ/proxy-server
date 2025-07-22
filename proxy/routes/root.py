from __future__ import annotations

from http.cookies import SimpleCookie
from typing import TYPE_CHECKING

from quart import Blueprint, Response, redirect, render_template, request

from ..utils import Requests

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


@bp.route("/csrf", methods=["GET", "POST"])
async def csrf():
    """Handle CSRF token submission and display form."""
    if request.method == "GET":
        error_message = request.args.get("error_message")
        redirect_url = request.args.get("redirect_url")
        problem_url = request.args.get("problem_url")
        netloc = request.args.get("netloc")
        return await render_template(
            "csrf.jinja2",
            error_message=error_message,
            redirect_url=redirect_url,
            problem_url=problem_url,
            netloc=netloc,
        )

    form = await request.form
    cookie_string = form.get("cf_clearance", "")
    netloc = form.get("netloc", "")
    redirect_url = form.get("redirect_url", "")
    if not cookie_string or not netloc:
        return Response("Cookie string and netloc are required", status=400)

    simple_cookie = SimpleCookie()
    try:
        simple_cookie.load(cookie_string)
    except Exception:
        return redirect(
            f"/csrf?error_message=Invalid cookie format&redirect_url={redirect_url}&problem_url={form.get('problem_url', '')}&netloc={netloc}"
        )

    if "cf_clearance" not in simple_cookie:
        return redirect(
            f"/csrf?error_message=cf_clearance cookie not found in the provided cookies&redirect_url={redirect_url}&problem_url={form.get('problem_url', '')}&netloc={netloc}"
        )

    cookies = Requests().cookies
    cookies.set("cf_clearance", simple_cookie["cf_clearance"].value, domain=netloc)
    for key in ("csrftoken", "sessionid", "session-affinity"):
        if key in simple_cookie:
            cookies.set(key, simple_cookie[key].value, domain=netloc)

    return redirect(redirect_url)


def register_routes(app: Quart):
    """Register the root routes with the given Quart app."""
    app.register_blueprint(bp, url_prefix="/")
