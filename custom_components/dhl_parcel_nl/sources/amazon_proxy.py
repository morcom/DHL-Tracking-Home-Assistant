"""Amazon login through Home Assistant (no copying), like Alexa Media Player.

The config/options flow opens ``/api/dhl_parcel_nl/amazon_login/<flow_id>``
in a new browser window. Home Assistant forwards that page to the Amazon
sign-in page (vendored authcaptureproxy), so the user logs in on the real
Amazon pages, including any captcha or verification code, which the user
solves. When Amazon redirects to ``/ap/maplanding`` the proxy takes the
``openid.oa2.authorization_code`` from that address, closes the window and
continues the flow, which registers the device (see amazon_auth.py).

Safety:
- The view is reachable without a Home Assistant login (the browser window
  has no HA token), but only for the random id of a flow that is in
  progress, for at most 15 minutes, and only until the login finished.
- Form data (the password) lives only in memory of the proxy session and is
  cleared when the login finishes; it is never stored or logged.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from aiohttp import web
from yarl import URL

from homeassistant.core import HomeAssistant
from homeassistant.helpers.http import HomeAssistantView
from homeassistant.helpers.httpx_client import create_async_httpx_client
from homeassistant.helpers.network import NoURLAvailableError, get_url

from ..const import DOMAIN
from .amazon_auth import new_login, signin_url

_LOGGER = logging.getLogger(__name__)

PROXY_PATH = f"/api/{DOMAIN}/amazon_login"
SESSION_MAX_AGE = 15 * 60
_VIEW_KEY = f"{DOMAIN}_amazon_login_view"

SUCCESS_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Amazon</title></head>
<body style="font-family:sans-serif;margin:3em">
<h2>&#10003; Amazon</h2>
<p>Login complete. You can close this window.</p>
<p>Logowanie zakończone. Możesz zamknąć to okno.</p>
<p>Inloggen voltooid. Je kunt dit venster sluiten.</p>
<script>setTimeout(function(){window.close()},1500)</script>
</body></html>"""


_CODE_RE = re.compile(r"(openid\.oa2\.(?:authorization_code|access_token)=)[^&\s\"']+")


class _RedactLoginCode(logging.Filter):
    """Mask Amazon login codes in URLs logged by httpx (INFO level)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(
                _CODE_RE.sub(r"\1***", str(a)) if "openid.oa2." in str(a) else a
                for a in (
                    record.args if isinstance(record.args, tuple) else (record.args,)
                )
            )
        if isinstance(record.msg, str) and "openid.oa2." in record.msg:
            record.msg = _CODE_RE.sub(r"\1***", record.msg)
        return True


_REDACT_FILTER = _RedactLoginCode()


def _install_log_filter() -> None:
    logger = logging.getLogger("httpx")
    if _REDACT_FILTER not in logger.filters:
        logger.addFilter(_REDACT_FILTER)


EXPIRED_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Parcel Tracking NL</title></head>
<body style="font-family:sans-serif;margin:3em">
<h2>Parcel Tracking NL - Amazon</h2>
<p>This login window is no longer valid. Start the Amazon login again in the
integration options.</p>
<p>To okno logowania jest już nieważne. Rozpocznij logowanie do Amazon ponownie
w opcjach integracji.</p>
<p>Dit inlogvenster is niet meer geldig. Start het inloggen bij Amazon opnieuw
in de opties van de integratie.</p>
</body></html>"""


class AmazonProxyLogin:
    """One browser login, bound to one config/options flow."""

    def __init__(
        self,
        hass: HomeAssistant,
        flow_id: str,
        store: str,
        is_active: Callable[[], bool],
        on_done: Callable[[], Awaitable[Any]],
    ) -> None:
        """Initialize."""
        self.hass = hass
        self.flow_id = flow_id
        self.login = new_login(store)
        self.authorization_code: str | None = None
        self._is_active = is_active
        self._on_done = on_done
        self._created = time.monotonic()
        self._proxy: Any = None

    @property
    def usable(self) -> bool:
        """Return True while the browser may use this session."""
        return (
            self.authorization_code is None
            and time.monotonic() - self._created < SESSION_MAX_AGE
            and self._is_active()
        )

    def _client(self) -> Any:
        async def _log_problem(response: Any) -> None:
            # Path and status only: queries carry login codes.
            if response.status_code >= 400:
                _LOGGER.warning(
                    "Amazon login page %s %s%s answered HTTP %s",
                    response.request.method,
                    response.request.url.host,
                    response.request.url.path,
                    response.status_code,
                )

        return create_async_httpx_client(
            self.hass,
            auto_cleanup=False,
            follow_redirects=False,
            event_hooks={"response": [_log_problem]},
        )

    def _proxy_url(self, request: web.Request) -> URL:
        """Address of this login as the browser sees it.

        Behind a reverse proxy with TLS, Home Assistant sees the request as
        plain http. Rewritten Amazon forms must post to the address the
        browser uses, otherwise the browser sends the form to http://, the
        reverse proxy redirects it and the POST turns into a GET (Amazon
        answers 404). Order: the browser's Origin/Referer for this host; the
        configured Home Assistant URL for this host (unless the browser used
        another explicit port); X-Forwarded-Proto; the raw request.
        """
        raw = URL(str(request.url))
        origin = self._browser_origin(request, raw)
        _LOGGER.debug("Amazon login proxy base: %s", origin)
        return origin.with_path(f"{PROXY_PATH}/{self.flow_id}")

    def _browser_origin(self, request: web.Request, raw: URL) -> URL:
        for header in ("Origin", "Referer"):
            value = request.headers.get(header)
            if not value:
                continue
            seen = URL(value)
            if seen.host == raw.host and seen.scheme in ("http", "https"):
                return URL(f"{seen.scheme}://{seen.raw_authority}")
        try:
            configured = URL(get_url(self.hass, require_current_request=True))
        except NoURLAvailableError:
            configured = None
        if configured is not None and configured.host == raw.host:
            if raw.explicit_port is None or raw.explicit_port == configured.port:
                return URL(f"{configured.scheme}://{configured.raw_authority}")
        proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip()
        if proto in ("http", "https") and proto != raw.scheme:
            return URL(f"{proto}://{raw.raw_host}")
        return URL(f"{raw.scheme}://{raw.raw_authority}")

    async def async_handle(self, request: web.Request) -> web.StreamResponse:
        """Forward one browser request to Amazon."""
        if self._proxy is None:
            from .authproxy import AmazonWAFInterceptor, AuthCaptureProxy

            _install_log_filter()
            self._proxy = AuthCaptureProxy(
                self._proxy_url(request),
                URL(signin_url(self.login)),
                session_factory=self._client,
            )
            self._proxy.tests = {"amazon_maplanding": self._test}
            self._proxy.interceptors = [AmazonWAFInterceptor()]
        return await self._proxy.all_handler(request)

    async def _test(self, resp: Any, data: Any, query: Any) -> URL | str | None:
        """Detect the end of the Amazon login."""
        url = URL(str(resp.url))
        if url.path == "/ap/maplanding":
            code = url.query.get("openid.oa2.authorization_code")
            if not code:
                return None
            self.authorization_code = code
            await self.async_close()
            self.hass.async_create_task(self._on_done())
            return SUCCESS_PAGE
        if url.path == "/" and self._proxy is not None:
            # Amazon sometimes lands on its home page after a captcha.
            return self._proxy.access_url()
        return None

    async def async_close(self) -> None:
        """Forget form data (password) and close the HTTP client."""
        if self._proxy is not None:
            await self._proxy.reset_data()
            await self._proxy.session.aclose()


class AmazonLoginView(HomeAssistantView):
    """Forwards the Amazon login pages of running flows."""

    url = PROXY_PATH + "/{flow_id}"
    extra_urls: ClassVar[list[str]] = [PROXY_PATH + "/{flow_id}/{tail:.*}"]
    name = f"api:{DOMAIN}:amazon_login"
    requires_auth = False

    def __init__(self) -> None:
        """Initialize."""
        self.sessions: dict[str, AmazonProxyLogin] = {}
        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            setattr(self, method, self._handle)

    async def _handle(
        self, request: web.Request, flow_id: str, tail: str = ""
    ) -> web.StreamResponse:
        session = self.sessions.get(flow_id)
        if session is None or not session.usable:
            if session is None:
                reason = "unknown or finished login"
            elif session.authorization_code is not None:
                reason = "login already finished"
            else:
                reason = "login dialog closed or older than 15 minutes"
                self.sessions.pop(flow_id, None)
                await session.async_close()
            _LOGGER.warning("Amazon login window refused: %s", reason)
            return web.Response(status=404, text=EXPIRED_PAGE, content_type="text/html")
        return await session.async_handle(request)


def async_get_view(hass: HomeAssistant) -> AmazonLoginView:
    """Register the view once and return it."""
    view = hass.data.get(_VIEW_KEY)
    if view is None:
        view = AmazonLoginView()
        hass.http.register_view(view)
        hass.data[_VIEW_KEY] = view
    return view


def start_url(hass: HomeAssistant, flow_id: str) -> str:
    """Address the flow opens; absolute when Home Assistant knows its URL."""
    path = f"{PROXY_PATH}/{flow_id}"
    for kwargs in ({"require_current_request": True}, {}):
        try:
            return get_url(hass, **kwargs) + path
        except NoURLAvailableError:
            continue
    return path
