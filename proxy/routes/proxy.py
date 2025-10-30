from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from quart import Blueprint, Response, render_template, request

from ..errors import NeedCSRF
from ..modifiers import modify_html_content, modify_js_content
from ..utils import Requests, ResourceCache
from ..utils.logger import get_logger

if TYPE_CHECKING:
    from quart import Quart


__all__ = ("register_routes",)

logger = get_logger(__name__)
bp = Blueprint("proxy", __name__)
rs_cache = ResourceCache()
requester = Requests()


def arg_to_bool(arg: str | None = None, default: bool = False) -> bool:
    """Convert a string argument to a boolean value."""
    if arg is None:
        return default
    if arg.lower() in ("true", "1", "yes"):
        return True
    elif arg.lower() in ("false", "0", "no"):
        return False
    return default


@bp.route("/<path:url>", methods=["GET", "POST"])
async def proxy(url: str) -> Response:
    """Fetches the specified URL and streams it out to the client.
    If the request was referred by the proxy itself (e.g. this is an image fetch
    for a previously proxied HTML page), then the original Referer is passed.
    """
    is_proxy_images = arg_to_bool(request.args.get("proxy_images"), False)

    if cached_response := rs_cache.get(url):
        return Response(
            cached_response[1],
            headers=cached_response[0],
            status=200,
        )

    request_data = None
    if request.method == "POST":
        content_type = request.headers.get("Content-Type", "")
        if "application/json" in content_type:
            request_data = await request.get_json()
        elif (
            "multipart/form-data" in content_type
            or "application/x-www-form-urlencoded" in content_type
        ):
            request_data = await request.form
        else:
            request_data = await request.get_data()

    target_url = "https://" + url

    response = await requester.request(
        request.method,
        target_url,
        params=request.args,
        headers=dict(request.headers),
        follow_redirects=True,
        data=request_data,
        timeout=10,
    )
    headers = response.headers.copy()
    headers.pop("Content-Encoding", None)
    headers.pop("Transfer-Encoding", None)
    headers.pop("Content-Length", None)
    headers.pop("Content-Security-Policy", None)
    headers.pop("X-Content-Security-Policy", None)
    headers.pop("Remote-Addr", None)

    content_type = headers.get("Content-Type", "")
    if "text/html" in content_type:
        if "Location" in headers:
            parts = urlparse(headers["Location"])
            if not parts.netloc:
                request_parts = urlparse(request.url)
                split_paths = request_parts.path.split("/", 3)
                if len(split_paths) < 4:
                    logger.error(
                        "invalid URL path for redirect: %s", request_parts.path
                    )
                    return Response("invalid redirect path", status=400)
                parts = parts._replace(netloc=split_paths[2])

            headers["Location"] = (
                f"/p/{parts.netloc}/{parts.path.lstrip('/')}"
                f"{'?' + parts.query if parts.query else ''}"
                f"{'#' + parts.fragment if parts.fragment else ''}"
            )

        try:
            html_content = modify_html_content(
                request_url=request.url,
                page_url=str(response.url),
                html_content=response.text,
                base_url=response.url._uri_reference.netloc,
                proxy_base=request.host_url,
                is_proxy_images=is_proxy_images,
            )
        except NeedCSRF as e:
            logger.warning("CSRF challenge detected for %s: %s", target_url, e)
            html_content = await render_template(
                "csrf.jinja2",
                error_message=str(e),
                redirect_url=request.url,
                problem_url=target_url,
                netloc=response.url._uri_reference.netloc,
            )

        return Response(
            html_content,
            headers=dict(headers),
            status=response.status_code,
        )

    elif "application/javascript" in content_type:
        modified_js = modify_js_content(request.url, response.text)
        return Response(
            modified_js,
            headers=dict(headers),
            status=response.status_code,
        )

    headers.pop("Content-Security-Policy", None)
    headers.pop("X-Content-Security-Policy", None)
    headers["Access-Control-Allow-Origin"] = "*"

    async def generate_response():
        cache = BytesIO() if response.status_code == 200 else None

        try:
            async for chunk in response.aiter_bytes(4096):
                yield chunk
                if cache:
                    cache.write(chunk)

            if cache:
                cache.seek(0)
                cache_headers = headers.copy()
                cache_headers.pop("Date", None)
                rs_cache.put(
                    url,
                    dict(cache_headers),
                    cache.getvalue(),
                    content_type=headers.get("Content-Type"),
                )

        except Exception as e:
            logger.error("error streaming response for %s: %s", url, e)
        finally:
            if cache:
                cache.close()

    return Response(
        generate_response(),
        headers={
            **headers,
            "Access-Control-Allow-Origin": "*",
        },
        status=response.status_code,
    )


def register_routes(app: Quart):
    """Register the proxy routes with the given Quart app."""
    app.register_blueprint(bp, url_prefix="/p")
