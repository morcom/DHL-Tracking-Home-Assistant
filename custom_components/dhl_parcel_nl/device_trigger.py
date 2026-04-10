"""Device triggers for DHL Parcel NL integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import (
    DOMAIN,
    EVENT_DHL_PARCEL_DELIVERY_WINDOW_CHANGED,
    EVENT_DHL_PARCEL_DISCOVERED,
    EVENT_DHL_PARCEL_REMOVED,
    EVENT_DHL_PARCEL_STATUS_CHANGED,
    EVENT_DHL_PARCEL_SUBSTATUS_CHANGED,
)

TRIGGER_TYPE_NEW_PARCEL = "new_parcel"
TRIGGER_TYPE_STATUS_CHANGED = "status_changed"
TRIGGER_TYPE_DELIVERY_WINDOW_CHANGED = "delivery_window_changed"
TRIGGER_TYPE_SUBSTATUS_CHANGED = "substatus_changed"
TRIGGER_TYPE_PARCEL_REMOVED = "parcel_removed"

TRIGGER_EVENT_MAP: dict[str, str] = {
    TRIGGER_TYPE_NEW_PARCEL: EVENT_DHL_PARCEL_DISCOVERED,
    TRIGGER_TYPE_STATUS_CHANGED: EVENT_DHL_PARCEL_STATUS_CHANGED,
    TRIGGER_TYPE_DELIVERY_WINDOW_CHANGED: EVENT_DHL_PARCEL_DELIVERY_WINDOW_CHANGED,
    TRIGGER_TYPE_SUBSTATUS_CHANGED: EVENT_DHL_PARCEL_SUBSTATUS_CHANGED,
    TRIGGER_TYPE_PARCEL_REMOVED: EVENT_DHL_PARCEL_REMOVED,
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_PLATFORM): "device",
        vol.Required(CONF_DOMAIN): DOMAIN,
        vol.Required(CONF_DEVICE_ID): cv.string,
        vol.Required(CONF_TYPE): vol.In(list(TRIGGER_EVENT_MAP)),
    }
)


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List device triggers for a device."""
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        return []

    if not any(identifier[0] == DOMAIN for identifier in device.identifiers):
        return []

    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: trigger_type,
        }
        for trigger_type in TRIGGER_EVENT_MAP
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: dict[str, Any],
    action,
    trigger_info,
) -> CALLBACK_TYPE:
    """Attach a device trigger."""
    config = TRIGGER_SCHEMA(config)
    event_type = TRIGGER_EVENT_MAP[config[CONF_TYPE]]

    event_config = {
        CONF_PLATFORM: "event",
        event_trigger.CONF_EVENT_TYPE: event_type,
    }

    return await event_trigger.async_attach_trigger(
        hass,
        event_config,
        action,
        trigger_info,
        platform_type="device",
    )


async def async_get_trigger_capabilities(
    hass: HomeAssistant, config: dict[str, Any]
) -> dict[str, vol.Schema]:
    """List trigger capabilities."""
    return {"extra_fields": vol.Schema({})}
