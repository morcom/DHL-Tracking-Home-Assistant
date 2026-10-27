#  SPDX-License-Identifier: Apache-2.0
#  Vendored from authcaptureproxy 1.3.7 (https://github.com/alandtse/auth_capture_proxy),
#  Copyright 2021 Alan Tse. Modified for Parcel Tracking NL: relative imports,
#  no logging of form data, cookies, headers or query strings, lazy imports.
# ruff: noqa
"""Python Package for auth capture proxy."""

import asyncio
import logging
import posixpath
import re
from functools import partial
from json import JSONDecodeError
from ssl import SSLContext
from typing import Any, Callable, Dict, List, Optional, Set, Text, Tuple, Union

import httpx
from aiohttp import MultipartReader, MultipartWriter, hdrs, web
from multidict import CIMultiDict
from yarl import URL

from .const import SKIP_AUTO_HEADERS
from .modifiers import (
    prepend_relative_urls,
    replace_empty_action_urls,
    replace_matching_urls,
)
from .helper import (
    convert_multidict_to_dict,
    get_content_type,
    get_nested_dict_keys,
    print_resp,
    run_func,
    swap_url,
)
from .interceptor import BaseInterceptor, InterceptContext
from .stackoverflow import get_open_port

# No module level SSL context (blocking); callers pass a session_factory.

_LOGGER = logging.getLogger(__name__)


class AuthCaptureProxy:
    """Class to handle proxy login connections.

    This class relies on tests to be provided to indicate the proxy has completed. At proxy completion all data can be found in self.session, self.data, and self.query.
    """

    def __init__(
        self,
        proxy_url: URL,
        host_url: URL,
        session: Optional[httpx.AsyncClient] = None,
        session_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
        preserve_headers: bool = False,
    ) -> None:
        """Initialize proxy object.

        Args:
            proxy_url (URL): url for proxy location. e.g., http://192.168.1.1/. If there is any path, the path is considered part of the base url. If no explicit port is specified, a random port will be generated. If https is passed in, ssl_context must be provided at start_proxy() or the url will be downgraded to http.
            host_url (URL): original url for login, e.g., http://example.com
            session (httpx.AsyncClient): httpx client to make queries. Optional
            session_factory (lambda: httpx.AsyncClient): factory to create the aforementioned httpx client if having one fixed session is insufficient.
            preserve_headers (bool): Whether to preserve headers from the backend. Useful in circumventing CSRF protection. Defaults to False.
        """
        self._preserve_headers = preserve_headers
        self.session_factory: Callable[[], httpx.AsyncClient] = session_factory or (
            lambda: httpx.AsyncClient()
        )
        self.session: httpx.AsyncClient = session if session else self.session_factory()
        self._proxy_url: URL = proxy_url
        self._host_url: URL = host_url
        self._port: int = proxy_url.explicit_port if proxy_url.explicit_port else 0  # type: ignore
        self.runner: Optional[web.AppRunner] = None
        self.last_resp: Optional[httpx.Response] = None
        self.init_query: Dict[Text, Any] = {}
        self.query: Dict[Text, Any] = {}
        self.data: Dict[Text, Any] = {}
        # tests and modifiers should be initialized after port is actually assigned and not during init.
        # however, to ensure defaults go first, they should have a dummy key set
        self._tests: Dict[Text, Callable] = {}
        self._modifiers: Dict[Text, Union[Callable, Dict[Text, Callable]]] = {
            "text/html": {
                "prepend_relative_urls": lambda x: x,
                "change_host_to_proxy": lambda x: x,
            }
        }
        self._old_tests: Dict[Text, Callable] = {}
        self._old_modifiers: Dict[Text, Union[Callable, Dict[Text, Callable]]] = {}
        self._active = False
        self._all_handler_active = True
        self.headers: Dict[Text, Text] = {}
        self.redirect_filters: Dict[Text, List[Text]] = {
            "url": []
        }  # dictionary of lists of regex strings to filter against
        self._background_tasks: Set[asyncio.Task] = set()
        self._interceptors: List[BaseInterceptor] = []

    @property
    def interceptors(self) -> List[BaseInterceptor]:
        """Return interceptors list.

        :setter: value (List[BaseInterceptor]): A list of interceptors to run during request processing. See :mod:`authcaptureproxy.examples.amazon_waf` for an example.
        """
        return self._interceptors

    @interceptors.setter
    def interceptors(self, value: List[BaseInterceptor]) -> None:
        """Set interceptors.

        Args:
            value (List[BaseInterceptor]): A list of interceptors.
        """
        self._interceptors = value

    @property
    def active(self) -> bool:
        """Return whether proxy is started."""
        return self._active

    @property
    def all_handler_active(self) -> bool:
        """Return whether all handler is active."""
        return self._all_handler_active

    @all_handler_active.setter
    def all_handler_active(self, value: bool) -> None:
        """Set all handler to value."""
        self._all_handler_active = value

    @property
    def port(self) -> int:
        """Return port setting."""
        return self._port

    @property
    def tests(self) -> Dict[Text, Callable]:
        """Return tests setting.

        :setter: value (Dict[Text, Any]): A dictionary of tests. The key should be the name of the test and the value should be a function or coroutine that takes a httpx.Response, a dictionary of post variables, and a dictioary of query variables and returns a URL or string. See :mod:`authcaptureproxy.examples.testers` for examples.
        """
        return self._tests

    @tests.setter
    def tests(self, value: Dict[Text, Callable]) -> None:
        """Set tests.

        Args:
            value (Dict[Text, Any]): A dictionary of tests.
        """
        self.refresh_tests()  # refresh in case of pending change
        self._old_tests = self._tests.copy()
        self._tests = value

    @property
    def modifiers(self) -> Dict[Text, Union[Callable, Dict[Text, Callable]]]:
        """Return modifiers setting.

        :setter: value (Dict[Text, Dict[Text, Callable]): A nested dictionary of modifiers. The key should be a MIME type and the value should be a dictionary of modifiers for that MIME type where the key should be the name of the modifier and the value should be a function or coroutine that takes a string and returns a modified string. If parameters are necessary, functools.partial should be used. See :mod:`authcaptureproxy.examples.modifiers` for examples.
        """
        return self._modifiers

    @modifiers.setter
    def modifiers(
        self, value: Dict[Text, Union[Callable, Dict[Text, Callable]]]
    ) -> None:
        """Set tests.

        Args:
            value (Dict[Text, Any]): A dictionary of tests.
        """
        self.refresh_modifiers()  # refresh in case of pending change
        self._old_modifiers = self._modifiers
        self._modifiers = value

    def access_url(self) -> URL:
        """Return access url for proxy with port."""
        return (
            self._proxy_url.with_port(self.port) if self.port != 0 else self._proxy_url
        )

    async def change_host_url(self, new_url: URL) -> None:
        """Change the host url of the proxy.

        This will also reset all stored data.

        Args:
            new_url (URL): original url for login, e.g., http://example.com
        """
        if not isinstance(new_url, URL):
            raise ValueError("URL required")
        self._host_url = new_url
        await self.reset_data()

    async def reset_data(self) -> None:
        """Reset all stored data.

        A proxy may need to service multiple login requests if the route is not torn down. This function will reset all data between logins.
        """
        if self.session:
            await self.session.aclose()
        self.session = self.session_factory()
        self.last_resp = None
        self.init_query = {}
        self.query = {}
        self.data = {}
        self._active = False
        self._all_handler_active = True
        _LOGGER.debug("Proxy data reset.")

    def refresh_tests(self) -> None:
        """Refresh tests.

        Because tests may use partials, they will freeze their parameters which is a problem with self.access() if the port hasn't been assigned.
        """
        if self._tests != self._old_tests:
            self.tests.update({})
            self.old_tests = self.tests.copy()
            _LOGGER.debug(
                "Refreshed %s tests: %s", len(self.tests), list(self.tests.keys())
            )

    def refresh_modifiers(self, site: Optional[URL] = None) -> None:
        """Refresh modifiers.

        Because modifiers may use partials, they will freeze their parameters which is a problem with self.access() if the port hasn't been assigned.

        Args:
            site (Optional[URL], optional): The current site. Defaults to None.
        """
        DEFAULT_MODIFIERS = {  # noqa: N806
            "prepend_relative_urls": partial(prepend_relative_urls, self.access_url()),
            "change_host_to_proxy": partial(
                replace_matching_urls,
                self._host_url.with_query({}).with_path("/"),
                self.access_url(),
            ),
        }
        if self._modifiers != self._old_modifiers:
            if self.modifiers.get("text/html") is None:
                self.modifiers["text/html"] = DEFAULT_MODIFIERS  # type: ignore
            elif self.modifiers.get("text/html") and isinstance(
                self.modifiers["text/html"], dict
            ):
                self.modifiers["text/html"].update(DEFAULT_MODIFIERS)
            if site and isinstance(self.modifiers["text/html"], dict):
                self.modifiers["text/html"].update(
                    {
                        "change_empty_to_proxy": partial(
                            replace_empty_action_urls,
                            swap_url(
                                old_url=self._host_url.with_query({}),
                                new_url=self.access_url().with_query({}),
                                url=site,
                            ),
                        ),
                    }
                )
            self._old_modifiers = self.modifiers.copy()
            refreshed_modifers = get_nested_dict_keys(self.modifiers)
            _LOGGER.debug(
                "Refreshed %s modifiers: %s",
                len(refreshed_modifers),
                refreshed_modifers,
            )

    @staticmethod
    def _filter_ajax_headers(resp: httpx.Response) -> dict:
        """Filter headers for AJAX responses, removing hop-by-hop and CSP headers."""
        _skip_headers = {
            "content-type",
            "content-length",
            "content-encoding",
            "transfer-encoding",
            "connection",
            "x-connection-hash",
            "content-security-policy",
            "content-security-policy-report-only",
        }
        filtered = {}
        for k, v in resp.headers.items():
            if k.lower() not in _skip_headers:
                filtered[k] = v
        filtered["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return filtered

    async def _build_response(
        self, response: Optional[httpx.Response] = None, *args, **kwargs
    ) -> web.Response:
        """
        Build a response.
        """
        if "headers" not in kwargs and response is not None:
            kwargs["headers"] = (
                response.headers.copy() if self._preserve_headers else CIMultiDict()
            )

            if hdrs.CONTENT_TYPE in kwargs["headers"] and "content_type" in kwargs:
                del kwargs["headers"][hdrs.CONTENT_TYPE]

            if hdrs.CONTENT_LENGTH in kwargs["headers"]:
                del kwargs["headers"][hdrs.CONTENT_LENGTH]

            if hdrs.CONTENT_ENCODING in kwargs["headers"]:
                del kwargs["headers"][hdrs.CONTENT_ENCODING]

            if hdrs.CONTENT_TRANSFER_ENCODING in kwargs["headers"]:
                del kwargs["headers"][hdrs.CONTENT_TRANSFER_ENCODING]

            if hdrs.TRANSFER_ENCODING in kwargs["headers"]:
                del kwargs["headers"][hdrs.TRANSFER_ENCODING]

            if "x-connection-hash" in kwargs["headers"]:
                del kwargs["headers"]["x-connection-hash"]

            while hdrs.SET_COOKIE in kwargs["headers"]:
                del kwargs["headers"][hdrs.SET_COOKIE]

            # cache control

            if hdrs.CACHE_CONTROL in kwargs["headers"]:
                del kwargs["headers"][hdrs.CACHE_CONTROL]

            kwargs["headers"][hdrs.CACHE_CONTROL] = (
                "no-cache, no-store, must-revalidate"
            )

        return web.Response(*args, **kwargs)

    async def all_handler(self, request: web.Request, **kwargs) -> web.Response:
        """Handle all requests.

        This handler will exit on successful test found in self.tests or if a /stop url is seen. This handler can be used with any aiohttp webserver and disabled after registered using self.all_haandler_active.

        The handler supports an interceptor pipeline for extending behavior
        without modifying core proxy code. See :class:`BaseInterceptor`.

        Args
            request (web.Request): The request to process
            **kwargs: Additional keyword arguments
                access_url (URL): The access url for the proxy. Defaults to self.access_url()
                host_url (URL): The host url for the proxy. Defaults to self._host_url

        Returns
            web.Response: The webresponse to the browser

        Raises
            web.HTTPFound: Redirect URL upon success
            web.HTTPNotFound: Return 404 when all_handler is disabled

        """
        if "access_url" in kwargs:
            access_url = kwargs.pop("access_url")
        else:
            access_url = self.access_url()

        if "host_url" in kwargs:
            host_url = kwargs.pop("host_url")
        else:
            host_url = self._host_url

        async def _process_multipart(
            reader: MultipartReader, writer: MultipartWriter
        ) -> None:
            """Process multipart.

            Args:
                reader (MultipartReader): Response multipart to process.
                writer (MultipartWriter): Multipart to write out.
            """
            while True:
                part = await reader.next()  # noqa: B305
                # https://github.com/PyCQA/flake8-bugbear/issues/59
                if part is None:
                    break
                if isinstance(part, MultipartReader):
                    await _process_multipart(part, writer)
                elif hdrs.CONTENT_TYPE in part.headers:
                    content_type = part.headers.get(hdrs.CONTENT_TYPE, "")
                    mime_type = content_type.split(";", 1)[0].strip()
                    if mime_type == "application/json":
                        try:
                            part_data: Optional[
                                Union[
                                    Text,
                                    Dict[Text, Any],
                                    List[Tuple[Text, Text]],
                                    bytes,
                                ]
                            ] = await part.json()
                            writer.append_json(part_data)
                        except (JSONDecodeError, ValueError, TypeError):
                            # Best-effort fallback: text, then bytes
                            try:
                                part_text = await part.text()
                                writer.append(part_text)
                            except ValueError:
                                part_data = await part.read()
                                writer.append(part_data)
                    elif mime_type.startswith("text"):
                        part_data = await part.text()
                        writer.append(part_data)
                    elif mime_type == "application/x-www-form-urlencoded":
                        part_data = await part.form()
                        writer.append_form(part_data)
                    else:
                        part_data = await part.read()
                        writer.append(part_data)
                else:
                    part_data = await part.read()
                    if part.name:
                        self.data.update({part.name: part_data})
                    elif part.filename:
                        part_data = await part.read()
                        self.data.update({part.filename: part_data})
                    writer.append(part_data)

        if not self.all_handler_active:
            _LOGGER.debug("%s all_handler is disabled; returning 404.", self)
            raise web.HTTPNotFound()
        method = request.method.lower()
        _LOGGER.debug("Received %s: %s", method, request.url.path)
        resp: Optional[httpx.Response] = None
        # Create interceptor context for the request pipeline
        ctx = InterceptContext(
            request=request,
            proxy=self,
            access_url=access_url,
            host_url=host_url,
            method=method,
        )
        # Run on_request interceptors (can set ctx.site for custom URL routing)
        for interceptor in self._interceptors:
            await interceptor.on_request(ctx)
            if ctx.short_circuit is not None:
                return ctx.short_circuit
        if ctx.site:
            # Interceptor set the target URL (e.g., multi-host routing)
            site = ctx.site
        else:
            # Generic URL resolution
            old_url: URL = (
                access_url.with_host(request.url.host)
                if request.url.host and request.url.host != access_url.host
                else access_url
            )
            if request.scheme == "http" and access_url.scheme == "https":
                _LOGGER.debug("Detected http while should be https; switching to https")
                site = str(
                    swap_url(
                        ignore_query=True,
                        old_url=old_url.with_scheme("https"),
                        new_url=host_url.with_path("/"),
                        url=URL(str(request.url)).with_scheme("https"),
                    ),
                )
            else:
                site = str(
                    swap_url(
                        ignore_query=True,
                        old_url=old_url,
                        new_url=host_url.with_path("/"),
                        url=URL(str(request.url)),
                    ),
                )
        self.query.update(request.query)
        data: Optional[Dict] = None
        raw_body: Optional[bytes] = None
        mpwriter = None
        if request.content_type == "multipart/form-data":
            mpwriter = MultipartWriter()
            await _process_multipart(await request.multipart(), mpwriter)
        elif (
            request.can_read_body
            and request.content_type
            and "x-www-form-urlencoded" not in request.content_type
            and "json" not in request.content_type
        ):
            # Raw body (text/plain, binary, etc.) - forward as-is.
            raw_body = await request.read()
            _LOGGER.debug(
                "Read raw body (%s bytes, type=%s) for %s",
                len(raw_body) if raw_body else 0,
                request.content_type,
                site,
            )
        else:
            data = convert_multidict_to_dict(await request.post())
        json_data = None
        # Only attempt JSON decoding for JSON requests; avoid raising for form posts.
        if request.can_read_body and (
            request.content_type == "application/json"
            or request.content_type.endswith("+json")
        ):
            try:
                json_data = await request.json()
            except (JSONDecodeError, ValueError):
                json_data = None
        if data:
            self.data.update(data)
            _LOGGER.debug("Storing form fields %s", sorted(data))
            # Run on_request_data interceptors (can modify data before HTTP request)
            ctx.site = site
            ctx.data = data
            ctx.json_data = json_data
            for interceptor in self._interceptors:
                await interceptor.on_request_data(ctx)
                if ctx.short_circuit is not None:
                    return ctx.short_circuit
            data = ctx.data
        elif json_data:
            self.data.update(json_data)
            _LOGGER.debug(
                "Storing json fields %s",
                sorted(json_data) if isinstance(json_data, dict) else "list",
            )
        if URL(str(request.url)).path == re.sub(
            r"/+", "/", self._proxy_url.with_path(f"{self._proxy_url.path}/stop").path
        ):
            self.all_handler_active = False
            if self.active:
                task = asyncio.create_task(self.stop_proxy(3))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
            return await self._build_response(text="Proxy stopped.")
        elif (
            URL(str(request.url)).path
            == re.sub(
                r"/+",
                "/",
                self._proxy_url.with_path(f"{self._proxy_url.path}/resume").path,
            )
            and self.last_resp
            and isinstance(self.last_resp, httpx.Response)
        ):
            self.init_query = self.query.copy()
            _LOGGER.debug("Resuming request")
            resp = self.last_resp
        else:
            if URL(str(request.url)).path in [
                self._proxy_url.path,
                re.sub(
                    r"/+",
                    "/",
                    self._proxy_url.with_path(f"{self._proxy_url.path}/resume").path,
                ),
            ]:
                # either base path or resume without anything to resume
                site = str(URL(host_url))
                if method == "get":
                    self.init_query = self.query.copy()
                    _LOGGER.debug(
                        "Starting auth capture proxy for %s",
                        host_url,
                    )
            headers = await self.modify_headers(URL(site), request)
            skip_auto_headers: List[str] = headers.get(SKIP_AUTO_HEADERS, [])
            if skip_auto_headers:
                _LOGGER.debug("Discovered skip_auto_headers %s", skip_auto_headers)
                headers.pop(SKIP_AUTO_HEADERS)
            # Avoid accidental header mutation across branches/calls
            req_headers: dict[str, Any] = dict(headers)
            _LOGGER.debug("Attempting %s to %s", method, URL(site).with_query(None))
            try:
                if mpwriter:
                    resp = await getattr(self.session, method)(
                        site, data=mpwriter, headers=req_headers, follow_redirects=True
                    )
                elif data:
                    resp = await getattr(self.session, method)(
                        site, data=data, headers=req_headers, follow_redirects=True
                    )
                elif raw_body is not None:
                    _LOGGER.debug(
                        "Sending raw body (%s bytes, Content-Type: %s) to %s",
                        len(raw_body),
                        request.content_type,
                        site,
                    )
                    # Preserve the original Content-Type for raw body requests
                    if request.content_type and "Content-Type" not in req_headers:
                        req_headers["Content-Type"] = request.content_type
                    resp = await getattr(self.session, method)(
                        site,
                        content=raw_body,
                        headers=req_headers,
                        follow_redirects=True,
                    )
                elif json_data:
                    for item in [
                        "Host",
                        "Origin",
                        "User-Agent",
                        "dnt",
                        "Accept-Encoding",
                    ]:
                        # remove proxy headers
                        if req_headers.get(item):
                            req_headers.pop(item)
                    resp = await getattr(self.session, method)(
                        site, json=json_data, headers=req_headers, follow_redirects=True
                    )
                else:
                    resp = await getattr(self.session, method)(
                        site, headers=req_headers, follow_redirects=True
                    )
            except httpx.ConnectError as ex:
                return await self._build_response(
                    text=f"Error connecting to {site}; please retry: {ex}"
                )
            except httpx.TooManyRedirects as ex:
                return await self._build_response(
                    text=f"Error connecting to {site}; too many redirects: {ex}"
                )
            except httpx.TimeoutException as ex:
                _LOGGER.warning("Timeout connecting to %s: %s", site, ex)
                return await self._build_response(
                    text=(
                        f"Timeout connecting to {site}: {ex}. "
                        "Please try again. If this persists, check your network "
                        "and that the service endpoint is reachable from this host."
                    )
                )
            except httpx.HTTPError as ex:
                return await self._build_response(
                    text=f"Error connecting to {site}: {ex}"
                )
        if resp is None:
            return await self._build_response(
                text=f"Error connecting to {site}; please retry"
            )
        self.last_resp = resp
        print_resp(resp)
        # Run on_response interceptors (post-response, pre-tests)
        ctx.response = resp
        ctx.site = site
        for interceptor in self._interceptors:
            await interceptor.on_response(ctx)
            if ctx.short_circuit is not None:
                return ctx.short_circuit
        self.check_redirects()
        self.refresh_tests()
        if self.tests:
            for test_name, test in self.tests.items():
                result = None
                result = await run_func(test, test_name, resp, self.data, self.query)
                if result:
                    _LOGGER.debug("Test %s triggered", test_name)
                    if isinstance(result, URL):
                        _LOGGER.debug("Redirecting to callback")
                        raise web.HTTPFound(location=result)
                    elif isinstance(result, str):
                        _LOGGER.debug("Displaying test result page")
                        return await self._build_response(
                            resp, text=result, content_type="text/html"
                        )
        else:
            _LOGGER.warning("Proxy has no tests; please set.")
        content_type = get_content_type(resp)
        # Detect AJAX requests using Fetch Metadata headers (W3C standard).
        # Sec-Fetch-Mode is set by the browser and cannot be spoofed by JS.
        # 'navigate' = top-level page navigation; anything else = AJAX/subresource.
        # Fall back to Upgrade-Insecure-Requests for older clients.
        _sec_fetch_mode = request.headers.get("Sec-Fetch-Mode")
        if _sec_fetch_mode is not None:
            _is_ajax = _sec_fetch_mode != "navigate"
        else:
            # Legacy fallback for clients without Sec-Fetch-Mode
            _is_ajax = request.headers.get("Upgrade-Insecure-Requests") != "1"
        if _is_ajax:
            _LOGGER.debug(
                "AJAX response for %s: status=%s, content_type=%s",
                URL(str(request.url)).path,
                resp.status_code,
                content_type,
            )
        if _is_ajax and content_type == "text/html":
            _ajax_body = resp.content
            # Run on_ajax_html interceptors (can modify AJAX HTML body)
            ctx.is_ajax = True
            ctx.content_type = content_type
            ctx.body = _ajax_body
            for interceptor in self._interceptors:
                await interceptor.on_ajax_html(ctx)
            _ajax_body = ctx.body if ctx.body is not None else _ajax_body
            _LOGGER.debug(
                "AJAX HTML response for %s - skipping modifiers",
                URL(str(request.url)).path,
            )
            # Forward original headers for AJAX responses.
            # Client-side JavaScript may check response headers (e.g., for CAPTCHA
            # initialization). Without them, it may fail silently.
            _ajax_headers = self._filter_ajax_headers(resp) if resp is not None else {}
            return await self._build_response(
                resp,
                body=_ajax_body,
                content_type=content_type,
                headers=_ajax_headers,
            )
        # Also skip modifiers for non-HTML AJAX responses (JSON, binary, etc.)
        if _is_ajax and content_type != "text/html":
            _LOGGER.debug(
                "AJAX non-HTML response (%s) for %s - skipping modifiers",
                content_type,
                URL(str(request.url)).path,
            )
            _resp_body = resp.content
            _ajax_headers_nh = (
                self._filter_ajax_headers(resp) if resp is not None else {}
            )
            return await self._build_response(
                resp,
                body=_resp_body,
                content_type=content_type,
                headers=_ajax_headers_nh,
            )
        self.refresh_modifiers(URL(str(resp.url)))
        if self.modifiers:
            modified: bool = False
            if (
                content_type != "text/html"
                and content_type not in self.modifiers.keys()
            ):
                text: Text = ""
            elif content_type != "text/html" and content_type in self.modifiers.keys():
                text = resp.text
            else:
                text = resp.text
            if not isinstance(text, str):  # process aiohttp text
                text = await resp.text()
            # Resolve relative form actions BEFORE modifiers run.
            if text and content_type == "text/html" and resp and resp.url:
                _resp_url = URL(str(resp.url))
                _resp_dir = (
                    _resp_url.path.rsplit("/", 1)[0] + "/"
                    if "/" in _resp_url.path
                    else "/"
                )

                def _resolve_form_action(form_match):
                    """Resolve relative action URLs only inside <form> tags."""
                    form_tag = form_match.group(0)
                    action_m = re.search(
                        r'(\s+action=["\'])([^"\']*?)(["\'])', form_tag
                    )
                    if not action_m:
                        return form_tag
                    action = action_m.group(2)
                    if action and not action.startswith(
                        ("http://", "https://", "//", "#", "javascript:", "/")
                    ):
                        resolved_path = posixpath.normpath(_resp_dir + action)
                        _proxy_base = self.access_url().path.rstrip("/")
                        abs_url = str(
                            self.access_url()
                            .with_path(_proxy_base + resolved_path)
                            .with_query({})
                        )
                        _LOGGER.debug(
                            "Resolved relative form action '%s' -> '%s' (page: %s)",
                            action,
                            abs_url,
                            _resp_url.path,
                        )
                        return (
                            form_tag[: action_m.start(2)]
                            + abs_url
                            + form_tag[action_m.end(2) :]
                        )
                    return form_tag

                text = re.sub(
                    r"<form\b[^>]*>",
                    _resolve_form_action,
                    text,
                    flags=re.IGNORECASE,
                )
            # Run on_page_html interceptors (can inject scripts before modifiers)
            if text and content_type == "text/html":
                ctx.text = text
                ctx.content_type = content_type
                ctx.is_ajax = False
                for interceptor in self._interceptors:
                    await interceptor.on_page_html(ctx)
                text = ctx.text if ctx.text is not None else text
            if text:
                for name, modifier in self.modifiers.items():
                    if isinstance(modifier, dict):
                        if name != content_type:
                            continue
                        for sub_name, sub_modifier in modifier.items():
                            try:
                                text = await run_func(sub_modifier, sub_name, text)
                                modified = True
                            except TypeError as ex:
                                _LOGGER.warning(
                                    "Modifier %s is not callable: %s", sub_name, ex
                                )
                    else:
                        # default run against text/html only
                        if content_type == "text/html":
                            try:
                                text = await run_func(modifier, name, text)
                                modified = True
                            except TypeError as ex:
                                _LOGGER.warning(
                                    "Modifier %s is not callable: %s", name, ex
                                )
            if modified:
                return await self._build_response(
                    resp,
                    text=text,
                    content_type=content_type,
                )
        # pass through non parsed content
        _LOGGER.debug(
            "Passing through %s as %s",
            URL(str(request.url)).name
            if URL(str(request.url)).name
            else URL(str(request.url)).path,
            content_type,
        )
        return await self._build_response(
            resp, body=resp.content, content_type=content_type
        )

    async def start_proxy(
        self, host: Optional[Text] = None, ssl_context: Optional[SSLContext] = None
    ) -> None:
        """Start proxy.

        Args:
            host (Optional[Text], optional): The host interface to bind to. Defaults to None which is "0.0.0.0" all interfaces.
            ssl_context (Optional[SSLContext], optional): SSL Context for the server. Defaults to None.
        """
        app = web.Application()
        app.add_routes(
            [
                web.route("*", "/{tail:.*}", self.all_handler),
            ]
        )
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        if not self.port:
            self._port = get_open_port()
        if self._proxy_url.scheme == "https" and ssl_context is None:
            _LOGGER.debug(
                "Proxy url is https but no SSL Context set, downgrading to http"
            )
            self._proxy_url = self._proxy_url.with_scheme("http")
        site = web.TCPSite(
            runner=self.runner, host=host, port=self.port, ssl_context=ssl_context
        )
        await site.start()
        self._active = True
        _LOGGER.debug("Started proxy at %s", self.access_url())

    async def stop_proxy(self, delay: int = 0) -> None:
        """Stop proxy server.

        Args:
            delay (int, optional): How many seconds to delay. Defaults to 0.
        """
        if not self.active:
            _LOGGER.debug("Proxy is not started; ignoring stop command")
            return
        _LOGGER.debug("Stopping proxy at %s after %s seconds", self.access_url(), delay)
        await asyncio.sleep(delay)
        _LOGGER.debug("Closing site runner")
        if self.runner:
            await self.runner.cleanup()
            await self.runner.shutdown()
        _LOGGER.debug("Site runner closed")
        # close session
        if self.session:
            _LOGGER.debug("Closing session")
            await self.session.aclose()
            _LOGGER.debug("Session closed")
        self._active = False
        _LOGGER.debug("Proxy stopped")

    def _swap_proxy_and_host(self, text: Text, domain_only: bool = False) -> Text:
        """Replace host with proxy address or proxy with host address.

        Args
            text (Text): text to replace
            domain (bool): Whether only the domains should be swapped.

        Returns
            Text: Result of replacing

        """
        host_string: Text = str(self._host_url.with_path("/"))
        proxy_string: Text = str(
            self.access_url() if not domain_only else self.access_url().with_path("/")
        )
        if str(self.access_url().with_path("/")).replace("https", "http") in text:
            _LOGGER.debug(
                "Replacing %s with %s",
                str(self.access_url().with_path("/")).replace("https", "http"),
                str(self.access_url().with_path("/")),
            )
            text = text.replace(
                str(self.access_url().with_path("/")).replace("https", "http"),
                str(self.access_url().with_path("/")),
            )
        if proxy_string in text:
            if host_string[-1] == "/" and (
                not proxy_string or proxy_string == "/" or proxy_string[-1] != "/"
            ):
                proxy_string = f"{proxy_string}/"
            _LOGGER.debug("Replacing proxy url with host url")
            return text.replace(proxy_string, host_string)
        elif host_string in text:
            if host_string[-1] == "/" and (
                not proxy_string or proxy_string == "/" or proxy_string[-1] != "/"
            ):
                proxy_string = f"{proxy_string}/"
            _LOGGER.debug("Replacing %s with %s", host_string, proxy_string)
            return text.replace(host_string, proxy_string)
        else:
            _LOGGER.debug("Unable to find host or proxy url")
            return text

    async def modify_headers(self, site: URL, request: web.Request) -> dict:
        """Modify headers.

        Return modified headers based on site and request. To disable auto header generation,
        pass in to the header a key const.SKIP_AUTO_HEADERS with a list of keys to not generate.

        For example, to prevent User-Agent generation: {SKIP_AUTO_HEADERS : ["User-Agent"]}

        Args:
            site (URL): URL of the next host request.
            request (web.Request): Proxy directed request. This will need to be changed for the actual host request.

        Returns:
            dict: Headers after modifications
        """
        result: Dict[str, Any] = {}
        result.update(request.headers)
        # _LOGGER.debug("Original headers %s", headers)
        if result.get("Host"):
            result.pop("Host")
        if result.get("Origin"):
            # Use the configured host URL as Origin for cross-origin requests.
            # Third-party services may validate Origin against the page that loaded them.
            result["Origin"] = f"{self._host_url.with_path('')}"
        # remove any cookies in header received from browser. If not removed, httpx will not send session cookies
        if result.get("Cookie"):
            result.pop("Cookie")
        if result.get("Referer") and (
            URL(result.get("Referer", "")).query == self.init_query
            or URL(result.get("Referer", "")).path
            == "/config/integrations"  # home-assistant referer
        ):
            # Change referer for starting request; this may have query items we shouldn't pass
            result["Referer"] = str(self._host_url)
        elif result.get("Referer"):
            result["Referer"] = self._swap_proxy_and_host(
                result.get("Referer", ""), domain_only=True
            )
        for item in [
            "Content-Length",
            "X-Forwarded-For",
            "X-Forwarded-Host",
            "X-Forwarded-Port",
            "X-Forwarded-Proto",
            "X-Forwarded-Scheme",
            "X-Forwarded-Server",
            "X-Real-IP",
        ]:
            # remove proxy headers
            if result.get(item):
                result.pop(item)
        result.update(self.headers if self.headers else {})
        return result

    def check_redirects(self) -> None:
        """Change host if redirect detected and regex does not match self.redirect_filters.

        Self.redirect_filters is a dict with key as attr in resp and value as list of regex expressions to filter against.
        """
        if not self.last_resp:
            return
        resp: httpx.Response = self.last_resp
        if resp.history:
            for item in resp.history:
                if (
                    item.status_code in [301, 302, 303, 304, 305, 306, 307, 308]
                    and item.url
                    and resp.url
                    and resp.url.host != self._host_url.host
                ):
                    filtered = False
                    for attr, regex_list in self.redirect_filters.items():
                        if getattr(resp, attr) and list(
                            filter(
                                lambda regex_string: re.search(
                                    regex_string, str(getattr(resp, attr))
                                ),
                                regex_list,
                            )
                        ):
                            _LOGGER.debug(
                                "Check_redirects: Filtered out on %s in %s for resp attribute %s",
                                list(
                                    filter(
                                        lambda regex_string: re.search(
                                            regex_string, str(getattr(resp, attr))
                                        ),
                                        regex_list,
                                    )
                                ),
                                str(getattr(resp, attr)),
                                attr,
                            )
                            filtered = True
                    if filtered:
                        return
                    _LOGGER.debug(
                        "Detected %s redirect from %s to %s; changing proxy host",
                        item.status_code,
                        item.url.host,
                        resp.url.host,
                    )
                    self._host_url = self._host_url.with_host(resp.url.host)
