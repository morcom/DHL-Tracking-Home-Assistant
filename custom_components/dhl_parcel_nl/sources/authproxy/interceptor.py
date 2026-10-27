#  SPDX-License-Identifier: Apache-2.0
#  Vendored from authcaptureproxy 1.3.7 (https://github.com/alandtse/auth_capture_proxy),
#  Copyright 2021 Alan Tse. Modified for Parcel Tracking NL: relative imports,
#  no logging of form data, cookies, headers or query strings, lazy imports.
# ruff: noqa
"""
Python Package for auth capture proxy.

Interceptor base classes for extending the proxy pipeline.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Text

import httpx
from aiohttp import web
from yarl import URL


@dataclass
class InterceptContext:
    """Mutable context passed through interceptor hooks.

    Attributes:
        request: The incoming aiohttp web request.
        proxy: Reference to the AuthCaptureProxy instance.
        access_url: The proxy's access URL.
        host_url: The target host URL.
        method: HTTP method (lowercase).
        site: Target URL string. Set by on_request to override generic URL resolution.
        data: Parsed form/post data (mutable).
        json_data: Parsed JSON body data.
        response: The httpx response (populated after HTTP request).
        is_ajax: Whether the request is an AJAX/subresource request.
        content_type: Response content type.
        body: Response body bytes (for AJAX HTML responses, mutable).
        text: Response text (for full-page HTML responses, mutable).
        short_circuit: Set to a web.Response to skip the remaining pipeline.
    """

    request: web.Request
    proxy: Any
    access_url: URL
    host_url: URL
    method: str
    site: str = ""
    data: Optional[Dict[Text, Any]] = None
    json_data: Any = None
    response: Optional[httpx.Response] = None
    is_ajax: bool = False
    content_type: str = ""
    body: Optional[bytes] = None
    text: Optional[str] = None
    short_circuit: Optional[web.Response] = None


class BaseInterceptor:
    """Base interceptor with no-op defaults.

    Subclass and override hooks to customize proxy behavior.
    Hooks are called in registration order for each interceptor.
    """

    async def on_request(self, ctx: InterceptContext) -> None:
        """Called after initial setup, before URL resolution.

        Can set ctx.site to override generic URL resolution,
        or ctx.short_circuit to return a response immediately.
        """

    async def on_request_data(self, ctx: InterceptContext) -> None:
        """Called after request body parsing, before HTTP request.

        Can modify ctx.data or ctx.json_data.
        """

    async def on_response(self, ctx: InterceptContext) -> None:
        """Called after HTTP response, before tests.

        Can inspect or log response details.
        """

    async def on_ajax_html(self, ctx: InterceptContext) -> None:
        """Called for AJAX text/html responses, before returning.

        Can modify ctx.body to inject scripts or transform content.
        """

    async def on_page_html(self, ctx: InterceptContext) -> None:
        """Called for full-page text/html responses, before modifiers.

        Can modify ctx.text to inject scripts or transform content.
        """
