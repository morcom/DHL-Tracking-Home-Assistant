"""Amazon browser login (PKCE + device registration), like Alexa Media Player.

The user signs in on Amazon in their own browser (and solves any captcha or
verification step there). Amazon then redirects to ``/ap/maplanding`` with
``openid.oa2.authorization_code`` in the URL; the page itself shows an error,
which is expected. The user pastes that URL into Home Assistant.

The code is exchanged for a long-lived refresh token by registering a
device (``/auth/register``, the flow used by alexapy/Alexa Media Player and
the audible package). Before each poll the refresh token is exchanged for
fresh website cookies of the selected store (``/ap/exchangetoken/cookies``),
and the orders are read on that store. No password is stored. The device
appears in the Amazon account under "Your devices"; removing it there
revokes the login.

Orders belong to the store they were placed in. The sign-in page only
decides the account region: the European stores share one region whose
Alexa sign-in page is on amazon.de (amazon.nl has none of its own); an
amazon.com sign-in gives a US-region session the European stores reject.

These endpoints are unofficial and may change.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import secrets
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import aiohttp

_LOGGER = logging.getLogger(__name__)

_EU_SIGNIN = {"signin_domain": "amazon.de", "handle": "amzn_dp_project_dee_ios_de"}
_US_SIGNIN = {"signin_domain": "amazon.com", "handle": "amzn_dp_project_dee_ios"}

# Store (the "amazon.<tld>" the orders were placed in) -> settings.
# auth_cookie follows amazon-orders' region data (lc-acbXX -> x-acbXX);
# marketplace ids are Amazon's public marketplace ids.
STORES: dict[str, dict[str, Any]] = {
    "amazon.nl": {
        **_EU_SIGNIN,
        "auth_cookie": "x-acbnl",
        "marketplace": "A1805IZSGTT6HS",
        "language": "nl_NL",
    },
    "amazon.de": {
        **_EU_SIGNIN,
        "auth_cookie": "x-acbde",
        "marketplace": "A1PA6795UKMFR9",
        "language": "de_DE",
    },
    "amazon.com.be": {
        **_EU_SIGNIN,
        "auth_cookie": "x-acbbe",
        "marketplace": "AMEN7PMS3EDWL",
        "language": "nl_BE",
    },
    "amazon.fr": {
        **_EU_SIGNIN,
        "auth_cookie": "x-acbfr",
        "marketplace": "A13V1IB3VIYZZH",
        "language": "fr_FR",
    },
    "amazon.it": {
        **_EU_SIGNIN,
        "auth_cookie": "x-acbit",
        "marketplace": "APJ6JRA9NG5V4",
        "language": "it_IT",
    },
    "amazon.es": {
        **_EU_SIGNIN,
        "auth_cookie": "x-acbes",
        "marketplace": "A1RKKUPIHCS9HS",
        "language": "es_ES",
    },
    "amazon.co.uk": {
        **_EU_SIGNIN,
        "auth_cookie": "x-acbuk",
        "marketplace": "A1F83G8C2ARO7P",
        "language": "en_GB",
    },
    "amazon.com": {
        **_US_SIGNIN,
        "auth_cookie": "x-main",
        "marketplace": "ATVPDKIKX0DER",
        "language": "en_US",
    },
}
DEFAULT_STORE = "amazon.nl"

DEVICE_TYPE = "A2IVLV5VM2W81"  # Alexa iOS app, as used by alexapy
APP_NAME = "Parcel Tracking NL"
APP_VERSION = "2.2.556530.0"
OS_VERSION = "16.6"
TIMEOUT = 20
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


class AmazonLoginError(Exception):
    """The pasted URL or the Amazon response was not usable."""


class AmazonTokenInvalid(AmazonLoginError):
    """The refresh token was revoked or expired; the user must log in again."""


def store_info(store: str | None) -> dict[str, Any]:
    """Settings of a store (default amazon.nl)."""
    return STORES.get(store or DEFAULT_STORE, STORES[DEFAULT_STORE])


def auth_cookie(store: str | None) -> str:
    """Name of the cookie Amazon sets when signed in on the store."""
    return store_info(store)["auth_cookie"]


def maplanding_prefix(store: str | None = None) -> str:
    """Start of the address Amazon shows after the login."""
    return f"https://www.{store_info(store)['signin_domain']}/ap/maplanding?"


def register_hosts(store: str | None) -> tuple[str, ...]:
    """Device registration hosts: sign-in site first, then the store."""
    info = store_info(store)
    hosts = [f"api.{info['signin_domain']}", f"api.{store or DEFAULT_STORE}"]
    return tuple(dict.fromkeys(hosts))


def new_login(store: str = DEFAULT_STORE) -> dict[str, str]:
    """Create PKCE verifier/challenge and a device serial for one login."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return {
        "code_verifier": verifier,
        "code_challenge": challenge,
        "serial": secrets.token_hex(16).upper(),
        "store": store if store in STORES else DEFAULT_STORE,
    }


def client_id(serial: str) -> str:
    """OAuth client id derived from the device serial."""
    return (serial.encode() + b"#" + DEVICE_TYPE.encode()).hex()


def signin_url(login: dict[str, str]) -> str:
    """Return the Amazon sign-in URL the user opens in a browser."""
    info = store_info(login.get("store"))
    base = f"https://www.{info['signin_domain']}"
    params = {
        "openid.return_to": f"{base}/ap/maplanding",
        "openid.assoc_handle": info["handle"],
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "pageId": "amzn_dp_project_dee_ios",
        "accountStatusPolicy": "P1",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.mode": "checkid_setup",
        "openid.ns.oa2": "http://www.amazon.com/ap/ext/oauth/2",
        "openid.oa2.client_id": f"device:{client_id(login['serial'])}",
        "openid.ns.pape": "http://specs.openid.net/extensions/pape/1.0",
        "openid.oa2.response_type": "code",
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.pape.max_auth_age": "0",
        "openid.oa2.scope": "device_auth_access offline_access",
        "openid.oa2.code_challenge_method": "S256",
        "openid.oa2.code_challenge": login["code_challenge"],
        "language": info["language"],
        "marketPlaceId": info["marketplace"],
    }
    return f"{base}/ap/signin?{urlencode(params)}"


def code_from_redirect(url: str) -> str:
    """Extract ``openid.oa2.authorization_code`` from the pasted maplanding URL."""
    parts = urlsplit((url or "").strip())
    if "/ap/maplanding" not in parts.path:
        raise AmazonLoginError("not_maplanding")
    code = (parse_qs(parts.query).get("openid.oa2.authorization_code") or [None])[0]
    if not code:
        raise AmazonLoginError("no_code")
    return code


def _error_code(payload: Any) -> str | None:
    if isinstance(payload, dict):
        error = (payload.get("response") or {}).get("error")
        if isinstance(error, dict):
            return error.get("code")
    return None


async def _post(
    session: aiohttp.ClientSession, url: str, **kwargs: Any
) -> tuple[int, Any]:
    headers = {"Accept": "application/json", "Accept-Language": "en-GB"}
    try:
        async with asyncio.timeout(TIMEOUT):
            response = await session.post(url, headers=headers, **kwargs)
            try:
                payload = await response.json(content_type=None)
            except (ValueError, aiohttp.ContentTypeError):
                payload = None
            return response.status, payload
    except (aiohttp.ClientError, TimeoutError) as err:
        raise AmazonLoginError(f"request failed: {err}") from err


async def async_register(
    session: aiohttp.ClientSession, authorization_code: str, login: dict[str, str]
) -> dict[str, Any]:
    """Register the device; returns the refresh token and device data."""
    store = login.get("store") or DEFAULT_STORE
    body = {
        "requested_extensions": ["device_info", "customer_info"],
        "cookies": {"website_cookies": [], "domain": f".{store}"},
        "registration_data": {
            "domain": "Device",
            "app_version": APP_VERSION,
            "device_type": DEVICE_TYPE,
            "device_name": f"%FIRST_NAME%'s%DUPE_STRATEGY_1ST%{APP_NAME}",
            "os_version": OS_VERSION,
            "device_serial": login["serial"],
            "device_model": "iPhone",
            "app_name": APP_NAME,
            "software_version": "1",
        },
        "auth_data": {
            "client_id": client_id(login["serial"]),
            "authorization_code": authorization_code,
            "code_verifier": login["code_verifier"],
            "code_algorithm": "SHA-256",
            "client_domain": "DeviceLegacy",
        },
        "requested_token_type": ["bearer", "mac_dms", "website_cookies"],
    }
    last_error: str | None = None
    for host in register_hosts(store):
        status, payload = await _post(
            session, f"https://{host}/auth/register", json=body
        )
        success = ((payload or {}).get("response") or {}).get("success")
        if status == 200 and isinstance(success, dict):
            tokens = success.get("tokens") or {}
            bearer = tokens.get("bearer") or {}
            if not bearer.get("refresh_token"):
                raise AmazonLoginError("no_refresh_token")
            customer = (success.get("extensions") or {}).get("customer_info") or {}
            return {
                "refresh_token": bearer["refresh_token"],
                "serial": login["serial"],
                "device_type": DEVICE_TYPE,
                "user_id": customer.get("user_id"),
                "store": store,
            }
        last_error = _error_code(payload) or f"HTTP {status}"
        _LOGGER.debug("Amazon device registration on %s failed: %s", host, last_error)
    raise AmazonLoginError(f"registration failed: {last_error}")


async def async_exchange_cookies(
    session: aiohttp.ClientSession, refresh_token: str, store: str = DEFAULT_STORE
) -> list[dict[str, Any]]:
    """Exchange the refresh token for fresh website cookies of the store."""
    form = {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "di.sdk.version": "6.12.4",
        "source_token": refresh_token,
        "source_token_type": "refresh_token",
        "requested_token_type": "auth_cookies",
        "domain": f".{store}",
    }
    status, payload = await _post(
        session, f"https://www.{store}/ap/exchangetoken/cookies", data=form
    )
    code = _error_code(payload)
    if code in ("InvalidToken", "Unauthorized", "InvalidValue") or status in (401, 403):
        raise AmazonTokenInvalid(code or f"HTTP {status}")
    tokens = ((payload or {}).get("response") or {}).get("tokens") or {}
    cookies: list[dict[str, Any]] = []
    for domain, items in (tokens.get("cookies") or {}).items():
        for item in items or []:
            if isinstance(item, dict) and item.get("Name"):
                cookies.append(
                    {
                        "name": item["Name"],
                        "value": str(item.get("Value") or "").strip('"'),
                        "domain": domain,
                        "path": item.get("Path") or "/",
                    }
                )
    if status != 200 or not cookies:
        raise AmazonLoginError(f"cookie exchange failed: {code or f'HTTP {status}'}")
    if not any(c["name"] == auth_cookie(store) for c in cookies):
        raise AmazonTokenInvalid(f"no {auth_cookie(store)} cookie")
    return cookies


def parse_cookie_header(text: str, store: str = DEFAULT_STORE) -> list[dict[str, Any]]:
    """Parse a pasted ``Cookie`` header ("name=value; name2=value2")."""
    raw = (text or "").strip()
    if raw.lower().startswith("cookie:"):
        raw = raw[7:]
    cookies: list[dict[str, Any]] = []
    for part in raw.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name and " " not in name:
            cookies.append(
                {
                    "name": name,
                    "value": value.strip().strip('"'),
                    "domain": f".{store}",
                    "path": "/",
                }
            )
    return cookies


def store_of_cookies(cookies: list[dict[str, Any]]) -> str | None:
    """Detect the store from its sign-in cookie (x-acbnl, x-acbde, ...)."""
    names = {c["name"] for c in cookies}
    return next((s for s, info in STORES.items() if info["auth_cookie"] in names), None)


def parse_cookie_headers(text: str) -> dict[str, list[dict[str, Any]]]:
    """Parse one pasted Cookie header per line; each line is matched to its
    store by the store's sign-in cookie. Lines of unknown stores are skipped.
    """
    result: dict[str, list[dict[str, Any]]] = {}
    for line in (text or "").splitlines() or [""]:
        cookies = parse_cookie_header(line)
        store = store_of_cookies(cookies)
        if store:
            for cookie in cookies:
                cookie["domain"] = f".{store}"
            result[store] = cookies
    return result


async def async_check_orders_page(
    session: aiohttp.ClientSession,
    cookies: list[dict[str, Any]],
    store: str = DEFAULT_STORE,
) -> bool:
    """Return True when the store accepts the cookies for the order history.

    Redirects are followed (the store may redirect to a language path); the
    final page must not be the sign-in page or contain the sign-in form.
    """
    header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    try:
        async with asyncio.timeout(TIMEOUT):
            response = await session.get(
                f"https://www.{store}/your-orders/orders",
                headers={
                    "Cookie": header,
                    "Accept": "text/html",
                    "Accept-Language": "en-GB,en;q=0.8",
                    "User-Agent": BROWSER_UA,
                },
                allow_redirects=True,
            )
            final_path = response.url.path
            status = response.status
            body = await response.text(errors="replace")
    except (aiohttp.ClientError, TimeoutError) as err:
        raise AmazonLoginError(f"order page check failed: {err}") from err
    signin_form = bool(re.search(r"""<form[^>]+name=["']signIn["']""", body))
    if status != 200 or "/ap/signin" in final_path or signin_form:
        _LOGGER.warning(
            "%s order history check failed: HTTP %s, final page %s, "
            "sign-in form %s, cookies %s",
            store,
            status,
            final_path,
            signin_form,
            sorted(c["name"] for c in cookies),
        )
        return False
    return True
