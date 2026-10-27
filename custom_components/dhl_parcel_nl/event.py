"""Event entity platform for DHL Parcel NL.

This provides GUI-friendly event triggers in Home Assistant automations.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    DOMAIN,
    EVENT_DHL_PARCEL_DELIVERY_WINDOW_CHANGED,
    EVENT_DHL_PARCEL_DISCOVERED,
    EVENT_DHL_PARCEL_REMOVED,
    EVENT_DHL_PARCEL_STATUS_CHANGED,
    EVENT_DHL_PARCEL_SUBSTATUS_CHANGED,
)

EVENT_ENTITY_MAP: dict[str, str] = {
    EVENT_DHL_PARCEL_DISCOVERED: "Parcel Discovered",
    EVENT_DHL_PARCEL_STATUS_CHANGED: "Parcel Status Changed",
    EVENT_DHL_PARCEL_DELIVERY_WINDOW_CHANGED: "Parcel Delivery Window Changed",
    EVENT_DHL_PARCEL_SUBSTATUS_CHANGED: "Parcel Substatus Changed",
    EVENT_DHL_PARCEL_REMOVED: "Parcel Removed",
}

EVENT_TRANSLATION_KEY_MAP: dict[str, str] = {
    EVENT_DHL_PARCEL_DISCOVERED: "parcel_discovered",
    EVENT_DHL_PARCEL_STATUS_CHANGED: "parcel_status_changed",
    EVENT_DHL_PARCEL_DELIVERY_WINDOW_CHANGED: "parcel_delivery_window_changed",
    EVENT_DHL_PARCEL_SUBSTATUS_CHANGED: "parcel_substatus_changed",
    EVENT_DHL_PARCEL_REMOVED: "parcel_removed",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up DHL event entities."""
    entities = [
        DHLParcelEventEntity(
            hass=hass,
            entry_id=entry.entry_id,
            event_type=event_type,
            name_suffix=name_suffix,
        )
        for event_type, name_suffix in EVENT_ENTITY_MAP.items()
    ]
    async_add_entities(entities)


class DHLParcelEventEntity(EventEntity):
    """Represent a DHL Parcel event stream as an EventEntity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        event_type: str,
        name_suffix: str,
    ) -> None:
        """Initialize DHL event entity."""
        self._hass = hass
        self._entry_id = entry_id
        self._event_type = event_type
        self._attr_unique_id = f"{entry_id}_{event_type}"
        self._attr_name = f"DHL {name_suffix}"
        # Keep the entity_id used by earlier versions and the examples, also
        # for new installs after the device was renamed.
        self.entity_id = f"event.dhl_parcel_netherlands_{event_type}"
        self._attr_translation_key = EVENT_TRANSLATION_KEY_MAP[event_type]
        self._attr_event_types = [event_type]
        self._attr_icon = "mdi:bell-ring-outline"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for grouping in UI."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Parcel Tracking NL",
            manufacturer="DHL, PostNL, Dragonfly",
            model="Parcel Tracking",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to HA bus events when entity is added."""
        await super().async_added_to_hass()

        @callback
        def _handle_event(event: Event) -> None:
            event_data: dict[str, Any] = dict(event.data) if event.data else {}
            self._trigger_event(self._event_type, event_data)
            # Publishing the state is required, otherwise the entity stays
            # "unknown" and event.received / state triggers never fire.
            self.async_write_ha_state()

        self.async_on_remove(
            self._hass.bus.async_listen(self._event_type, _handle_event)
        )
