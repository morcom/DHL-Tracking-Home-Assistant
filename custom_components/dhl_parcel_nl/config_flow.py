"""Config flow for Parcel Tracking NL (DHL, PostNL, Dragonfly)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .carriers import CARRIERS
from .carriers.base import TrackingError
from .carriers.postnl import (
    PostNLAuth,
    PostNLAuthError,
    authorize_url,
    code_from_redirect,
    new_pkce,
)
from .const import (
    AMAZON_LOGIN_BROWSER,
    AMAZON_LOGIN_BROWSER_PASTE,
    AMAZON_LOGIN_COOKIES,
    AMAZON_LOGIN_PASSWORD,
    CONF_AMAZON_COOKIES_ENCRYPTED,
    CONF_AMAZON_STORES,
    CONF_AMAZON_LOGIN_METHOD,
    CONF_AMAZON_TOKEN_ENCRYPTED,
    ACCOUNT_TYPE_BUSINESS,
    ACCOUNT_TYPE_CONSUMER,
    ACCOUNT_TYPE_NONE,
    CONF_ACCOUNT_TYPE,
    CONF_AMAZON_EMAIL_ENCRYPTED,
    CONF_AMAZON_ENABLED,
    CONF_AMAZON_OTP_ENCRYPTED,
    CONF_AMAZON_PASSWORD_ENCRYPTED,
    CONF_AMAZON_POLL_MINUTES,
    CONF_API_KEY,
    CONF_CREDENTIALS_ENCRYPTED,
    CONF_DELIVERED_KEEP_DAYS,
    CONF_EMAIL,
    CONF_EMAIL_ENCRYPTED,
    CONF_ENABLED_CARRIERS,
    CONF_PASSWORD,
    CONF_PASSWORD_ENCRYPTED,
    CONF_POSTAL_CODE,
    CONF_POSTNL_TOKEN_ENCRYPTED,
    CONF_REFRESH_END_TIME,
    CONF_REFRESH_INTERVAL_MINUTES,
    CONF_REFRESH_START_TIME,
    CONF_SUMMARY_LANGUAGE,
    CONF_USER_ID,
    DEFAULT_AMAZON_POLL_MINUTES,
    DEFAULT_DELIVERED_KEEP_DAYS,
    DEFAULT_REFRESH_END_TIME,
    DEFAULT_REFRESH_START_TIME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_SUMMARY_LANGUAGE,
    DOMAIN,
)
from .crypto import encrypt_text

_LOGGER = logging.getLogger(__name__)

TITLE = "Parcel Tracking NL"
ACCOUNT_TYPES = [ACCOUNT_TYPE_CONSUMER, ACCOUNT_TYPE_BUSINESS, ACCOUNT_TYPE_NONE]
CARRIER_SELECT = cv.multi_select(
    {"dhl": "DHL", "postnl": "PostNL", "dragonfly": "Dragonfly"}
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_ACCOUNT_TYPE,
            default=ACCOUNT_TYPE_CONSUMER,
        ): vol.In(ACCOUNT_TYPES),
        vol.Optional(CONF_EMAIL): str,
        vol.Optional(CONF_PASSWORD): str,
        vol.Optional(CONF_USER_ID): str,
        vol.Optional(CONF_API_KEY): str,
        vol.Optional(CONF_POSTAL_CODE): str,
        vol.Optional(CONF_ENABLED_CARRIERS, default=list(CARRIERS)): CARRIER_SELECT,
        vol.Optional(
            CONF_DELIVERED_KEEP_DAYS,
            default=DEFAULT_DELIVERED_KEEP_DAYS,
        ): vol.All(vol.Coerce(int), vol.Range(min=0, max=60)),
        vol.Optional(
            CONF_REFRESH_INTERVAL_MINUTES,
            default=DEFAULT_SCAN_INTERVAL_MINUTES,
        ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
        vol.Optional(
            CONF_REFRESH_START_TIME,
            default=DEFAULT_REFRESH_START_TIME,
        ): str,
        vol.Optional(
            CONF_REFRESH_END_TIME,
            default=DEFAULT_REFRESH_END_TIME,
        ): str,
        vol.Optional(
            CONF_SUMMARY_LANGUAGE,
            default=DEFAULT_SUMMARY_LANGUAGE,
        ): vol.In(["en", "pl", "nl"]),
    }
)


AMAZON_PASSWORD_SCHEMA = vol.Schema(
    {
        vol.Optional("amazon_email", default=""): str,
        vol.Optional("amazon_password", default=""): str,
        vol.Optional("amazon_otp_secret", default=""): str,
    }
)
AMAZON_BROWSER_SCHEMA = vol.Schema({vol.Required("amazon_redirect_url"): str})
AMAZON_COOKIES_SCHEMA = vol.Schema({vol.Required("amazon_cookies"): str})
AMAZON_METHODS = [
    AMAZON_LOGIN_BROWSER,
    AMAZON_LOGIN_BROWSER_PASTE,
    AMAZON_LOGIN_COOKIES,
    AMAZON_LOGIN_PASSWORD,
]
BROWSER_METHODS = (AMAZON_LOGIN_BROWSER, AMAZON_LOGIN_BROWSER_PASTE)


STORE_SELECT = cv.multi_select(
    {
        "amazon.nl": "amazon.nl",
        "amazon.de": "amazon.de",
        "amazon.com.be": "amazon.com.be",
        "amazon.fr": "amazon.fr",
        "amazon.es": "amazon.es",
        "amazon.it": "amazon.it",
        "amazon.co.uk": "amazon.co.uk",
        "amazon.com": "amazon.com",
    }
)


def _signin_domains(stores: list[str]) -> set[str]:
    from .sources.amazon_auth import store_info

    return {store_info(store)["signin_domain"] for store in stores}


async def _async_check_stores(
    hass: HomeAssistant, cookies_by_store: dict[str, list[dict[str, Any]]]
) -> tuple[list[str], str | None]:
    """Check the order history of each store. Returns (accepted, error key)."""
    import aiohttp

    from homeassistant.helpers.aiohttp_client import async_create_clientsession

    from .sources.amazon_auth import AmazonLoginError, async_check_orders_page

    session = async_create_clientsession(
        hass, auto_cleanup=False, cookie_jar=aiohttp.DummyCookieJar()
    )
    accepted: list[str] = []
    try:
        for store, cookies in cookies_by_store.items():
            if await async_check_orders_page(session, cookies, store):
                accepted.append(store)
    except AmazonLoginError as err:
        _LOGGER.warning("Amazon order history check failed: %s", err)
        return accepted, "cannot_connect"
    finally:
        session.detach()
    return accepted, None


async def _async_amazon_cookie_login(
    hass: HomeAssistant, pasted: str, stores: list[str]
) -> tuple[str | None, str | None, list[str]]:
    """Validate pasted browser cookies (one Cookie header per store and line).

    Returns (encrypted cookies, error key, stores without valid cookies).
    """
    from .sources.amazon_auth import parse_cookie_headers

    parsed = {k: v for k, v in parse_cookie_headers(pasted).items() if k in stores}
    if not parsed:
        return None, "amazon_cookies_invalid", stores
    accepted, error = await _async_check_stores(hass, parsed)
    if error:
        return None, error, stores
    missing = [store for store in stores if store not in accepted]
    if not accepted:
        return None, "amazon_cookies_rejected", missing
    if missing:
        _LOGGER.warning("Amazon cookie login: no valid cookies for %s", missing)
    data = {store: parsed[store] for store in accepted}
    return encrypt_text(hass, json.dumps(data)), None, missing


async def _async_validate_amazon(
    hass: HomeAssistant,
    entry_id: str,
    email: str,
    password: str,
    otp: str | None,
    stores: list[str] | None = None,
) -> str | None:
    """Log in once on the first store. Returns an error key or None."""
    from .sources.amazon import AmazonAuthRequired, AmazonSource, AmazonUnavailable

    source = AmazonSource(hass, entry_id, email, password, otp, stores=stores)
    try:
        await source.async_validate()
    except AmazonAuthRequired as err:
        _LOGGER.warning("Amazon password login failed: %s", err)
        return "amazon_auth"
    except AmazonUnavailable as err:
        _LOGGER.warning("Amazon source unavailable: %s", err)
        return "amazon_unavailable"
    except Exception as err:
        _LOGGER.warning("Amazon password login error: %s", err)
        return "cannot_connect"
    return None


def _encrypted_amazon_options(
    hass: HomeAssistant, user_input: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge Amazon credentials; empty fields keep stored values."""
    result: dict[str, Any] = {}
    for field, key in (
        ("amazon_email", CONF_AMAZON_EMAIL_ENCRYPTED),
        ("amazon_password", CONF_AMAZON_PASSWORD_ENCRYPTED),
        ("amazon_otp_secret", CONF_AMAZON_OTP_ENCRYPTED),
    ):
        value = (user_input.get(field) or "").strip()
        if value:
            result[key] = encrypt_text(hass, value)
        elif current.get(key):
            result[key] = current[key]
    return result


class PostNLLoginMixin:
    """Shared PostNL paste-login steps for options and reauth flows."""

    hass: HomeAssistant
    _pkce: dict[str, str] | None = None

    def _postnl_placeholders(self) -> dict[str, str]:
        if self._pkce is None:
            self._pkce = new_pkce()
        return {"authorize_url": authorize_url(self._pkce)}

    async def _async_postnl_exchange(
        self, redirect: str
    ) -> tuple[str | None, str | None]:
        """Return (encrypted token, error key)."""
        assert self._pkce is not None
        try:
            code = code_from_redirect(redirect, self._pkce["state"])
        except ValueError:
            return None, "postnl_invalid_redirect"
        auth = PostNLAuth(async_get_clientsession(self.hass))
        try:
            token = await auth.async_exchange_code(code, self._pkce["code_verifier"])
        except PostNLAuthError:
            self._pkce = None
            return None, "postnl_auth"
        except TrackingError:
            return None, "cannot_connect"
        return encrypt_text(self.hass, json.dumps(token)), None


class AmazonBrowserLoginMixin:
    """Amazon browser login (PKCE + device registration), shared by flows."""

    hass: HomeAssistant
    _amazon_login: dict[str, str] | None = None
    _amazon_stores: list[str] | None = None

    def _amazon_placeholders(self) -> dict[str, str]:
        from .sources.amazon_auth import maplanding_prefix, new_login, signin_url

        store = (self._amazon_stores or ["amazon.nl"])[0]
        if self._amazon_login is None or self._amazon_login.get("store") != store:
            self._amazon_login = new_login(store)
        return {
            "signin_url": signin_url(self._amazon_login),
            "maplanding": maplanding_prefix(store),
            "stores": ", ".join(self._amazon_stores or ["amazon.nl"]),
        }

    # -- login through Home Assistant (no copying) ---------------------

    _amazon_proxy: Any = None
    _amazon_error: str | None = None

    def _flow_manager(self) -> Any:
        raise NotImplementedError

    def _amazon_external_step(self, step_id: str) -> Any:
        """Open the Amazon login through Home Assistant in a new window."""
        from .sources.amazon_proxy import (
            AmazonProxyLogin,
            async_get_view,
            start_url,
        )

        flow_id = self.flow_id  # type: ignore[attr-defined]
        view = async_get_view(self.hass)
        if self._amazon_proxy is None:
            manager = self._flow_manager()

            def _is_active() -> bool:
                try:
                    manager.async_get(flow_id)
                except Exception:
                    return False
                return True

            async def _on_done() -> None:
                try:
                    await manager.async_configure(flow_id)
                except Exception as err:
                    _LOGGER.warning("Could not continue the Amazon login: %s", err)

            self._amazon_proxy = AmazonProxyLogin(
                self.hass,
                flow_id,
                (self._amazon_stores or ["amazon.nl"])[0],
                _is_active,
                _on_done,
            )
            view.sessions[flow_id] = self._amazon_proxy
        return self.async_external_step(  # type: ignore[attr-defined]
            step_id=step_id,
            url=start_url(self.hass, flow_id),
            description_placeholders={
                "stores": ", ".join(self._amazon_stores or ["amazon.nl"])
            },
        )

    def _amazon_proxy_done(self) -> bool:
        return bool(self._amazon_proxy and self._amazon_proxy.authorization_code)

    async def _async_finish_amazon_proxy(self) -> tuple[str | None, str | None]:
        """Finish the login through Home Assistant. Returns (token, error)."""
        from .sources.amazon_proxy import async_get_view

        proxy, self._amazon_proxy = self._amazon_proxy, None
        async_get_view(self.hass).sessions.pop(self.flow_id, None)  # type: ignore[attr-defined]
        if proxy is None or not proxy.authorization_code:
            return None, "amazon_register_failed"
        self._amazon_login = proxy.login
        token, error = await self._async_amazon_finish_login(proxy.authorization_code)
        if error:
            # Offer the manual (paste) login with a fresh sign-in link.
            self._amazon_error = error
            self._amazon_login = None
        return token, error

    async def _async_amazon_browser_login(
        self, redirect: str
    ) -> tuple[str | None, str | None]:
        """Return (encrypted token blob, error key)."""

        from .sources.amazon_auth import AmazonLoginError, code_from_redirect

        assert self._amazon_login is not None
        try:
            code = code_from_redirect(redirect)
        except AmazonLoginError:
            return None, "amazon_invalid_redirect"
        return await self._async_amazon_finish_login(code)

    async def _async_amazon_finish_login(
        self, code: str
    ) -> tuple[str | None, str | None]:
        """Register the device with the code; check the stores' order history."""
        import aiohttp

        from homeassistant.helpers.aiohttp_client import async_create_clientsession

        from .sources.amazon_auth import (
            AmazonLoginError,
            AmazonTokenInvalid,
            async_exchange_cookies,
            async_register,
        )

        assert self._amazon_login is not None
        stores = self._amazon_stores or ["amazon.nl"]
        session = async_create_clientsession(
            self.hass, auto_cleanup=False, cookie_jar=aiohttp.DummyCookieJar()
        )
        cookies_by_store: dict[str, list[dict[str, Any]]] = {}
        try:
            token = await async_register(session, code, self._amazon_login)
            for store in stores:
                try:
                    cookies_by_store[store] = await async_exchange_cookies(
                        session, token["refresh_token"], store
                    )
                except AmazonLoginError as err:
                    _LOGGER.warning(
                        "Amazon browser login: no cookies for %s (%s)", store, err
                    )
        except AmazonTokenInvalid as err:
            _LOGGER.warning("Amazon browser login: token not accepted (%s)", err)
            self._amazon_login = None
            return None, "amazon_auth"
        except AmazonLoginError as err:
            _LOGGER.warning("Amazon browser login failed: %s", err)
            # Codes are single use: start over with a new sign-in link.
            self._amazon_login = None
            return None, "amazon_register_failed"
        finally:
            session.detach()
        accepted, error = await _async_check_stores(self.hass, cookies_by_store)
        if error:
            return None, error
        if not accepted:
            self._amazon_login = None
            return None, "amazon_cookies_rejected"
        missing = [store for store in stores if store not in accepted]
        if missing:
            _LOGGER.warning(
                "Amazon browser login: order history not accessible on %s", missing
            )
        return encrypt_text(self.hass, json.dumps(token)), None


class DHLParcelNLConfigFlow(
    AmazonBrowserLoginMixin, PostNLLoginMixin, ConfigFlow, domain=DOMAIN
):
    """Handle a config flow for Parcel Tracking NL."""

    VERSION = 1
    _reauth_target: str | None = None

    def _flow_manager(self) -> Any:
        return self.hass.config_entries.flow

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            account_type = user_input.get(CONF_ACCOUNT_TYPE, ACCOUNT_TYPE_CONSUMER)
            has_consumer = bool(
                user_input.get(CONF_EMAIL) and user_input.get(CONF_PASSWORD)
            )
            has_business = bool(
                user_input.get(CONF_USER_ID) and user_input.get(CONF_API_KEY)
            )

            if account_type == ACCOUNT_TYPE_CONSUMER and not has_consumer:
                errors["base"] = "invalid_auth_consumer"
            elif account_type == ACCOUNT_TYPE_BUSINESS and not has_business:
                errors["base"] = "invalid_auth_business"
            elif (
                account_type != ACCOUNT_TYPE_NONE
                and not has_consumer
                and not has_business
            ):
                errors["base"] = "invalid_auth"
            elif not user_input.get(CONF_ENABLED_CARRIERS, list(CARRIERS)):
                errors["base"] = "no_carrier"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()

                user_input.setdefault(
                    CONF_DELIVERED_KEEP_DAYS, DEFAULT_DELIVERED_KEEP_DAYS
                )
                user_input.setdefault(
                    CONF_REFRESH_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
                )
                user_input.setdefault(
                    CONF_REFRESH_START_TIME, DEFAULT_REFRESH_START_TIME
                )
                user_input.setdefault(CONF_REFRESH_END_TIME, DEFAULT_REFRESH_END_TIME)
                user_input.setdefault(CONF_SUMMARY_LANGUAGE, DEFAULT_SUMMARY_LANGUAGE)
                user_input.setdefault(CONF_ENABLED_CARRIERS, list(CARRIERS))

                if account_type == ACCOUNT_TYPE_CONSUMER:
                    secure_input = dict(user_input)
                    secure_input[CONF_EMAIL_ENCRYPTED] = encrypt_text(
                        self.hass, user_input.get(CONF_EMAIL, "")
                    )
                    secure_input[CONF_PASSWORD_ENCRYPTED] = encrypt_text(
                        self.hass, user_input.get(CONF_PASSWORD, "")
                    )
                    secure_input[CONF_CREDENTIALS_ENCRYPTED] = True
                    secure_input.pop(CONF_EMAIL, None)
                    secure_input.pop(CONF_PASSWORD, None)
                    user_input = secure_input
                elif account_type == ACCOUNT_TYPE_NONE:
                    for key in (CONF_EMAIL, CONF_PASSWORD, CONF_USER_ID, CONF_API_KEY):
                        user_input.pop(key, None)

                return self.async_create_entry(title=TITLE, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    # -- reauth -------------------------------------------------------

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication of PostNL or Amazon."""
        self._reauth_target = entry_data.get("reauth_target") or "postnl"
        if self._reauth_target == "amazon":
            return await self.async_step_reauth_amazon()
        return await self.async_step_reauth_postnl()

    async def async_step_reauth_postnl(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Repeat the PostNL paste login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token, error = await self._async_postnl_exchange(
                user_input.get("redirect_url", "")
            )
            if token:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_POSTNL_TOKEN_ENCRYPTED: token},
                )
            errors["base"] = error or "unknown"
        return self.async_show_form(
            step_id="reauth_postnl",
            data_schema=vol.Schema({vol.Required("redirect_url"): str}),
            description_placeholders=self._postnl_placeholders(),
            errors=errors,
        )

    async def async_step_reauth_amazon(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Log in to Amazon.nl again (same method as before)."""
        entry = self._get_reauth_entry()
        method = entry.options.get(CONF_AMAZON_LOGIN_METHOD, AMAZON_LOGIN_PASSWORD)
        self._amazon_stores = list(
            entry.options.get(CONF_AMAZON_STORES) or ["amazon.nl"]
        )
        if method == AMAZON_LOGIN_BROWSER:
            return await self.async_step_reauth_amazon_proxy()
        if method == AMAZON_LOGIN_BROWSER_PASTE:
            return await self.async_step_reauth_amazon_browser()
        if method == AMAZON_LOGIN_COOKIES:
            return await self.async_step_reauth_amazon_cookies()
        return await self.async_step_reauth_amazon_password()

    async def async_step_reauth_amazon_cookies(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Paste fresh Amazon.nl browser cookies."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            cookies, error, _ = await _async_amazon_cookie_login(
                self.hass,
                user_input.get("amazon_cookies", ""),
                list(entry.options.get(CONF_AMAZON_STORES) or ["amazon.nl"]),
            )
            if cookies:
                return self.async_update_reload_and_abort(
                    entry,
                    options={
                        **entry.options,
                        CONF_AMAZON_COOKIES_ENCRYPTED: cookies,
                        CONF_AMAZON_ENABLED: True,
                    },
                )
            errors["base"] = error or "unknown"
        return self.async_show_form(
            step_id="reauth_amazon_cookies",
            data_schema=AMAZON_COOKIES_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth_amazon_proxy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Log in to Amazon again through Home Assistant."""
        if self._amazon_proxy_done():
            return self.async_external_step_done(
                next_step_id="reauth_amazon_proxy_done"
            )
        return self._amazon_external_step("reauth_amazon_proxy")

    async def async_step_reauth_amazon_proxy_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Store the new Amazon login (or offer the manual login)."""
        token, _ = await self._async_finish_amazon_proxy()
        if token:
            entry = self._get_reauth_entry()
            return self.async_update_reload_and_abort(
                entry,
                options={
                    **entry.options,
                    CONF_AMAZON_TOKEN_ENCRYPTED: token,
                    CONF_AMAZON_ENABLED: True,
                },
            )
        return await self.async_step_reauth_amazon_browser()

    async def async_step_reauth_amazon_browser(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Repeat the Amazon browser login (paste the address)."""
        entry = self._get_reauth_entry()
        self._amazon_stores = list(
            entry.options.get(CONF_AMAZON_STORES) or ["amazon.nl"]
        )
        errors: dict[str, str] = {}
        if user_input is None and self._amazon_error:
            errors["base"], self._amazon_error = self._amazon_error, None
        if user_input is not None:
            token, error = await self._async_amazon_browser_login(
                user_input.get("amazon_redirect_url", "")
            )
            if token:
                return self.async_update_reload_and_abort(
                    entry,
                    options={
                        **entry.options,
                        CONF_AMAZON_TOKEN_ENCRYPTED: token,
                        CONF_AMAZON_ENABLED: True,
                    },
                )
            errors["base"] = error or "unknown"
        return self.async_show_form(
            step_id="reauth_amazon_browser",
            data_schema=AMAZON_BROWSER_SCHEMA,
            description_placeholders=self._amazon_placeholders(),
            errors=errors,
        )

    async def async_step_reauth_amazon_password(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter Amazon.nl credentials again."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            amazon = _encrypted_amazon_options(self.hass, user_input, entry.options)
            from .crypto import decrypt_text

            email = decrypt_text(self.hass, amazon.get(CONF_AMAZON_EMAIL_ENCRYPTED, ""))
            password = decrypt_text(
                self.hass, amazon.get(CONF_AMAZON_PASSWORD_ENCRYPTED, "")
            )
            otp = decrypt_text(self.hass, amazon.get(CONF_AMAZON_OTP_ENCRYPTED, ""))
            error = await _async_validate_amazon(
                self.hass,
                entry.entry_id,
                email,
                password,
                otp or None,
                list(entry.options.get(CONF_AMAZON_STORES) or ["amazon.nl"]),
            )
            if error is None:
                return self.async_update_reload_and_abort(
                    entry,
                    options={**entry.options, **amazon, CONF_AMAZON_ENABLED: True},
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_amazon_password",
            data_schema=AMAZON_PASSWORD_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get options flow."""
        return DHLParcelNLOptionsFlow()


class DHLParcelNLOptionsFlow(AmazonBrowserLoginMixin, PostNLLoginMixin, OptionsFlow):
    """Options: General, DHL, PostNL, Amazon.nl."""

    def _flow_manager(self) -> Any:
        return self.hass.config_entries.options

    # Set by async_step_amazon before the browser/password steps run.
    _amazon_settings: dict[str, Any]

    def _current(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    def _save(self, updates: Mapping[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(
            title="", data={**self.config_entry.options, **updates}
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options menu."""
        return self.async_show_menu(
            step_id="init", menu_options=["general", "dhl", "postnl", "amazon"]
        )

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """General settings and enabled carriers."""
        current = self._current()
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_ENABLED_CARRIERS):
                errors["base"] = "no_carrier"
            else:
                return self._save(user_input)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ENABLED_CARRIERS,
                    default=list(current.get(CONF_ENABLED_CARRIERS) or CARRIERS),
                ): CARRIER_SELECT,
                vol.Optional(
                    CONF_POSTAL_CODE, default=current.get(CONF_POSTAL_CODE) or ""
                ): str,
                vol.Optional(
                    "tracking_codes", default=current.get("tracking_codes") or ""
                ): str,
                vol.Optional(
                    CONF_DELIVERED_KEEP_DAYS,
                    default=current.get(
                        CONF_DELIVERED_KEEP_DAYS, DEFAULT_DELIVERED_KEEP_DAYS
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=60)),
                vol.Optional(
                    CONF_REFRESH_INTERVAL_MINUTES,
                    default=current.get(
                        CONF_REFRESH_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
                vol.Optional(
                    CONF_REFRESH_START_TIME,
                    default=current.get(
                        CONF_REFRESH_START_TIME, DEFAULT_REFRESH_START_TIME
                    ),
                ): str,
                vol.Optional(
                    CONF_REFRESH_END_TIME,
                    default=current.get(
                        CONF_REFRESH_END_TIME, DEFAULT_REFRESH_END_TIME
                    ),
                ): str,
                vol.Optional(
                    CONF_SUMMARY_LANGUAGE,
                    default=current.get(
                        CONF_SUMMARY_LANGUAGE, DEFAULT_SUMMARY_LANGUAGE
                    ),
                ): vol.In(["en", "pl", "nl"]),
            }
        )
        return self.async_show_form(
            step_id="general", data_schema=schema, errors=errors
        )

    async def async_step_dhl(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """DHL account settings (optional)."""
        current = self._current()
        if user_input is not None:
            account_type = user_input.get(CONF_ACCOUNT_TYPE, ACCOUNT_TYPE_CONSUMER)
            if account_type == ACCOUNT_TYPE_CONSUMER:
                new_email = (user_input.get(CONF_EMAIL) or "").strip()
                new_password = user_input.get(CONF_PASSWORD) or ""
                if new_email:
                    user_input[CONF_EMAIL_ENCRYPTED] = encrypt_text(
                        self.hass, new_email
                    )
                elif current.get(CONF_EMAIL_ENCRYPTED):
                    user_input[CONF_EMAIL_ENCRYPTED] = current[CONF_EMAIL_ENCRYPTED]
                if new_password:
                    user_input[CONF_PASSWORD_ENCRYPTED] = encrypt_text(
                        self.hass, new_password
                    )
                elif current.get(CONF_PASSWORD_ENCRYPTED):
                    user_input[CONF_PASSWORD_ENCRYPTED] = current[
                        CONF_PASSWORD_ENCRYPTED
                    ]
                user_input[CONF_CREDENTIALS_ENCRYPTED] = True
            user_input.pop(CONF_EMAIL, None)
            user_input.pop(CONF_PASSWORD, None)
            return self._save(user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ACCOUNT_TYPE,
                    default=current.get(CONF_ACCOUNT_TYPE, ACCOUNT_TYPE_CONSUMER),
                ): vol.In(ACCOUNT_TYPES),
                vol.Optional(CONF_EMAIL, default=""): str,
                vol.Optional(CONF_PASSWORD, default=""): str,
                vol.Optional(
                    CONF_USER_ID, default=current.get(CONF_USER_ID) or ""
                ): str,
                vol.Optional(
                    CONF_API_KEY, default=current.get(CONF_API_KEY) or ""
                ): str,
            }
        )
        return self.async_show_form(step_id="dhl", data_schema=schema)

    async def async_step_postnl(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """PostNL account login via pasted redirect URL."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("postnl_logout"):
                data = dict(self.config_entry.data)
                data.pop(CONF_POSTNL_TOKEN_ENCRYPTED, None)
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=data
                )
                return self._save({"postnl_login_at": None})
            redirect = (user_input.get("redirect_url") or "").strip()
            if not redirect:
                return self._save({})
            token, error = await self._async_postnl_exchange(redirect)
            if token:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={**self.config_entry.data, CONF_POSTNL_TOKEN_ENCRYPTED: token},
                )
                # Changing an option makes the entry reload with the login.
                return self._save({"postnl_login_at": int(time.time())})
            errors["base"] = error or "unknown"

        placeholders = self._postnl_placeholders()
        placeholders["status"] = (
            "connected"
            if self.config_entry.data.get(CONF_POSTNL_TOKEN_ENCRYPTED)
            else "not connected"
        )
        return self.async_show_form(
            step_id="postnl",
            data_schema=vol.Schema(
                {
                    vol.Optional("redirect_url", default=""): str,
                    vol.Optional("postnl_logout", default=False): bool,
                }
            ),
            description_placeholders=placeholders,
            errors=errors,
        )

    def _amazon_schema(self, values: Mapping[str, Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(
                    CONF_AMAZON_ENABLED,
                    default=bool(values.get(CONF_AMAZON_ENABLED, False)),
                ): bool,
                vol.Required(
                    CONF_AMAZON_STORES,
                    default=list(values.get(CONF_AMAZON_STORES) or ["amazon.nl"]),
                ): STORE_SELECT,
                vol.Required(
                    CONF_AMAZON_LOGIN_METHOD,
                    default=values.get(CONF_AMAZON_LOGIN_METHOD, AMAZON_LOGIN_BROWSER),
                ): vol.In(AMAZON_METHODS),
                vol.Optional(
                    CONF_AMAZON_POLL_MINUTES,
                    default=values.get(
                        CONF_AMAZON_POLL_MINUTES, DEFAULT_AMAZON_POLL_MINUTES
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=30, max=60)),
            }
        )

    async def async_step_amazon(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Amazon orders (experimental): stores and login method."""
        current = self.config_entry.options
        if user_input is not None:
            self._amazon_settings = {
                CONF_AMAZON_ENABLED: bool(user_input.get(CONF_AMAZON_ENABLED)),
                CONF_AMAZON_LOGIN_METHOD: user_input.get(
                    CONF_AMAZON_LOGIN_METHOD, AMAZON_LOGIN_BROWSER
                ),
                CONF_AMAZON_POLL_MINUTES: user_input.get(
                    CONF_AMAZON_POLL_MINUTES, DEFAULT_AMAZON_POLL_MINUTES
                ),
                CONF_AMAZON_STORES: list(
                    user_input.get(CONF_AMAZON_STORES) or ["amazon.nl"]
                ),
            }
            self._amazon_stores = self._amazon_settings[CONF_AMAZON_STORES]
            if not self._amazon_settings[CONF_AMAZON_ENABLED]:
                return self._save(self._amazon_settings)
            method = self._amazon_settings[CONF_AMAZON_LOGIN_METHOD]
            if method in BROWSER_METHODS and (
                len(_signin_domains(self._amazon_stores)) > 1
            ):
                return self.async_show_form(
                    step_id="amazon",
                    data_schema=self._amazon_schema(user_input),
                    errors={"base": "amazon_mixed_regions"},
                )
            if method == AMAZON_LOGIN_BROWSER:
                return await self.async_step_amazon_proxy()
            if method == AMAZON_LOGIN_BROWSER_PASTE:
                return await self.async_step_amazon_browser()
            if method == AMAZON_LOGIN_COOKIES:
                return await self.async_step_amazon_cookies()
            return await self.async_step_amazon_password()

        schema = self._amazon_schema(current)
        return self.async_show_form(step_id="amazon", data_schema=schema)

    async def async_step_amazon_proxy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Amazon login through Home Assistant: nothing to copy."""
        if self._amazon_proxy_done():
            return self.async_external_step_done(next_step_id="amazon_proxy_done")
        return self._amazon_external_step("amazon_proxy")

    async def async_step_amazon_proxy_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Store the Amazon login (or offer the manual login on errors)."""
        token, _ = await self._async_finish_amazon_proxy()
        if token:
            # The login method stays "browser"; the stored token is the same.
            return self._save(
                {**self._amazon_settings, CONF_AMAZON_TOKEN_ENCRYPTED: token}
            )
        self._amazon_settings[CONF_AMAZON_LOGIN_METHOD] = AMAZON_LOGIN_BROWSER_PASTE
        return await self.async_step_amazon_browser()

    async def async_step_amazon_browser(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Amazon browser login: paste the /ap/maplanding URL."""
        current = self.config_entry.options
        errors: dict[str, str] = {}
        if user_input is None and self._amazon_error:
            errors["base"], self._amazon_error = self._amazon_error, None
        if user_input is not None:
            redirect = (user_input.get("amazon_redirect_url") or "").strip()
            if not redirect and current.get(CONF_AMAZON_TOKEN_ENCRYPTED):
                return self._save(self._amazon_settings)  # keep current login
            token, error = await self._async_amazon_browser_login(redirect)
            if token:
                return self._save(
                    {**self._amazon_settings, CONF_AMAZON_TOKEN_ENCRYPTED: token}
                )
            errors["base"] = error or "unknown"
        placeholders = self._amazon_placeholders()
        placeholders["status"] = (
            "connected" if current.get(CONF_AMAZON_TOKEN_ENCRYPTED) else "not connected"
        )
        return self.async_show_form(
            step_id="amazon_browser",
            data_schema=vol.Schema(
                {vol.Optional("amazon_redirect_url", default=""): str}
            ),
            description_placeholders=placeholders,
            errors=errors,
        )

    async def async_step_amazon_cookies(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Amazon cookie login: paste the Cookie header of amazon.nl."""
        current = self.config_entry.options
        errors: dict[str, str] = {}
        if user_input is not None:
            pasted = (user_input.get("amazon_cookies") or "").strip()
            if not pasted and current.get(CONF_AMAZON_COOKIES_ENCRYPTED):
                return self._save(self._amazon_settings)  # keep current cookies
            cookies, error, _ = await _async_amazon_cookie_login(
                self.hass, pasted, self._amazon_stores or ["amazon.nl"]
            )
            if cookies:
                return self._save(
                    {**self._amazon_settings, CONF_AMAZON_COOKIES_ENCRYPTED: cookies}
                )
            errors["base"] = error or "unknown"
        return self.async_show_form(
            step_id="amazon_cookies",
            data_schema=vol.Schema({vol.Optional("amazon_cookies", default=""): str}),
            description_placeholders={
                "status": "connected"
                if current.get(CONF_AMAZON_COOKIES_ENCRYPTED)
                else "not connected",
                "orders_url": "https://www.<store>/your-orders/orders",
                "stores": ", ".join(self._amazon_stores or ["amazon.nl"]),
            },
            errors=errors,
        )

    async def async_step_amazon_password(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Amazon password login (amazon-orders library)."""
        current = self.config_entry.options
        errors: dict[str, str] = {}
        if user_input is not None:
            amazon = _encrypted_amazon_options(self.hass, user_input, current)
            if not (
                amazon.get(CONF_AMAZON_EMAIL_ENCRYPTED)
                and amazon.get(CONF_AMAZON_PASSWORD_ENCRYPTED)
            ):
                errors["base"] = "amazon_credentials"
            else:
                from .crypto import decrypt_text

                error = await _async_validate_amazon(
                    self.hass,
                    self.config_entry.entry_id,
                    decrypt_text(self.hass, amazon[CONF_AMAZON_EMAIL_ENCRYPTED]),
                    decrypt_text(self.hass, amazon[CONF_AMAZON_PASSWORD_ENCRYPTED]),
                    decrypt_text(self.hass, amazon.get(CONF_AMAZON_OTP_ENCRYPTED, ""))
                    or None,
                    self._amazon_stores,
                )
                if error is None:
                    return self._save({**self._amazon_settings, **amazon})
                errors["base"] = error
        return self.async_show_form(
            step_id="amazon_password", data_schema=AMAZON_PASSWORD_SCHEMA, errors=errors
        )
