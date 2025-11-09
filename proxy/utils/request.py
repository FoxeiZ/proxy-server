import html
import json
import re
import sys
from contextlib import asynccontextmanager
from copy import deepcopy
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, OrderedDict
from urllib.parse import urlparse

import httpx
from cloudscraper import exceptions as cs_exceptions
from cloudscraper.interpreters import JavaScriptInterpreter
from cloudscraper.user_agent import User_Agent
from requests.utils import cookiejar_from_dict

from ..config import Config
from ..singleton import Singleton

__all__ = ("Requests",)


class CloudflareCompat:
    def __init__(self, cloudscraper: "HttpXScraper"):
        self.cloudscraper = cloudscraper

    @staticmethod
    def is_IUAM_Challenge(resp: httpx.Response) -> bool:
        try:
            return (
                resp.headers.get("Server", "").startswith("cloudflare")
                and resp.status_code in [429, 503]
                and (
                    re.search(r"/cdn-cgi/images/trace/jsch/", resp.text, re.M | re.S)
                    is not None
                )
                and (
                    re.search(
                        r"""<form .*?="challenge-form" action="/\S+__cf_chl_f_tk=""",
                        resp.text,
                        re.M | re.S,
                    )
                    is not None
                )
            )
        except AttributeError:
            pass

        return False

    def is_New_IUAM_Challenge(self, resp: httpx.Response) -> bool:
        try:
            return (
                self.is_IUAM_Challenge(resp)
                and re.search(
                    r"""cpo.src\s*=\s*['"]/cdn-cgi/challenge-platform/\S+orchestrate/jsch/v1""",
                    resp.text,
                    re.M | re.S,
                )
                is not None
            )
        except AttributeError:
            pass

        return False

    def is_New_Captcha_Challenge(self, resp: httpx.Response) -> bool:
        try:
            return (
                self.is_Captcha_Challenge(resp)
                and re.search(
                    r"""cpo.src\s*=\s*['"]/cdn-cgi/challenge-platform/\S+orchestrate/(captcha|managed)/v1""",
                    resp.text,
                    re.M | re.S,
                )
                is not None
            )
        except AttributeError:
            pass

        return False

    @staticmethod
    def is_Captcha_Challenge(resp: httpx.Response) -> bool:
        try:
            return (
                resp.headers.get("Server", "").startswith("cloudflare")
                and resp.status_code == 403
                and (
                    re.search(
                        r"/cdn-cgi/images/trace/(captcha|managed)/",
                        resp.text,
                        re.M | re.S,
                    )
                    is not None
                )
                and (
                    re.search(
                        r"""<form .*?="challenge-form" action="/\S+__cf_chl_f_tk=""",
                        resp.text,
                        re.M | re.S,
                    )
                    is not None
                )
            )
        except AttributeError:
            pass

        return False

    @staticmethod
    def is_Firewall_Blocked(resp: httpx.Response) -> bool:
        try:
            return (
                resp.headers.get("Server", "").startswith("cloudflare")
                and resp.status_code == 403
                and (
                    re.search(
                        r'<span class="cf-error-code">1020</span>',
                        resp.text,
                        re.M | re.DOTALL,
                    )
                    is not None
                )
            )
        except AttributeError:
            pass

        return False

    def is_Challenge_Request(self, resp: httpx.Response) -> bool:
        if self.is_Firewall_Blocked(resp):
            self.cloudscraper.simpleException(
                cs_exceptions.CloudflareCode1020,
                "Cloudflare has blocked this request (Code 1020 Detected).",
            )

        if self.is_New_Captcha_Challenge(resp):
            self.cloudscraper.simpleException(
                cs_exceptions.CloudflareChallengeError,
                "Detected a Cloudflare version 2 Captcha challenge, This feature is not available in the opensource (free) version.",
            )

        if self.is_New_IUAM_Challenge(resp):
            self.cloudscraper.simpleException(
                cs_exceptions.CloudflareChallengeError,
                "Detected a Cloudflare version 2 challenge, This feature is not available in the opensource (free) version.",
            )

        if self.is_Captcha_Challenge(resp) or self.is_IUAM_Challenge(resp):
            return True

        return False

    def IUAM_Challenge_Response(self, body: str, url: httpx.URL, interpreter: str):
        try:
            formPayload = re.search(
                r'<form (?P<form>.*?="challenge-form" '
                r'action="(?P<challengeUUID>.*?'
                r'__cf_chl_f_tk=\S+)"(.*?)</form>)',
                body,
                re.M | re.DOTALL,
            )
            formPayload = formPayload.groupdict() if formPayload else {}

            if not all(key in formPayload for key in ["form", "challengeUUID"]):
                self.cloudscraper.simpleException(
                    cs_exceptions.CloudflareIUAMError,
                    "Cloudflare IUAM detected, unfortunately we can't extract the parameters correctly.",
                )

            payload = OrderedDict()
            for challengeParam in re.findall(
                r"^\s*<input\s(.*?)/>", formPayload["form"], re.M | re.S
            ):
                inputPayload = dict(re.findall(r'(\S+)="(\S+)"', challengeParam))
                if inputPayload.get("name") in ["r", "jschl_vc", "pass"]:
                    payload.update({inputPayload["name"]: inputPayload["value"]})

        except AttributeError:
            self.cloudscraper.simpleException(
                cs_exceptions.CloudflareIUAMError,
                "Cloudflare IUAM detected, unfortunately we can't extract the parameters correctly.",
            )

        try:
            payload["jschl_answer"] = JavaScriptInterpreter.dynamicImport(
                interpreter
            ).solveChallenge(body, url._uri_reference.netloc)
        except Exception as e:
            self.cloudscraper.simpleException(
                cs_exceptions.CloudflareIUAMError,
                f"Unable to parse Cloudflare anti-bots page: {getattr(e, 'message', e)}",
            )

        return {
            "url": f"{url.scheme}://{url._uri_reference.netloc}{html.unescape(formPayload['challengeUUID'])}",
            "data": payload,
        }

    async def Challenge_Response(
        self, resp: httpx.Response, **kwargs
    ) -> httpx.Response:
        if self.is_Captcha_Challenge(resp):
            if self.cloudscraper.doubleDown:
                resp = await self.cloudscraper.perform_request(
                    resp.request.method, resp.url, **kwargs
                )

            if not self.is_Captcha_Challenge(resp):
                return resp

            if (
                not self.cloudscraper.captcha
                or not isinstance(self.cloudscraper.captcha, dict)
                or not self.cloudscraper.captcha.get("provider")
            ):
                self.cloudscraper.simpleException(
                    cs_exceptions.CloudflareCaptchaProvider,
                    "Cloudflare Captcha detected, unfortunately you haven't loaded an anti Captcha provider "
                    "correctly via the 'captcha' parameter.",
                )

            if self.cloudscraper.captcha.get("provider") == "return_response":
                return resp

        submit_url = self.IUAM_Challenge_Response(
            resp.text, resp.url, self.cloudscraper.interpreter
        )

        if submit_url:

            def updateAttr(obj, name, newValue):
                try:
                    obj[name].update(newValue)
                    return obj[name]
                except (AttributeError, KeyError):
                    obj[name] = {}
                    obj[name].update(newValue)
                    return obj[name]

            cloudflare_kwargs = deepcopy(kwargs)
            cloudflare_kwargs["allow_redirects"] = False
            cloudflare_kwargs["data"] = updateAttr(
                cloudflare_kwargs, "data", submit_url["data"]
            )

            cloudflare_kwargs["headers"] = updateAttr(
                cloudflare_kwargs,
                "headers",
                {
                    "Origin": f"{resp.url.scheme}://{resp.url._uri_reference.netloc}",
                    "Referer": str(resp.url),
                },
            )

            challengeSubmitResponse = await self.cloudscraper.request(
                "POST", submit_url["url"], **cloudflare_kwargs
            )

            if challengeSubmitResponse.status_code == 400:
                self.cloudscraper.simpleException(
                    cs_exceptions.CloudflareSolveError,
                    "Invalid challenge answer detected, Cloudflare broken?",
                )

            if not challengeSubmitResponse.is_redirect:
                return challengeSubmitResponse

            else:
                cloudflare_kwargs = deepcopy(kwargs)
                cloudflare_kwargs["headers"] = updateAttr(
                    cloudflare_kwargs,
                    "headers",
                    {"Referer": challengeSubmitResponse.url},
                )

                if not urlparse(challengeSubmitResponse.headers["Location"]).netloc:
                    redirect_location = challengeSubmitResponse.url.join(
                        challengeSubmitResponse.headers["Location"]
                    )
                else:
                    redirect_location = challengeSubmitResponse.headers["Location"]

                return await self.cloudscraper.request(
                    resp.request.method, redirect_location, **cloudflare_kwargs
                )

        return await self.cloudscraper.request(resp.request.method, resp.url, **kwargs)


class HttpXScraper(httpx.AsyncClient):
    def __init__(
        self,
        *args,
        delay: float | None = None,
        captcha: dict | None = None,
        double_down: bool = True,
        interpreter: str = "native",
        debug: bool = False,
        **kwargs,
    ):
        self.debug = debug

        self.delay = delay
        self.captcha = captcha or {}
        self.doubleDown = double_down
        self.interpreter = interpreter

        self.user_agent = User_Agent(
            allow_brotli=True, browser=kwargs.pop("browser", None)
        )

        self._solveDepthCnt = 0
        self.solveDepth = kwargs.pop("solveDepth", 3)

        super(HttpXScraper, self).__init__(*args, http2=True, **kwargs)

        self.headers.update(self.user_agent.headers or {})  # type: ignore

    # compat
    @property
    def proxies(self):
        return {}

    def simpleException(self, exception, msg):
        self._solveDepthCnt = 0
        sys.tracebacklimit = 0
        raise exception(msg)

    async def perform_request(self, method, url, *args, **kwargs):
        return await super().request(method, url, *args, **kwargs)

    async def request(
        self, method: str, url: str | httpx.URL, *args, **kwargs
    ) -> httpx.Response:
        response = await self.perform_request(method, url, *args, **kwargs)

        cloudflare_challenge = CloudflareCompat(self)
        if cloudflare_challenge.is_Challenge_Request(response):
            if self._solveDepthCnt >= self.solveDepth:
                self.simpleException(
                    cs_exceptions.CloudflareLoopProtection,
                    f"!!Loop Protection!! We have tried to solve {self._solveDepthCnt} time(s) in a row.",
                )
            self._solveDepthCnt += 1

            response = await cloudflare_challenge.Challenge_Response(response, **kwargs)
        else:
            if not response.is_redirect and response.status_code not in (429, 503):
                self._solveDepthCnt = 0

        return response

    # TODO: check for missing read() before solving challenge. NOT WORKING YET
    @asynccontextmanager
    async def stream(self, method, url, **kwargs):
        async with super().stream(method, url, **kwargs) as response:
            cloudflare_challenge = CloudflareCompat(self)
            if cloudflare_challenge.is_Challenge_Request(response):
                if self._solveDepthCnt >= self.solveDepth:
                    self.simpleException(
                        cs_exceptions.CloudflareLoopProtection,
                        f"!!Loop Protection!! We have tried to solve {self._solveDepthCnt} time(s) in a row.",
                    )
                self._solveDepthCnt += 1

                # For challenges, we need to read the full response and handle it
                # Then make a new request - streaming doesn't work well with challenge responses
                await response.aread()  # Read the challenge response
                challenge_response = await cloudflare_challenge.Challenge_Response(
                    response, **kwargs
                )

                # Return the challenge response as a new stream if needed
                if challenge_response != response:
                    async with super().stream(
                        challenge_response.request.method,
                        challenge_response.url,
                        **kwargs,
                    ) as new_response:
                        yield new_response
                        return
            else:
                if not response.is_redirect and response.status_code not in (429, 503):
                    self._solveDepthCnt = 0

            yield response


# class Requests(Singleton, CloudScraper):
class Requests(Singleton, HttpXScraper):
    def __init__(self):
        super(Requests, self).__init__(
            browser={
                # "browser": "firefox",
                # "platform": "windows",
                # "mobile": False,
                "custom": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
            },
            delay=10,
            debug=False,
            interpreter="js2py",
        )
        cookies_path = Path(Config.cache_path) / "cookies.json"
        if cookies_path.exists():
            with cookies_path.open("r", encoding="utf-8") as f:
                cookies_dict = json.load(f)
            cookiejar_from_dict(cookies_dict, cookiejar=self.cookies, overwrite=True)

    def _clean_headers(self, url: httpx.URL | str, headers: dict[str, Any]) -> None:
        """Remove headers that may cause issues with proxying."""
        headers.pop("Host", None)
        headers.pop("User-Agent", None)
        headers.pop("Accept-Encoding", None)
        headers.pop("Content-Length", None)
        headers.pop("Content-Security-Policy", None)
        headers.pop("X-Content-Security-Policy", None)
        headers.pop("Remote-Addr", None)
        headers.pop("X-Forwarded-For", None)
        headers.update({"Accept-Encoding": "identity"})

        if isinstance(url, str):
            url = httpx.URL(url)

        cookies = headers.pop("Cookie", None)
        if cookies:
            print("Setting cookies from headers:", cookies)
            print("Cookies from self.cookies:", self.cookies)
            cookie = SimpleCookie(cookies)
            for key, morsel in cookie.items():
                existing_cookies = [
                    c
                    for c in self.cookies.jar
                    if c.name == key and c.domain == url._uri_reference.netloc
                ]
                if not existing_cookies:
                    self.cookies.set(
                        key,
                        morsel.value,
                        domain=url._uri_reference.netloc,
                        path=url.path,
                    )

    async def request(self, method, url, *args, **kwargs):
        if headers := kwargs.get("headers"):
            if isinstance(headers, dict):
                self._clean_headers(url, headers)

        return await super().request(method, url, **kwargs)

    def stream(self, method, url, **kwargs):
        if headers := kwargs.get("headers"):
            if isinstance(headers, dict):
                self._clean_headers(url, headers)

        return super().stream(method, url, **kwargs)
