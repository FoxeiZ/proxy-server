from __future__ import annotations

import uuid
from io import BytesIO
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import requests
from quart import Blueprint, Response, g, render_template, request, session
from quart.utils import run_sync

from ..errors import NeedCSRF
from ..modifiers import modify_html_content, modify_js_content
from ..utils import CFSession, SessionStore

if TYPE_CHECKING:
    from quart import Quart
    from requests import Response as RequestResponse


__all__ = ("register_routes",)


bp = Blueprint("proxy", __name__)


def arg_to_bool(arg: str | None = None, default: bool = False) -> bool:
    """Convert a string argument to a boolean value."""
    if arg is None:
        return default
    if arg.lower() in ("true", "1", "yes"):
        return True
    elif arg.lower() in ("false", "0", "no"):
        return False
    return default


@bp.before_request
def before_request():
    session_id = session.get("session_id") or str(uuid.uuid4())
    print(session_id)
    g.session = SessionStore().get(session_id)


# enforce session creation
def get_current_session() -> CFSession:
    return g.session


@bp.after_request
async def after_request(response: Response):
    session["session_id"] = get_current_session().session_id
    return response


@bp.route("/<path:url>", methods=["GET", "POST"])
async def proxy(url: str):
    """Fetches the specified URL and streams it out to the client.
    If the request was referred by the proxy itself (e.g. this is an image fetch
    for a previously proxied HTML page), then the original Referer is passed."""
    is_proxy_images = arg_to_bool(request.args.get("proxy_images"), False)

    current_session = get_current_session()
    if rc := current_session.resource_cache.get(url):
        return Response(
            rc[1],
            headers=rc[0],
            status=200,
        )

    data = await request.form if request.method == "POST" else None
    try:
        need_url = "https://" + url
        r: RequestResponse = await run_sync(
            lambda: current_session.request(
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
    except requests.RequestException:
        need_url = "http://" + url
        r: RequestResponse = await run_sync(
            lambda: current_session.request(
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

    headers = r.headers.copy()
    headers.pop("Content-Encoding", None)
    headers.pop("Transfer-Encoding", None)
    headers.pop("Content-Length", None)
    headers.pop("Content-Security-Policy", None)
    headers.pop("X-Content-Security-Policy", None)
    headers.pop("Remote-Addr", None)

    # if "Set-Cookie" in headers:
    #     cookie = headers["Set-Cookie"]
    #     cookie = cookie.replace("SameSite=Lax", "SameSite=None; Secure")
    #     regex = re.compile(r"[dD]omain=(.?\w+)+;")
    #     for match in regex.finditer(cookie):
    #         idx_end = match.end()
    #         cookie = cookie[:idx_end] + " SameSite=None; Secure;" + cookie[idx_end:]
    #     headers["Set-Cookie"] = cookie

    content_type = headers.get("Content-Type", "")
    if "text/html" in content_type:
        if "Location" in headers:
            parts = urlparse(headers["Location"])
            if not parts.netloc:
                request_parts = urlparse(request.url)
                _split_paths = request_parts.path.split("/", 3)
                if len(_split_paths) < 4:
                    raise ValueError(
                        "Invalid URL path for redirect: %s" % request_parts.path
                    )  # raise error for now, could be handled better
                parts = parts._replace(netloc=_split_paths[2])

            headers["Location"] = (
                f"/p/{parts.netloc}/{parts.path.lstrip('/')}{parts.query and '?' + parts.query or ''}{parts.fragment and '#' + parts.fragment or ''}"
            )

        parts = urlparse(r.url)
        try:
            html_content = modify_html_content(
                page_url=r.url,
                html_content=r.text,
                is_proxy_images=is_proxy_images,
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
            headers=dict(headers),
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
            current_session.resource_cache.put(url, (dict(_headers), cache.getvalue()))

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
