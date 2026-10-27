"""Amazon orders source (experimental, read-only).

Reads the orders of every selected Amazon store (amazon.nl, .de, .fr, .es,
...). Orders exist only in the store they were placed in, so each store is
read separately. Uses the ``amazon-orders`` library, installed on demand
only when the user enables this source.

Logins: browser login (see amazon_auth.py, stores only a refresh token and
exchanges it for cookies of each store), cookies pasted from the user's
browser (one Cookie header per store), or email + password + optional TOTP
through the amazon-orders login (one login per store).

Safety rules:
- No captcha solving: the captcha forms of the library are not used, and
  any interactive prompt raises, so the integration stops polling and asks
  the user to reauthenticate instead.
- Read-only: only the order history (last 30 days, filtered to 14 days)
  and the tracking page of not yet delivered shipments are fetched.
- Session cookies are persisted obfuscated in ``.storage`` (not in plain
  files) to limit logins.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.requirements import RequirementsNotFound, async_process_requirements

from ..const import DOMAIN
from ..crypto import decrypt_text, encrypt_text
from .amazon_auth import (
    DEFAULT_STORE,
    STORES,
    AmazonTokenInvalid,
    async_exchange_cookies,
    auth_cookie,
)

_LOGGER = logging.getLogger(__name__)

REQUIREMENT = "amazon-orders==5.2.0"
ORDER_MAX_AGE_DAYS = 14
STORAGE_VERSION = 1

_POSTCODE_RE = re.compile(r"\b(\d{4})\s?([A-Z]{2})\b")


class AmazonAuthRequired(Exception):
    """Login failed or Amazon asked for a captcha/OTP: user action needed."""


class AmazonUnavailable(Exception):
    """The amazon-orders library cannot be installed or imported."""


@dataclass(slots=True)
class AmazonShipment:
    """One Amazon shipment."""

    order_id: str
    carrier: str | None = None
    tracking_number: str | None = None
    postal_code: str | None = None
    delivered: bool = False
    delivery_status: str | None = None
    store: str = DEFAULT_STORE


def postal_code_from_address(address: str | None) -> str | None:
    """Return a Dutch postal code (1234AB) found in an address text."""
    if not address:
        return None
    match = _POSTCODE_RE.search(str(address).upper())
    return f"{match.group(1)}{match.group(2)}" if match else None


def is_delivered_status(status: str | None) -> bool:
    """Return True for "Delivered ..." shipment status texts.

    amazon-orders requests English pages; local words are a fallback.
    """
    text = (status or "").strip().lower()
    return text.startswith(
        (
            "delivered",
            "bezorgd",
            "geleverd",
            "zugestellt",
            "geliefert",
            "livré",
            "entregado",
            "consegnato",
        )
    )


async def async_ensure_library(hass: HomeAssistant) -> None:
    """Install amazon-orders on demand (raises AmazonUnavailable)."""
    try:
        await async_process_requirements(hass, DOMAIN, [REQUIREMENT], False)
    except RequirementsNotFound as err:
        raise AmazonUnavailable(f"Could not install {REQUIREMENT}") from err
    try:
        await hass.async_add_executor_job(_import_library)
    except ImportError as err:
        raise AmazonUnavailable(f"Could not import amazon-orders: {err}") from err


def _import_library() -> None:
    import amazonorders.orders
    import amazonorders.session  # noqa: F401


def _build_session(
    workdir: str,
    email: str | None,
    password: str | None,
    otp_secret: str | None,
    store: str = DEFAULT_STORE,
) -> Any:
    """Create an AmazonSession without captcha solving or console prompts."""
    from amazonorders.conf import AmazonOrdersConfig
    from amazonorders.exception import AmazonOrdersAuthError
    from amazonorders.forms import (
        AcicAuthBlocker,
        ClaimForm,
        IntentForm,
        JSAuthBlocker,
        MfaForm,
        PasskeyForm,
        SignInForm,
    )
    from amazonorders.session import AmazonSession, IODefault

    class _NoPromptIO(IODefault):
        """Never wait for console input; fail so the user can reauthenticate."""

        def echo(self, msg: str, **kwargs: Any) -> None:
            _LOGGER.debug("amazon-orders: %s", msg)

        def prompt(self, msg: str, type: Any = None, **kwargs: Any) -> Any:
            raise AmazonOrdersAuthError(f"Interactive input required: {msg}")

    cfg = AmazonOrdersConfig(
        config_path=str(Path(workdir) / "config.yml"),
        data={
            "domain": store,
            "cookie_jar_path": str(Path(workdir) / "cookies.json"),
            "request_timeout": 30,
            "max_auth_attempts": 6,
            "max_auth_retries": 0,
        },
    )
    # No CaptchaForm and no MfaDeviceSelectForm: they would solve captchas
    # or wait for input. Unknown pages raise AmazonOrdersAuthError.
    forms = [
        ClaimForm(cfg),
        IntentForm(cfg),
        SignInForm(cfg),
        PasskeyForm(cfg),
        MfaForm(cfg),
        AcicAuthBlocker(cfg),
        JSAuthBlocker(
            cfg,
            cfg.constants.JS_ROBOT_TEXT_REGEX,
            cfg.constants.AKAMAI_INTERSTITIAL_REGEX,
        ),
    ]
    return AmazonSession(
        email,
        password,
        io=_NoPromptIO(),
        config=cfg,
        auth_forms=forms,
        otp_secret_key=(otp_secret or "").replace(" ", "") or None,
    )


def _store_errors_only_auth(errors: dict[str, str]) -> bool:
    return bool(errors) and all(e.startswith("auth:") for e in errors.values())


class AmazonSource:
    """Poll Amazon orders of the selected stores for tracking numbers."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        email: str | None = None,
        password: str | None = None,
        otp_secret: str | None = None,
        refresh_token: str | None = None,
        cookies: dict[str, list[dict[str, Any]]] | None = None,
        stores: list[str] | None = None,
    ) -> None:
        """Initialize.

        ``refresh_token``: browser login, cookies of each store are obtained
        before every poll. ``cookies``: pasted browser cookies per store.
        Otherwise the amazon-orders password login is used per store.
        """
        self.hass = hass
        self.email = email
        self.password = password
        self.otp_secret = otp_secret
        self.refresh_token = refresh_token
        self.static_cookies = cookies or {}
        self.stores = [s for s in (stores or [DEFAULT_STORE]) if s in STORES] or [
            DEFAULT_STORE
        ]
        self.blocked = False
        self.last_order_count = 0
        self.last_store_results: dict[str, Any] = {}
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}_amazon"
        )
        self._cookies: dict[str, list[dict[str, Any]]] | None = None
        self._http: aiohttp.ClientSession | None = None
        # (store, order_id, shipment_id) -> (carrier, tracking_number)
        self._tracking_cache: dict[tuple, tuple[str | None, str | None]] = {}

    @property
    def token_mode(self) -> bool:
        """Return True when the browser login (refresh token) is used."""
        return bool(self.refresh_token)

    @property
    def cookie_mode(self) -> bool:
        """Return True when signed-in cookies are used (no password login)."""
        return self.token_mode or bool(self.static_cookies)

    def _http_session(self) -> aiohttp.ClientSession:
        # Own session without a cookie jar: Amazon cookies must not end up in
        # Home Assistant's shared HTTP session.
        if self._http is None:
            self._http = async_create_clientsession(
                self.hass, cookie_jar=aiohttp.DummyCookieJar()
            )
        return self._http

    async def _async_token_cookies(self, store: str) -> list[dict[str, Any]]:
        try:
            return await async_exchange_cookies(
                self._http_session(), self.refresh_token or "", store
            )
        except AmazonTokenInvalid as err:
            raise AmazonAuthRequired(f"Amazon login expired: {err}") from err

    async def async_remove_store(self) -> None:
        """Delete persisted cookies."""
        await self._store.async_remove()

    async def _async_load_cookies(self) -> dict[str, list[dict[str, Any]]]:
        if self._cookies is None:
            stored = await self._store.async_load() or {}
            self._cookies = {}
            blob = stored.get("cookies")
            if blob:
                try:
                    data = json.loads(decrypt_text(self.hass, blob))
                except (ValueError, TypeError):
                    data = {}
                # Version 2.0.0 betas stored one list for amazon.nl.
                self._cookies = (
                    {DEFAULT_STORE: data} if isinstance(data, list) else dict(data)
                )
        return self._cookies

    async def _async_save_cookies(self, cookies: dict[str, Any]) -> None:
        self._cookies = cookies
        await self._store.async_save(
            {"cookies": encrypt_text(self.hass, json.dumps(cookies))}
        )

    async def async_validate(self) -> None:
        """Log in once on the first store (config flow validation)."""
        await async_ensure_library(self.hass)
        store = self.stores[0]
        _, cookies = await self.hass.async_add_executor_job(
            self._run_blocking, store, [], False
        )
        await self._async_save_cookies({store: cookies})

    async def async_fetch_shipments(self) -> list[AmazonShipment]:
        """Return shipments of the last 14 days from all selected stores.

        A store that fails is skipped (and reported in last_store_results);
        only when every store needs a new login AmazonAuthRequired is raised.
        """
        await async_ensure_library(self.hass)
        shipments: list[AmazonShipment] = []
        errors: dict[str, str] = {}
        results: dict[str, Any] = {}
        total_orders = 0
        saved = await self._async_load_cookies() if not self.cookie_mode else {}
        for store in self.stores:
            try:
                if self.token_mode:
                    cookies = await self._async_token_cookies(store)
                elif self.cookie_mode:
                    cookies = list(self.static_cookies.get(store) or [])
                    if not cookies:
                        raise AmazonAuthRequired(f"no cookies for {store}")
                else:
                    cookies = list(saved.get(store) or [])
                found, new_cookies = await self.hass.async_add_executor_job(
                    self._run_blocking, store, cookies, True
                )
            except AmazonAuthRequired as err:
                if self.token_mode and "login expired" in str(err):
                    raise  # the refresh token itself is invalid
                errors[store] = f"auth: {err}"
                results[store] = "auth_required"
                _LOGGER.warning("%s: %s", store, err)
                continue
            except Exception as err:
                errors[store] = f"error: {err}"
                results[store] = "error"
                _LOGGER.warning("%s: Amazon orders could not be read: %s", store, err)
                continue
            if not self.cookie_mode:
                saved[store] = new_cookies
            results[store] = self.last_order_count
            total_orders += self.last_order_count
            shipments.extend(found)
        if not self.cookie_mode:
            await self._async_save_cookies(saved)
        self.last_order_count = total_orders
        self.last_store_results = results
        if errors and len(errors) == len(self.stores):
            if _store_errors_only_auth(errors):
                raise AmazonAuthRequired(
                    "; ".join(f"{k}: {v}" for k, v in errors.items())
                )
            raise RuntimeError("; ".join(f"{k}: {v}" for k, v in errors.items()))
        return shipments

    # -- executor -----------------------------------------------------

    def _run_blocking(
        self, store: str, cookies: list[dict[str, Any]], fetch: bool
    ) -> tuple[list[AmazonShipment], list[dict[str, Any]]]:
        from amazonorders.exception import (
            AmazonOrdersAuthError,
            AmazonOrdersError,
            AmazonOrdersNotFoundError,
            AmazonOrdersUnavailableError,
        )
        from amazonorders.orders import AmazonOrders

        workdir = tempfile.mkdtemp(prefix="dhl_parcel_nl_amazon_")
        try:
            session = _build_session(
                workdir, self.email, self.password, self.otp_secret, store
            )
            for cookie in cookies:
                try:
                    session.session.cookies.set(
                        cookie["name"],
                        cookie["value"],
                        domain=cookie.get("domain") or f".{store}",
                        path=cookie.get("path") or "/",
                    )
                except (KeyError, TypeError):
                    continue
            try:
                if self.cookie_mode:
                    # Signed-in cookies (exchanged or pasted); never call
                    # login() (it would sign out and ask for a password).
                    if not session.auth_cookies_stored():
                        raise AmazonAuthRequired(
                            f"cookies of {store} miss {auth_cookie(store)}"
                        )
                    session.is_authenticated = True
                else:
                    session.login()
                result: list[AmazonShipment] = []
                if fetch:
                    result = self._collect(
                        AmazonOrders(session),
                        AmazonOrdersNotFoundError,
                        AmazonOrdersUnavailableError,
                        store,
                    )
            except AmazonOrdersAuthError as err:
                raise AmazonAuthRequired(str(err).split(" http")[0]) from err
            except AmazonOrdersError as err:
                raise RuntimeError(f"amazon-orders error: {err}") from err
            new_cookies = [
                {
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain,
                    "path": c.path,
                }
                for c in session.session.cookies
            ]
            return result, new_cookies
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _collect(
        self,
        orders_api: Any,
        not_found_exc: type,
        unavailable_exc: type,
        store: str = DEFAULT_STORE,
    ) -> list[AmazonShipment]:
        cutoff = date.today() - timedelta(days=ORDER_MAX_AGE_DAYS)
        shipments: list[AmazonShipment] = []
        orders = list(orders_api.get_orders(time_filter="last30"))
        self.last_order_count = 0
        for order in orders:
            placed = getattr(order, "order_placed_date", None)
            if isinstance(placed, date) and placed < cutoff:
                continue
            if getattr(order, "cancelled", False):
                continue
            order_id = str(getattr(order, "order_number", "") or "")
            if not order_id:
                continue
            self.last_order_count += 1
            recipient = getattr(order, "recipient", None)
            postal = postal_code_from_address(getattr(recipient, "address", None))
            for shipment in getattr(order, "shipments", None) or []:
                status = getattr(shipment, "delivery_status", None)
                delivered = is_delivered_status(status)
                shipment_id = getattr(shipment, "shipment_id", None)
                carrier = tracking = None
                # Delivered shipments are looked up too (once, cached), so they
                # show up like delivered DHL account parcels until retention.
                if shipment_id:
                    key = (store, order_id, str(shipment_id))
                    if key in self._tracking_cache:
                        carrier, tracking = self._tracking_cache[key]
                    else:
                        try:
                            info = orders_api.get_tracking(order_id, shipment_id)
                            carrier = getattr(info, "carrier", None)
                            tracking = getattr(info, "tracking_number", None)
                        except (not_found_exc, unavailable_exc) as err:
                            _LOGGER.debug(
                                "No tracking page for Amazon order %s: %s",
                                order_id,
                                type(err).__name__,
                            )
                        if tracking:
                            self._tracking_cache[key] = (carrier, tracking)
                shipments.append(
                    AmazonShipment(
                        order_id=order_id,
                        carrier=carrier,
                        tracking_number=tracking,
                        postal_code=postal,
                        delivered=delivered,
                        delivery_status=status,
                        store=store,
                    )
                )
        return shipments
