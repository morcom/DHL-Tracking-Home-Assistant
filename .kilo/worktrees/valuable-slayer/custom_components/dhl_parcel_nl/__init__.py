"""DHL Parcel Netherlands integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SERVICE_DHL_PARCEL_REFRESH
from .crypto import decrypt_text
from .coordinator import DHLParcelNLCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.EVENT]

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the DHL Parcel NL component."""
    hass.data.setdefault(DOMAIN, {})

    async def _async_service_refresh(call) -> None:
        """Force refresh all DHL Parcel coordinators."""
        coordinators = hass.data.get(DOMAIN, {})
        for key, coordinator in coordinators.items():
            if key == "entities":
                continue
            await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_DHL_PARCEL_REFRESH, _async_service_refresh
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DHL Parcel NL from a config entry."""
    merged = {**entry.data, **entry.options}
    email = entry.options.get("email", entry.data.get("email"))
    password = entry.options.get("password", entry.data.get("password"))

    if not email and merged.get("email_encrypted"):
        try:
            email = decrypt_text(hass, merged.get("email_encrypted", ""))
        except Exception:
            email = None

    if not password and merged.get("password_encrypted"):
        try:
            password = decrypt_text(hass, merged.get("password_encrypted", ""))
        except Exception:
            password = None

    tracking_codes = entry.options.get("tracking_codes", "")
    tracking_code_list = (
        [code.strip() for code in tracking_codes.split(",") if code.strip()]
        if isinstance(tracking_codes, str)
        else list(tracking_codes)
    )

    coordinator = DHLParcelNLCoordinator(
        hass,
        account_type=entry.options.get("account_type", entry.data.get("account_type")),
        user_id=entry.options.get("user_id", entry.data.get("user_id")),
        api_key=entry.options.get("api_key", entry.data.get("api_key")),
        email=email,
        password=password,
        postal_code=entry.options.get("postal_code", entry.data.get("postal_code")),
        delivered_keep_days=entry.options.get(
            "delivered_keep_days", entry.data.get("delivered_keep_days", 7)
        ),
        refresh_interval_minutes=entry.options.get(
            "refresh_interval_minutes", entry.data.get("refresh_interval_minutes", 5)
        ),
        refresh_start_time=entry.options.get(
            "refresh_start_time", entry.data.get("refresh_start_time", "06:00")
        ),
        refresh_end_time=entry.options.get(
            "refresh_end_time", entry.data.get("refresh_end_time", "23:00")
        ),
        summary_language=entry.options.get(
            "summary_language", entry.data.get("summary_language", "en")
        ),
        tracking_codes=tracking_code_list,
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
