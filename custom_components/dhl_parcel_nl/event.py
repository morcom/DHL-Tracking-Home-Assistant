"""Event entity platform for DHL Parcel NL.

This provides GUI-friendly event triggers in Home Assistant automations.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

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
    async_add_entities: AddEntitiesCallback,
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
        self._attr_translation_key = EVENT_TRANSLATION_KEY_MAP[event_type]
        self._attr_event_types = [event_type]
        self._attr_icon = "mdi:bell-ring-outline"
        self._unsub = None

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information for grouping in UI."""
        return {
            "identifiers": {(DOMAIN, self._entry_id)},
            "name": "DHL Parcel Netherlands",
            "manufacturer": "DHL eCommerce",
            "model": "Parcel Tracking",
        }

    async def async_added_to_hass(self) -> None:
        """Subscribe to HA bus events when entity is added."""
        await super().async_added_to_hass()

        @callback
        def _handle_event(event) -> None:
            event_data: dict[str, Any] = dict(event.data) if event.data else {}
            self._trigger_event(
                event_type=self._event_type,
                event_attributes=event_data,
            )

        self._unsub = self._hass.bus.async_listen(self._event_type, _handle_event)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe bus listener."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        await super().async_will_remove_from_hass()
