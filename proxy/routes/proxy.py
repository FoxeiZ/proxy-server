from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from quart import Blueprint, Response, render_template, request
from quart.utils import run_sync

from ..errors import NeedCSRF
from ..modifiers import modify_html_content, modify_js_content
from ..utils import Requests, ResourceCache

if TYPE_CHECKING:
    from quart import Quart
    from requests import Response as RequestResponse


__all__ = ("register_routes",)


bp = Blueprint("proxy", __name__)
rs_cache = ResourceCache()
requester = Requests()


@bp.route("/<path:url>", methods=["GET", "POST"])
async def proxy(url: str):
    """Fetches the specified URL and streams it out to the client.
    If the request was referred by the proxy itself (e.g. this is an image fetch
    for a previously proxied HTML page), then the original Referer is passed."""
    # Check if url to proxy has host only, and redirect with trailing slash
    # (path component) to avoid breakage for downstream apps attempting base
    # path detection
    # url_parts = urlparse("%s://%s" % (request.scheme, url))
    # if url_parts.path == "":
    #     parts = urlparse(request.url)
    #     logger.warning(
    #         "Proxy request without a path was sent, redirecting assuming '/': %s -> %s/"
    #         % (url, url)
    #     )
    #     return redirect(urlunparse(parts._replace(path=parts.path + "/")))

    if rc := rs_cache.get(url):
        return Response(
            rc[1],
            headers=rc[0],
            status=200,
        )
    data = await request.form if request.method == "POST" else None
    need_url = "https://" + url
    r: RequestResponse = await run_sync(
        lambda: requester.request(
            request.method,
            need_url,
            params=request.args,
            headers=dict(request.headers),
            allow_redirects=False,
            data=data,
            timeout=10,
            stream=True,
        ),
    )()

    headers = dict(r.headers)
    headers.pop("Content-Encoding", None)
    headers.pop("Transfer-Encoding", None)
    headers.pop("Content-Length", None)
    headers.pop("Content-Security-Policy", None)
    headers.pop("X-Content-Security-Policy", None)
    headers.pop("Remote-Addr", None)

    content_type = r.headers.get("Content-Type", "")
    if "text/html" in content_type:
        if "Location" in headers:
            parts = urlparse(headers["Location"])
            headers["Location"] = (
                f"/p/{parts.netloc}/{parts.path.lstrip('/')}{parts.query and '?' + parts.query or ''}{parts.fragment and '#' + parts.fragment or ''}"
            )
        parts = urlparse(r.url)
        try:
            html_content = modify_html_content(
                request_url=request.url,
                page_url=r.url,
                html_content=r.text,
                base_url=parts.netloc,
                proxy_base=request.host_url,
                proxy_images=(
                    request.args.get("proxy_images", "false").lower() == "true"
                ),
            )
        except NeedCSRF as e:
            html_content = await render_template(
                "csrf.jinja2",
                error_message=str(e),
                redirect_url=request.url,
                problem_url=need_url,
                netloc=parts.netloc,
            )

        return Response(
            html_content,
            headers=headers,
            status=r.status_code,
        )

    elif "application/javascript" in content_type:
        return modify_js_content(request.url, r.text)

    def generate_response():
        _headers = headers.copy()
        _headers.pop("Content-Security-Policy", None)
        _headers.pop("X-Content-Security-Policy", None)
        _headers["Access-Control-Allow-Origin"] = "*"

        is_good = r.status_code == 200
        cache = BytesIO()
        for chunk in r.iter_content(4096):
            yield chunk
            if is_good:
                cache.write(chunk)
        cache.seek(0)
        if is_good:
            rs_cache.put(url, (_headers, cache.getvalue()))

    return Response(
        generate_response(),
        headers={
            **headers,
            "Access-Control-Allow-Origin": "*",
        },
        status=r.status_code,
    )


def register_routes(app: Quart):
    """Register the proxy routes with the given Quart app."""
    app.register_blueprint(bp, url_prefix="/p")
