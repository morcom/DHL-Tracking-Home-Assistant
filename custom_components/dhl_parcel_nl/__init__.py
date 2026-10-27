"""Parcel Tracking NL (DHL, PostNL, Dragonfly) integration."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .carriers import CARRIERS, parse_code_spec
from .carriers.postnl import PostNLAuth
from .const import (
    AMAZON_LOGIN_BROWSER,
    AMAZON_LOGIN_BROWSER_PASTE,
    AMAZON_LOGIN_COOKIES,
    AMAZON_LOGIN_PASSWORD,
    CONF_AMAZON_COOKIES_ENCRYPTED,
    CONF_AMAZON_STORES,
    CONF_AMAZON_EMAIL_ENCRYPTED,
    CONF_AMAZON_ENABLED,
    CONF_AMAZON_LOGIN_METHOD,
    CONF_AMAZON_TOKEN_ENCRYPTED,
    CONF_AMAZON_OTP_ENCRYPTED,
    CONF_AMAZON_PASSWORD_ENCRYPTED,
    CONF_AMAZON_POLL_MINUTES,
    CONF_ENABLED_CARRIERS,
    CONF_POSTNL_TOKEN_ENCRYPTED,
    DEFAULT_AMAZON_POLL_MINUTES,
    DOMAIN,
    SERVICE_DHL_PARCEL_REFRESH,
    SERVICE_TRACK_PARCEL,
    SERVICE_UNTRACK_PARCEL,
)
from .coordinator import STORAGE_VERSION, DHLParcelConfigEntry, DHLParcelNLCoordinator
from .crypto import decrypt_text, encrypt_text

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.EVENT]

# Configured via the UI only (required when async_setup is defined).
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)

TRACK_PARCEL_SCHEMA = vol.Schema(
    {
        vol.Required("tracking_code"): cv.string,
        vol.Optional("carrier", default="auto"): vol.In(["auto", *CARRIERS]),
        vol.Optional("postal_code"): cv.string,
    }
)
UNTRACK_PARCEL_SCHEMA = vol.Schema({vol.Required("tracking_code"): cv.string})


def _decrypt(hass: HomeAssistant, value: Any) -> str | None:
    if not value:
        return None
    try:
        return decrypt_text(hass, value) or None
    except Exception:
        return None


def _loaded_coordinators(hass: HomeAssistant) -> list[DHLParcelNLCoordinator]:
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the component and its services."""

    async def _async_service_refresh(call: ServiceCall) -> None:
        """Force refresh all coordinators."""
        for coordinator in _loaded_coordinators(hass):
            await coordinator.async_request_refresh()
            if coordinator.amazon_source is not None:
                hass.async_create_task(coordinator.async_poll_amazon(force=True))

    async def _async_service_track(call: ServiceCall) -> ServiceResponse:
        coordinators = _loaded_coordinators(hass)
        if not coordinators:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="not_loaded"
            )
        carrier = call.data.get("carrier", "auto")
        try:
            used = await coordinators[0].async_track_parcel(
                call.data["tracking_code"],
                None if carrier == "auto" else carrier,
                call.data.get("postal_code"),
            )
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="carrier_disabled",
                translation_placeholders={"error": str(err)},
            ) from err
        return {
            "tracking_code": call.data["tracking_code"].strip().upper(),
            "carrier": used,
        }

    async def _async_service_untrack(call: ServiceCall) -> None:
        removed = False
        for coordinator in _loaded_coordinators(hass):
            removed = (
                await coordinator.async_untrack_parcel(call.data["tracking_code"])
                or removed
            )
        if not removed:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_tracked",
                translation_placeholders={"tracking_code": call.data["tracking_code"]},
            )

    hass.services.async_register(
        DOMAIN, SERVICE_DHL_PARCEL_REFRESH, _async_service_refresh
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_TRACK_PARCEL,
        _async_service_track,
        schema=TRACK_PARCEL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UNTRACK_PARCEL,
        _async_service_untrack,
        schema=UNTRACK_PARCEL_SCHEMA,
    )
    return True


def _build_postnl_auth(hass: HomeAssistant, entry: ConfigEntry) -> PostNLAuth | None:
    token_blob = _decrypt(hass, entry.data.get(CONF_POSTNL_TOKEN_ENCRYPTED))
    if not token_blob:
        return None
    try:
        token = json.loads(token_blob)
    except ValueError:
        return None

    async def _save_token(new_token: dict[str, Any]) -> None:
        # Rotated refresh tokens are persisted encrypted; this changes only
        # entry.data, so the update listener does not reload the entry.
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_POSTNL_TOKEN_ENCRYPTED: encrypt_text(hass, json.dumps(new_token)),
            },
        )

    return PostNLAuth(async_get_clientsession(hass), token, _save_token)


def _build_amazon_source(hass: HomeAssistant, entry: ConfigEntry):
    options = entry.options
    if not options.get(CONF_AMAZON_ENABLED):
        return None
    from .sources.amazon import AmazonSource

    stores = list(options.get(CONF_AMAZON_STORES) or ["amazon.nl"])
    method = options.get(CONF_AMAZON_LOGIN_METHOD, AMAZON_LOGIN_PASSWORD)
    if method == AMAZON_LOGIN_COOKIES:
        try:
            cookies = json.loads(
                _decrypt(hass, options.get(CONF_AMAZON_COOKIES_ENCRYPTED)) or "{}"
            )
        except ValueError:
            cookies = {}
        if isinstance(cookies, list):  # 2.0.0 betas: one amazon.nl list
            cookies = {"amazon.nl": cookies}
        if not cookies:
            return None
        return AmazonSource(hass, entry.entry_id, cookies=cookies, stores=stores)
    if method in (AMAZON_LOGIN_BROWSER, AMAZON_LOGIN_BROWSER_PASTE):
        try:
            token = json.loads(
                _decrypt(hass, options.get(CONF_AMAZON_TOKEN_ENCRYPTED)) or "{}"
            )
        except ValueError:
            token = {}
        if not token.get("refresh_token"):
            return None
        return AmazonSource(
            hass, entry.entry_id, refresh_token=token["refresh_token"], stores=stores
        )
    email = _decrypt(hass, options.get(CONF_AMAZON_EMAIL_ENCRYPTED))
    password = _decrypt(hass, options.get(CONF_AMAZON_PASSWORD_ENCRYPTED))
    if not (email and password):
        return None
    return AmazonSource(
        hass,
        entry.entry_id,
        email,
        password,
        _decrypt(hass, options.get(CONF_AMAZON_OTP_ENCRYPTED)),
        stores=stores,
    )


async def async_setup_entry(hass: HomeAssistant, entry: DHLParcelConfigEntry) -> bool:
    """Set up Parcel Tracking NL from a config entry."""
    merged = {**entry.data, **entry.options}
    email = entry.options.get("email", entry.data.get("email"))
    password = entry.options.get("password", entry.data.get("password"))

    if not email and merged.get("email_encrypted"):
        email = _decrypt(hass, merged.get("email_encrypted", ""))
    if not password and merged.get("password_encrypted"):
        password = _decrypt(hass, merged.get("password_encrypted", ""))

    if email:
        email = email.strip()
        if "@" not in email:
            _LOGGER.warning(
                "Stored consumer email appears invalid; skipping consumer auth until reconfigured"
            )
            email = None

    tracking_codes = entry.options.get("tracking_codes", "")
    specs = (
        [code.strip() for code in tracking_codes.split(",") if code.strip()]
        if isinstance(tracking_codes, str)
        else list(tracking_codes)
    )
    tracking_code_list: list[str] = []
    manual_carriers: dict[str, str] = {}
    for spec in specs:
        carrier, code = parse_code_spec(spec)
        if not code:
            continue
        tracking_code_list.append(code)
        if carrier:
            manual_carriers[code] = carrier

    enabled = merged.get(CONF_ENABLED_CARRIERS)
    enabled_carriers = set(enabled) if enabled else set(CARRIERS)

    account_type = merged.get("account_type")
    coordinator = DHLParcelNLCoordinator(
        hass,
        config_entry=entry,
        account_type=account_type,
        user_id=merged.get("user_id") if account_type != "none" else None,
        api_key=merged.get("api_key") if account_type != "none" else None,
        email=email if account_type != "none" else None,
        password=password if account_type != "none" else None,
        postal_code=merged.get("postal_code"),
        delivered_keep_days=merged.get("delivered_keep_days", 7),
        refresh_interval_minutes=merged.get("refresh_interval_minutes", 5),
        refresh_start_time=merged.get("refresh_start_time", "06:00"),
        refresh_end_time=merged.get("refresh_end_time", "23:00"),
        summary_language=merged.get("summary_language", "en"),
        tracking_codes=tracking_code_list,
        manual_carriers=manual_carriers,
        enabled_carriers=enabled_carriers,
        postnl_auth=_build_postnl_auth(hass, entry),
        amazon_source=_build_amazon_source(hass, entry),
    )

    await coordinator.async_load_state()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    options_snapshot = dict(entry.options)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Fire queued events only when event entities exist and automations are
    # loaded (after HA start), so nothing detected during startup is lost.
    @callback
    def _enable_events(_hass: HomeAssistant) -> None:
        coordinator.async_enable_events()
        if coordinator.amazon_source is not None:
            # First poll after setup/reload ignores the refresh window, like
            # the first carrier refresh (so a new login is checked at once).
            entry.async_create_background_task(
                hass,
                coordinator.async_poll_amazon(force=True),
                "dhl_parcel_nl amazon poll",
            )

    entry.async_on_unload(async_at_started(hass, _enable_events))

    if coordinator.amazon_source is not None:
        minutes = min(
            60,
            max(
                30,
                int(merged.get(CONF_AMAZON_POLL_MINUTES, DEFAULT_AMAZON_POLL_MINUTES)),
            ),
        )

        @callback
        def _poll_amazon(_now) -> None:
            entry.async_create_background_task(
                hass, coordinator.async_poll_amazon(), "dhl_parcel_nl amazon poll"
            )

        entry.async_on_unload(
            async_track_time_interval(hass, _poll_amazon, timedelta(minutes=minutes))
        )

    # Relative day labels ("today", "tomorrow") are rendered when state is
    # written; re-render them after midnight even when no poll happens.
    @callback
    def _refresh_day_labels(_now) -> None:
        coordinator.async_update_listeners()

    entry.async_on_unload(
        async_track_time_change(hass, _refresh_day_labels, hour=0, minute=0, second=5)
    )

    async def _async_entry_updated(hass: HomeAssistant, updated: ConfigEntry) -> None:
        # Token rotation only changes entry.data; reload on options changes.
        if dict(updated.options) != options_snapshot:
            await hass.config_entries.async_reload(updated.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: DHLParcelConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = True
    try:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    except ValueError:
        # Entry may never have been fully loaded (e.g. setup failed before
        # platform forwarding); treat unload as successful to avoid noisy reloads.
        _LOGGER.debug(
            "Config entry %s was not loaded on platforms; skipping platform unload",
            entry.entry_id,
        )

    coordinator = getattr(entry, "runtime_data", None)
    if unload_ok and coordinator is not None:
        await coordinator.async_save_state()

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete persisted parcel state when the entry is removed."""
    await Store(
        hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}_state"
    ).async_remove()
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}_amazon").async_remove()


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
