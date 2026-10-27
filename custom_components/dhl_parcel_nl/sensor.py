"""Sensor platform for DHL Parcel NL."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import (
    ATTR_BARCODE,
    ATTR_DELIVERED_AT,
    ATTR_DELIVERY_DATE,
    ATTR_DELIVERY_DAY_LABEL,
    ATTR_DELIVERY_LOCATION,
    ATTR_DELIVERY_TIME_FROM,
    ATTR_DELIVERY_TIME_TO,
    ATTR_DELIVERY_TIMEFRAME,
    ATTR_ESTIMATED_DELIVERY,
    ATTR_EVENT_COUNT,
    ATTR_EVENTS,
    ATTR_IS_DELIVERED,
    ATTR_LAST_EVENT_STATUS,
    ATTR_LAST_UPDATE_SOURCE,
    ATTR_LATEST_EVENT,
    ATTR_PARCEL_SHOP,
    ATTR_PRODUCT,
    ATTR_RAW_STATUS,
    ATTR_RECIPIENT,
    ATTR_SENDER,
    ATTR_SENDER_NAME,
    ATTR_SHIPPER_NAME,
    ATTR_STATUS,
    ATTR_STATUS_PL,
    ATTR_TRACKING_CODE,
    ATTR_WEIGHT,
    DOMAIN,
    STATUS_READY_FOR_PICKUP,
    status_for_lang,
    substatus_for_lang,
)
from .carriers import CARRIER_DHL, CARRIER_DISPLAY_NAMES
from .coordinator import DHLParcelNLCoordinator

DHL_TRACKING_URL = "https://www.dhlparcel.nl/nl/volg-uw-zending-0?tt={code}"


def _to_sender_name(sender: Any) -> str | None:
    """Normalize sender payload to display name."""
    if sender is None:
        return None
    if isinstance(sender, str):
        return sender
    if isinstance(sender, dict):
        return (
            sender.get("name") or sender.get("companyName") or sender.get("displayName")
        )
    return str(sender)


def _is_delivered(payload: dict[str, Any]) -> bool:
    """Return True when the parcel payload is delivered."""
    return bool(payload.get("is_delivered") or payload.get("delivered_at"))


def _sender_of(payload: dict[str, Any]) -> str | None:
    """Best available sender display name."""
    return (
        payload.get("sender_name")
        or payload.get("shipper_name")
        or _to_sender_name(payload.get("sender"))
    )


def _tracking_url(code: str, payload: dict[str, Any]) -> str | None:
    """Carrier tracking page of a parcel."""
    if payload.get("tracking_url"):
        return payload["tracking_url"]
    if _carrier_of(payload) == CARRIER_DHL:
        return DHL_TRACKING_URL.format(code=code)
    return None


def _single_carrier_label(carriers: Any) -> str | None:
    """Display name when exactly one carrier is involved, else None."""
    values = set(carriers or ())
    if len(values) == 1:
        carrier = next(iter(values))
        return CARRIER_DISPLAY_NAMES.get(carrier, carrier)
    return None


def _carrier_of(payload: dict[str, Any]) -> str:
    return payload.get("carrier") or CARRIER_DHL


def _parcel_unique_id(entry_id: str, code: str, carrier: str = CARRIER_DHL) -> str:
    """Unique id of a parcel sensor; DHL keeps the v1 format."""
    if carrier == CARRIER_DHL:
        return f"{entry_id}_{code}"
    return f"{entry_id}_{carrier}_{code}"


def _parcel_entity_id(
    hass: HomeAssistant | None,
    entry_id: str,
    code: str,
    carrier: str = CARRIER_DHL,
) -> str:
    """Return the real entity_id of a parcel sensor (respects user renames)."""
    if hass is not None:
        entity_id = er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, _parcel_unique_id(entry_id, code, carrier)
        )
        if entity_id:
            return entity_id
    return f"sensor.{slugify(f'{carrier}_parcel_{code}')}"


def _split_timeframe(timeframe: Any) -> tuple[str | None, str | None]:
    """Split DHL timeframe string to from/to ISO strings."""
    if isinstance(timeframe, str) and "/" in timeframe:
        start, end = timeframe.split("/", 1)
        return start, end
    if isinstance(timeframe, dict):
        return timeframe.get("from"), timeframe.get("to")
    return None, None


def _to_local(dt_iso: Any) -> datetime | None:
    """Parse ISO timestamp and convert it to the Home Assistant time zone.

    Naive timestamps are assumed to already be in the HA time zone.
    """
    if not isinstance(dt_iso, str) or not dt_iso:
        return None
    try:
        parsed = dt_util.parse_datetime(dt_iso)
    except ValueError:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_local(parsed)


def _pl_plural(count: int, one: str, few: str, many: str) -> str:
    """Pick Polish plural form for a count."""
    if count == 1:
        return one
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return few
    return many


def _delivery_day_label(start_iso: str | None) -> str | None:
    """Human-friendly day label for voice usage."""
    dt = _to_local(start_iso)
    if dt is None:
        return None
    try:
        today = dt_util.now().date()
        delta = (dt.date() - today).days
        if delta == 0:
            return "today"
        if delta == 1:
            return "tomorrow"
        if delta == 2:
            return "in two days"
        return dt.date().isoformat()
    except Exception:
        return None


def _delivery_day_label_pl(start_iso: str | None) -> str | None:
    """Polish day label for voice usage."""
    base = _delivery_day_label(start_iso)
    if base == "today":
        return "dzisiaj"
    if base == "tomorrow":
        return "jutro"
    if base == "in two days":
        return "pojutrze"
    return base


def _delivery_day_label_nl(start_iso: str | None) -> str | None:
    """Dutch day label for voice usage."""
    base = _delivery_day_label(start_iso)
    if base == "today":
        return "vandaag"
    if base == "tomorrow":
        return "morgen"
    if base == "in two days":
        return "overmorgen"
    return base


def _status_for_lang(status: str | None, lang: str, carrier: str | None = None) -> str:
    """Map status category to selected language (carrier aware)."""
    return status_for_lang(status, lang, carrier)


def _day_label_for_lang(start_iso: str | None, lang: str) -> str | None:
    """Map day label to selected language."""
    if lang == "pl":
        return _delivery_day_label_pl(start_iso)
    if lang == "nl":
        return _delivery_day_label_nl(start_iso)
    return _delivery_day_label(start_iso)


def _format_hhmm(dt_iso: str | None) -> str | None:
    """Format ISO datetime to HH:MM in the Home Assistant time zone."""
    dt = _to_local(dt_iso)
    return dt.strftime("%H:%M") if dt else None


def _past_day_label_for_lang(dt_iso: str | None, lang: str) -> str | None:
    """Return relative day label for delivered timestamps."""
    dt = _to_local(dt_iso)
    if dt is None:
        return None
    try:
        today = dt_util.now().date()
        diff = (dt.date() - today).days

        if lang == "pl":
            if diff == 0:
                return "dzisiaj"
            if diff == -1:
                return "wczoraj"
            if diff == -2:
                return "przedwczoraj"
            if diff < -2:
                return f"{-diff} dni temu"
            return dt.date().isoformat()

        if lang == "nl":
            if diff == 0:
                return "vandaag"
            if diff == -1:
                return "gisteren"
            if diff == -2:
                return "eergisteren"
            if diff < -2:
                return f"{-diff} dagen geleden"
            return dt.date().isoformat()

        if diff == 0:
            return "today"
        if diff == -1:
            return "yesterday"
        if diff == -2:
            return "two days ago"
        if diff < -2:
            return f"{-diff} days ago"
        return dt.date().isoformat()
    except Exception:
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up DHL sensors for this config entry."""
    coordinator: DHLParcelNLCoordinator = entry.runtime_data
    known_tracking_codes: set[str] = set()
    parcel_entities: dict[str, DHLParcelSensor] = {}

    def _cleanup_stale_registry_entities() -> None:
        """Remove sensor entities that are no longer provided."""
        registry = er.async_get(hass)
        valid_unique_ids = {
            _parcel_unique_id(entry.entry_id, code, coordinator.carrier_of(code))
            for code in coordinator.tracking_codes
        }
        valid_unique_ids.update(
            {
                f"{entry.entry_id}_dhl_parcel_count",
                f"{entry.entry_id}_dhl_tracking_details",
                f"{entry.entry_id}_dhl_delivered_details",
                f"{entry.entry_id}_dhl_parcel_voice_summary",
                f"{entry.entry_id}_dhl_parcel_delivered_summary",
            }
        )
        if coordinator.amazon_source is not None:
            valid_unique_ids.add(f"{entry.entry_id}_amazon_orders")

        for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity_entry.domain != "sensor":
                continue
            if entity_entry.platform != DOMAIN:
                continue
            if (
                entity_entry.unique_id
                and entity_entry.unique_id not in valid_unique_ids
            ):
                registry.async_remove(entity_entry.entity_id)

    def _build_entities() -> list[DHLParcelSensor]:
        entities: list[DHLParcelSensor] = []
        for tracking_code in coordinator.tracking_codes:
            if tracking_code in known_tracking_codes:
                continue
            entity = DHLParcelSensor(coordinator, tracking_code, entry.entry_id)
            entities.append(entity)
            parcel_entities[tracking_code] = entity
            known_tracking_codes.add(tracking_code)
        return entities

    entities = _build_entities()
    entities.append(DHLParcelCountSensor(coordinator, entry.entry_id))
    entities.append(DHLParcelTrackingDetailsSensor(coordinator, entry.entry_id))
    entities.append(DHLParcelDeliveredDetailsSensor(coordinator, entry.entry_id))
    entities.append(DHLParcelVoiceSummarySensor(coordinator, entry.entry_id))
    entities.append(DHLParcelDeliveredSummarySensor(coordinator, entry.entry_id))
    if coordinator.amazon_source is not None:
        entities.append(AmazonOrdersSensor(coordinator, entry.entry_id))
    if entities:
        async_add_entities(entities)

    _cleanup_stale_registry_entities()

    def _handle_coordinator_update() -> None:
        current_codes = set(coordinator.tracking_codes)
        # Removed parcels, and parcels whose carrier was resolved later
        # (ambiguous 3S codes): their unique id / entity id changes.
        stale_codes = [
            code
            for code, entity in list(parcel_entities.items())
            if code not in current_codes
            or entity._carrier != coordinator.carrier_of(code)
        ]
        if stale_codes:
            registry = er.async_get(hass)
            for code in stale_codes:
                entity = parcel_entities.pop(code, None)
                known_tracking_codes.discard(code)
                if entity and entity.entity_id:
                    registry.async_remove(entity.entity_id)

        _cleanup_stale_registry_entities()

        new_entities = _build_entities()
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_handle_coordinator_update))


class DHLParcelSensor(CoordinatorEntity[DHLParcelNLCoordinator], SensorEntity):
    """Representation of one tracked parcel (any carrier)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DHLParcelNLCoordinator,
        tracking_code: str,
        entry_id: str,
    ) -> None:
        """Initialize parcel sensor."""
        super().__init__(coordinator)
        self._tracking_code = tracking_code
        self._carrier = coordinator.carrier_of(tracking_code)
        self._attr_unique_id = _parcel_unique_id(entry_id, tracking_code, self._carrier)
        self._attr_name = (
            f"{CARRIER_DISPLAY_NAMES.get(self._carrier, self._carrier)} Parcel "
            f"{tracking_code}"
        )
        self._attr_icon = "mdi:package-variant-closed"

    @property
    def native_value(self) -> str | None:
        """Return parcel status."""
        data = (
            self.coordinator.data.get(self._tracking_code)
            if self.coordinator.data
            else None
        )
        if not data:
            return None
        raw = data.get("status_category", "UNKNOWN")
        lang = getattr(self.coordinator, "summary_language", "en")
        return _status_for_lang(raw, lang, self._carrier)

    @property
    def available(self) -> bool:
        """Return availability."""
        return bool(
            self.coordinator.data and self._tracking_code in self.coordinator.data
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return rich parcel details for automations/notifications."""
        data = (
            self.coordinator.data.get(self._tracking_code)
            if self.coordinator.data
            else None
        )
        if not data:
            return {}

        events = data.get("events") or []
        latest_event = events[-1] if events else None
        raw = data.get("status_category", "UNKNOWN")
        delivery_from, delivery_to = _split_timeframe(data.get("delivery_timeframe"))
        sender_name = (
            data.get("sender_name")
            or data.get("shipper_name")
            or _to_sender_name(data.get("sender"))
        )

        lang = getattr(self.coordinator, "summary_language", "en")
        carrier = self._carrier

        return {
            ATTR_TRACKING_CODE: self._tracking_code,
            "carrier": carrier,
            "carrier_name": CARRIER_DISPLAY_NAMES.get(carrier, carrier),
            "source": data.get("source")
            or self.coordinator.source_of(self._tracking_code),
            "order_id": data.get("order_id"),
            "store": data.get("store"),
            ATTR_BARCODE: data.get("barcode"),
            ATTR_STATUS: _status_for_lang(raw, lang, carrier),
            "status_en": _status_for_lang(raw, "en", carrier),
            ATTR_STATUS_PL: _status_for_lang(raw, "pl", carrier),
            "status_nl": _status_for_lang(raw, "nl", carrier),
            ATTR_RAW_STATUS: raw,
            ATTR_IS_DELIVERED: data.get("is_delivered", False),
            ATTR_DELIVERY_DATE: data.get("delivery_date"),
            ATTR_DELIVERY_TIMEFRAME: data.get("delivery_timeframe"),
            ATTR_DELIVERY_TIME_FROM: delivery_from,
            ATTR_DELIVERY_TIME_TO: delivery_to,
            "delivery_time_from_hhmm": _format_hhmm(delivery_from),
            "delivery_time_to_hhmm": _format_hhmm(delivery_to),
            ATTR_DELIVERY_DAY_LABEL: _day_label_for_lang(delivery_from, lang),
            "delivered_at_hhmm": _format_hhmm(data.get("delivered_at")),
            "delivered_day_label": _past_day_label_for_lang(
                data.get("delivered_at"), lang
            ),
            ATTR_ESTIMATED_DELIVERY: data.get("estimated_delivery"),
            ATTR_SENDER: sender_name,
            ATTR_SENDER_NAME: data.get("sender_name"),
            ATTR_SHIPPER_NAME: data.get("shipper_name"),
            ATTR_RECIPIENT: data.get("recipient"),
            ATTR_PRODUCT: data.get("product"),
            ATTR_WEIGHT: data.get("weight"),
            ATTR_PARCEL_SHOP: data.get("parcel_shop"),
            ATTR_DELIVERED_AT: data.get("delivered_at"),
            ATTR_DELIVERY_LOCATION: data.get("delivery_location"),
            ATTR_LAST_EVENT_STATUS: data.get("last_event_status"),
            "last_event_status_localized": substatus_for_lang(
                data.get("last_event_status"),
                lang,
                data.get("last_event_description"),
            ),
            ATTR_EVENTS: events[-10:],
            ATTR_EVENT_COUNT: len(events),
            ATTR_LATEST_EVENT: latest_event,
            ATTR_LAST_UPDATE_SOURCE: "dhl_parcel_nl",
            "tracking_url": _tracking_url(self._tracking_code, data),
            # Time of the last real change of this parcel's data (not of the
            # last poll), so the recorder does not store a new row every poll.
            "last_updated": self.coordinator.parcel_last_changed.get(
                self._tracking_code
            ),
        }


class DHLParcelCountSensor(CoordinatorEntity[DHLParcelNLCoordinator], SensorEntity):
    """Summary sensor with number of tracked DHL parcels."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator: DHLParcelNLCoordinator, entry_id: str) -> None:
        """Initialize count sensor."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_dhl_parcel_count"
        self._attr_name = "DHL Parcel Count"

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Enable by default for easier Assist setup."""
        return True

    @property
    def native_value(self) -> int:
        """Return number of active (not yet delivered) DHL parcels."""
        return sum(
            1
            for payload in (self.coordinator.data or {}).values()
            if not _is_delivered(payload)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra details for notifications and templates."""
        lang = getattr(self.coordinator, "summary_language", "en")
        items = []
        for code, payload in (self.coordinator.data or {}).items():
            raw = payload.get("status_category") or "UNKNOWN"
            carrier = _carrier_of(payload)
            delivery_from, delivery_to = _split_timeframe(
                payload.get("delivery_timeframe")
            )
            items.append(
                {
                    "tracking_code": code,
                    "carrier": carrier,
                    "source": payload.get("source"),
                    "entity_id": _parcel_entity_id(
                        self.hass, self._entry_id, code, carrier
                    ),
                    "status": raw,
                    "status_localized": _status_for_lang(raw, lang, carrier),
                    "status_en": _status_for_lang(raw, "en", carrier),
                    "status_pl": _status_for_lang(raw, "pl", carrier),
                    "status_nl": _status_for_lang(raw, "nl", carrier),
                    "is_delivered": _is_delivered(payload),
                    "sender": _sender_of(payload),
                    "delivery_date": payload.get("delivery_date"),
                    "delivery_time_from": delivery_from,
                    "delivery_time_to": delivery_to,
                    "delivery_time_from_hhmm": _format_hhmm(delivery_from),
                    "delivery_time_to_hhmm": _format_hhmm(delivery_to),
                    "delivery_day_label": _delivery_day_label(delivery_from),
                    "delivery_day_label_pl": _delivery_day_label_pl(delivery_from),
                    "delivery_day_label_nl": _delivery_day_label_nl(delivery_from),
                    "delivery_day_label_localized": _day_label_for_lang(
                        delivery_from, lang
                    ),
                    "delivered_at": payload.get("delivered_at"),
                    "delivered_at_hhmm": _format_hhmm(payload.get("delivered_at")),
                    "delivered_day_label": _past_day_label_for_lang(
                        payload.get("delivered_at"), lang
                    ),
                    "delivery_location": payload.get("delivery_location"),
                }
            )

        active_count = sum(1 for item in items if not item["is_delivered"])
        return {
            "summary_language": lang,
            "active_count": active_count,
            "delivered_count": len(items) - active_count,
            "total_count": len(items),
            "tracked_codes": [item["tracking_code"] for item in items],
            "parcel_entity_ids": [item["entity_id"] for item in items],
            "parcels": items,
        }


class DHLParcelVoiceSummarySensor(
    CoordinatorEntity[DHLParcelNLCoordinator], SensorEntity
):
    """Voice-friendly summary sensor for Assist and LLMs."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:text-box-search"

    def __init__(self, coordinator: DHLParcelNLCoordinator, entry_id: str) -> None:
        """Initialize voice summary sensor."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_dhl_parcel_voice_summary"
        self._attr_name = "DHL Parcel Voice Summary"

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Enable by default for easier Assist setup."""
        return True

    def _build_parcels_payload(self, lang: str) -> list[dict[str, Any]]:
        """Build normalized parcel payload list for voice summaries."""
        parcels_data = self.coordinator.data or {}
        parcels: list[dict[str, Any]] = []

        for code, payload in parcels_data.items():
            if _is_delivered(payload):
                continue
            delivery_from, delivery_to = _split_timeframe(
                payload.get("delivery_timeframe")
            )
            carrier = _carrier_of(payload)
            raw = payload.get("status_category") or "UNKNOWN"
            parcels.append(
                {
                    "tracking_code": code,
                    "carrier": carrier,
                    "carrier_name": CARRIER_DISPLAY_NAMES.get(carrier, carrier),
                    "entity_id": _parcel_entity_id(
                        self.hass, self._entry_id, code, carrier
                    ),
                    "sender": _sender_of(payload),
                    "status": payload.get("status_category"),
                    "status_en": _status_for_lang(raw, "en", carrier),
                    "status_pl": _status_for_lang(raw, "pl", carrier),
                    "status_nl": _status_for_lang(raw, "nl", carrier),
                    "status_localized": _status_for_lang(raw, lang, carrier),
                    "delivery_time_from": delivery_from,
                    "delivery_time_to": delivery_to,
                    "delivery_day_label": _delivery_day_label(delivery_from),
                    "delivery_day_label_pl": _delivery_day_label_pl(delivery_from),
                    "delivery_day_label_nl": _delivery_day_label_nl(delivery_from),
                    "delivery_day_label_localized": _day_label_for_lang(
                        delivery_from, lang
                    ),
                    "delivery_time_from_hhmm": _format_hhmm(delivery_from),
                    "delivery_time_to_hhmm": _format_hhmm(delivery_to),
                    "delivery_location": payload.get("delivery_location"),
                    "substatus": payload.get("last_event_status"),
                    "substatus_localized": substatus_for_lang(
                        payload.get("last_event_status"),
                        lang,
                        payload.get("last_event_description"),
                    ),
                }
            )

        def _sort_key(item: dict[str, Any]) -> tuple[int, str]:
            start = item.get("delivery_time_from")
            if start:
                return (0, str(start))
            return (1, item.get("tracking_code", ""))

        parcels.sort(key=_sort_key)
        return parcels

    def _build_voice_summaries(self, parcels: list[dict[str, Any]]) -> dict[str, str]:
        """Build multilingual voice summaries with parcel details.

        With parcels of one carrier the carrier is named once in the intro
        ("You have 2 DHL parcels."); with mixed carriers every parcel names
        its carrier ("PostNL package 1 from ...").
        """
        enabled = getattr(self.coordinator, "enabled_carriers", {CARRIER_DHL})
        if not parcels:
            label = _single_carrier_label(enabled)
            if label:
                return {
                    "en": f"No active {label} parcels.",
                    "pl": f"Brak aktywnych paczek {label}.",
                    "nl": f"Geen actieve {label}-pakketten.",
                }
            return {
                "en": "No active parcels.",
                "pl": "Brak aktywnych paczek.",
                "nl": "Geen actieve pakketten.",
            }

        count = len(parcels)
        carriers = {p.get("carrier") or CARRIER_DHL for p in parcels}
        label = _single_carrier_label(carriers)
        mixed = label is None
        if mixed:
            parts_en = [f"You have {count} {'parcel' if count == 1 else 'parcels'}."]
            parts_pl = [
                f"Masz {count} {_pl_plural(count, 'paczkę', 'paczki', 'paczek')}."
            ]
            parts_nl = [f"Je hebt {count} {'pakket' if count == 1 else 'pakketten'}."]
        else:
            parts_en = [
                f"You have {count} {label} {'parcel' if count == 1 else 'parcels'}."
            ]
            parts_pl = [
                f"Masz {count} {_pl_plural(count, 'paczkę', 'paczki', 'paczek')} {label}."
            ]
            parts_nl = [
                f"Je hebt {count} {label}-{'pakket' if count == 1 else 'pakketten'}."
            ]

        for index, p in enumerate(parcels, start=1):
            carrier = p.get("carrier") or CARRIER_DHL
            carrier_name = CARRIER_DISPLAY_NAMES.get(carrier, carrier)
            if mixed:
                pkg_en = f"{carrier_name} package {index}"
                pkg_pl = f"Paczka {index} {carrier_name}"
                pkg_nl = f"{carrier_name}-pakket {index}"
            else:
                pkg_en = f"Package {index}"
                pkg_pl = f"Paczka {index}"
                pkg_nl = f"Pakket {index}"
            sender_en = p.get("sender") or "unknown sender"
            sender_pl = p.get("sender") or "nieznanego nadawcy"
            sender_nl = p.get("sender") or "onbekende afzender"
            status_en = p.get("status_en") or p.get("status") or "unknown"
            status_pl = p.get("status_pl") or p.get("status") or "nieznany"
            status_nl = p.get("status_nl") or p.get("status") or "onbekend"
            from_hhmm = p.get("delivery_time_from_hhmm")
            to_hhmm = p.get("delivery_time_to_hhmm")
            day_en = p.get("delivery_day_label") or ""
            day_pl = p.get("delivery_day_label_pl") or day_en
            day_nl = p.get("delivery_day_label_nl") or day_en
            location = p.get("delivery_location")

            if p.get("status") == STATUS_READY_FOR_PICKUP:
                if carrier == CARRIER_DHL:
                    point_en = "a DHL ParcelShop"
                    point_pl = "w punkcie DHL"
                    point_nl = "een DHL ParcelShop"
                else:
                    point_en = f"a {carrier_name} pickup point"
                    point_pl = f"w punkcie odbioru {carrier_name}"
                    point_nl = f"een {carrier_name}-afhaalpunt"
                where_en = f": {location}" if location else ""
                parts_en.append(
                    f"{pkg_en} from {sender_en} is waiting for pickup at {point_en}{where_en}."
                )
                parts_pl.append(
                    f"{pkg_pl} od {sender_pl} czeka na odbiór {point_pl}"
                    f"{f', adres: {location}' if location else ''}."
                )
                parts_nl.append(
                    f"{pkg_nl} van {sender_nl} ligt klaar bij {point_nl}"
                    f"{f', adres: {location}' if location else ''}."
                )
            elif from_hhmm and to_hhmm:
                parts_en.append(
                    f"{pkg_en} from {sender_en} is {status_en} and is expected {day_en} between {from_hhmm} and {to_hhmm}."
                )
                parts_pl.append(
                    f"{pkg_pl} od {sender_pl} ma status {status_pl} i jest przewidywana {day_pl} między {from_hhmm} a {to_hhmm}."
                )
                parts_nl.append(
                    f"{pkg_nl} van {sender_nl} heeft status {status_nl} en wordt verwacht {day_nl} tussen {from_hhmm} en {to_hhmm}."
                )
            elif from_hhmm:
                parts_en.append(
                    f"{pkg_en} from {sender_en} is {status_en} and is expected {day_en} at about {from_hhmm}."
                )
                parts_pl.append(
                    f"{pkg_pl} od {sender_pl} ma status {status_pl} i jest przewidywana {day_pl} około {from_hhmm}."
                )
                parts_nl.append(
                    f"{pkg_nl} van {sender_nl} heeft status {status_nl} en wordt verwacht {day_nl} rond {from_hhmm}."
                )
            else:
                parts_en.append(f"{pkg_en} from {sender_en} is {status_en}.")
                parts_pl.append(f"{pkg_pl} od {sender_pl} ma status {status_pl}.")
                parts_nl.append(f"{pkg_nl} van {sender_nl} heeft status {status_nl}.")

        return {
            "en": " ".join(parts_en),
            "pl": " ".join(parts_pl),
            "nl": " ".join(parts_nl),
        }

    @property
    def native_value(self) -> str:
        """Return localized summary state (short)."""
        lang = getattr(self.coordinator, "summary_language", "en")
        parcels = self._build_parcels_payload(lang)
        summaries = self._build_voice_summaries(parcels)
        value = summaries.get(lang, summaries["en"])
        return value[:250] if len(value) > 250 else value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return rich summary for assistants."""
        lang = getattr(self.coordinator, "summary_language", "en")
        parcels = self._build_parcels_payload(lang)
        summaries = self._build_voice_summaries(parcels)
        summary_en = summaries["en"]
        summary_pl = summaries["pl"]
        summary_nl = summaries["nl"]
        summary_localized = summaries.get(lang, summary_en)

        return {
            "parcel_count": len(parcels),
            "parcels": parcels,
            "summary_language": lang,
            "voice_summary": summary_localized,
            "voice_summary_en": summary_en,
            "voice_summary_pl": summary_pl,
            "voice_summary_nl": summary_nl,
            "recommended_voice_entities": [
                "sensor.dhl_parcel_count",
                "sensor.dhl_tracking_details",
                "sensor.dhl_delivered_details",
                "sensor.dhl_parcel_voice_summary",
                "sensor.dhl_parcel_delivered_summary",
            ],
        }


class DHLParcelTrackingDetailsSensor(
    CoordinatorEntity[DHLParcelNLCoordinator], SensorEntity
):
    """Tracking-details matrix for voice assistants."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:format-list-bulleted"

    def __init__(self, coordinator: DHLParcelNLCoordinator, entry_id: str) -> None:
        """Initialize tracking details sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_dhl_tracking_details"
        self._attr_name = "DHL Tracking Details"

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Enable by default for easier Assist setup."""
        return True

    def _build_rows(self, lang: str) -> list[dict[str, Any]]:
        """Build details rows keyed by tracking code."""
        rows: list[dict[str, Any]] = []
        for code, payload in (self.coordinator.data or {}).items():
            if payload.get("is_delivered") or payload.get("delivered_at"):
                continue
            sender = (
                payload.get("sender_name")
                or payload.get("shipper_name")
                or _to_sender_name(payload.get("sender"))
                or "unknown sender"
            )
            delivery_from, delivery_to = _split_timeframe(
                payload.get("delivery_timeframe")
            )
            rows.append(
                {
                    "tracking_code": code,
                    "carrier": _carrier_of(payload),
                    "carrier_name": CARRIER_DISPLAY_NAMES.get(
                        _carrier_of(payload), _carrier_of(payload)
                    ),
                    "tracking_url": _tracking_url(code, payload),
                    "sender": sender,
                    "status": payload.get("status_category"),
                    "status_localized": _status_for_lang(
                        payload.get("status_category"), lang, _carrier_of(payload)
                    ),
                    "delivery_time_from": delivery_from,
                    "delivery_time_to": delivery_to,
                    "delivery_time_from_hhmm": _format_hhmm(delivery_from),
                    "delivery_time_to_hhmm": _format_hhmm(delivery_to),
                    "delivery_day_label": _day_label_for_lang(delivery_from, lang),
                    "substatus": payload.get("last_event_status"),
                    "substatus_localized": substatus_for_lang(
                        payload.get("last_event_status"),
                        lang,
                        payload.get("last_event_description"),
                    ),
                    "delivered_at": payload.get("delivered_at"),
                    "delivery_location": payload.get("delivery_location"),
                    "delivered_at_hhmm": _format_hhmm(payload.get("delivered_at")),
                }
            )

        rows.sort(key=lambda item: item.get("tracking_code", ""))
        return rows

    @property
    def native_value(self) -> str:
        """Return compact comma-separated tracking and sender pairs."""
        rows = self._build_rows(getattr(self.coordinator, "summary_language", "en"))
        if not rows:
            return "No tracking numbers"
        compact = ", ".join(f"{row['tracking_code']}:{row['sender']}" for row in rows)
        return compact[:250] if len(compact) > 250 else compact

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return matrix/list details for AI voice assistants."""
        lang = getattr(self.coordinator, "summary_language", "en")
        rows = self._build_rows(lang)

        csv_simple = ", ".join(
            f"{row['tracking_code']}|{row['sender']}" for row in rows
        )
        csv_extended = ", ".join(
            (
                f"{row['tracking_code']}|{row['sender']}|{row['status_localized']}"
                f"|{row.get('delivery_day_label') or ''}"
                f"|{row.get('delivery_time_from_hhmm') or ''}-{row.get('delivery_time_to_hhmm') or ''}"
            )
            for row in rows
        )

        by_tracking = {row["tracking_code"]: row for row in rows}

        return {
            "summary_language": lang,
            "tracking_count": len(rows),
            "dhl_tracking_numbers": csv_simple,
            "dhl_tracking_numbers_extended": csv_extended,
            "tracking_numbers": [row["tracking_code"] for row in rows],
            "tracking_details": rows,
            "tracking_by_number": by_tracking,
            "recommended_voice_use": "Use tracking_details or tracking_by_number for precise answers",
        }


class DHLParcelDeliveredDetailsSensor(
    CoordinatorEntity[DHLParcelNLCoordinator], SensorEntity
):
    """Delivered tracking-details matrix for voice assistants."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:package-variant-closed-check"

    def __init__(self, coordinator: DHLParcelNLCoordinator, entry_id: str) -> None:
        """Initialize delivered details sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_dhl_delivered_details"
        self._attr_name = "DHL Delivered Details"

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Enable by default for easier Assist setup."""
        return True

    def _build_rows(self, lang: str) -> list[dict[str, Any]]:
        """Build delivered details rows keyed by tracking code."""
        rows: list[dict[str, Any]] = []
        for code, payload in (self.coordinator.data or {}).items():
            if not (payload.get("is_delivered") or payload.get("delivered_at")):
                continue

            sender = (
                payload.get("sender_name")
                or payload.get("shipper_name")
                or _to_sender_name(payload.get("sender"))
                or "unknown sender"
            )
            delivered_at = payload.get("delivered_at")
            rows.append(
                {
                    "tracking_code": code,
                    "carrier": _carrier_of(payload),
                    "carrier_name": CARRIER_DISPLAY_NAMES.get(
                        _carrier_of(payload), _carrier_of(payload)
                    ),
                    "tracking_url": _tracking_url(code, payload),
                    "sender": sender,
                    "status": payload.get("status_category"),
                    "status_localized": _status_for_lang(
                        payload.get("status_category"), lang, _carrier_of(payload)
                    ),
                    "delivered_at": delivered_at,
                    "delivered_at_hhmm": _format_hhmm(delivered_at),
                    "delivered_day_label": _past_day_label_for_lang(delivered_at, lang),
                    "delivery_location": payload.get("delivery_location"),
                }
            )

        rows.sort(key=lambda item: str(item.get("delivered_at") or ""), reverse=True)
        return rows

    @property
    def native_value(self) -> str:
        """Return compact comma-separated delivered tracking and sender pairs."""
        rows = self._build_rows(getattr(self.coordinator, "summary_language", "en"))
        if not rows:
            return "No delivered tracking numbers"
        compact = ", ".join(
            f"{r['tracking_code']}:{r['sender']}:{r.get('delivered_day_label') or ''}:{r.get('delivered_at_hhmm') or ''}"
            for r in rows
        )
        return compact[:250] if len(compact) > 250 else compact

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return delivered matrix/list details for AI voice assistants."""
        lang = getattr(self.coordinator, "summary_language", "en")
        rows = self._build_rows(lang)
        by_tracking = {row["tracking_code"]: row for row in rows}
        csv_simple = ", ".join(
            f"{row['tracking_code']}|{row['sender']}|{row.get('delivered_day_label') or ''}|{row.get('delivered_at_hhmm') or ''}"
            for row in rows
        )
        csv_extended = ", ".join(
            (
                f"{row['tracking_code']}|{row['sender']}|{row.get('status_localized') or ''}"
                f"|{row.get('delivered_day_label') or ''}|{row.get('delivered_at_hhmm') or ''}"
                f"|{row.get('delivery_location') or ''}"
            )
            for row in rows
        )

        return {
            "summary_language": lang,
            "delivered_count": len(rows),
            "dhl_delivered_numbers": csv_simple,
            "dhl_delivered_numbers_extended": csv_extended,
            "delivered_tracking_numbers": [row["tracking_code"] for row in rows],
            "delivered_details": rows,
            "delivered_by_number": by_tracking,
            "recommended_voice_use": "Use delivered_details or delivered_by_number for delivered-package answers",
        }


class DHLParcelDeliveredSummarySensor(
    CoordinatorEntity[DHLParcelNLCoordinator], SensorEntity
):
    """Voice-friendly summary for delivered parcels only."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:text-box-check"

    def __init__(self, coordinator: DHLParcelNLCoordinator, entry_id: str) -> None:
        """Initialize delivered summary sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_dhl_parcel_delivered_summary"
        self._attr_name = "DHL Parcel Delivered Summary"

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Enable by default for easier Assist setup."""
        return True

    def _rows(self, lang: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for code, payload in (self.coordinator.data or {}).items():
            if not (payload.get("is_delivered") or payload.get("delivered_at")):
                continue
            sender = (
                payload.get("sender_name")
                or payload.get("shipper_name")
                or _to_sender_name(payload.get("sender"))
                or "unknown sender"
            )
            rows.append(
                {
                    "tracking_code": code,
                    "carrier": _carrier_of(payload),
                    "carrier_name": CARRIER_DISPLAY_NAMES.get(
                        _carrier_of(payload), _carrier_of(payload)
                    ),
                    "tracking_url": _tracking_url(code, payload),
                    "sender": sender,
                    "status_localized": _status_for_lang(
                        payload.get("status_category"), lang, _carrier_of(payload)
                    ),
                    "delivered_at": payload.get("delivered_at"),
                    "delivered_at_hhmm": _format_hhmm(payload.get("delivered_at")),
                    "delivered_day_label": _past_day_label_for_lang(
                        payload.get("delivered_at"), lang
                    ),
                    "delivery_location": payload.get("delivery_location"),
                }
            )
        rows.sort(key=lambda x: str(x.get("delivered_at") or ""), reverse=True)
        return rows

    def _summary(self, rows: list[dict[str, Any]], lang: str) -> str:
        enabled = getattr(self.coordinator, "enabled_carriers", {CARRIER_DHL})
        if not rows:
            label = _single_carrier_label(enabled)
            if lang == "pl":
                return f"Brak doręczonych paczek{f' {label}' if label else ''}."
            if lang == "nl":
                return f"Geen bezorgde {f'{label}-' if label else ''}pakketten."
            return f"No delivered{f' {label}' if label else ''} parcels."

        label = _single_carrier_label({r.get("carrier") or CARRIER_DHL for r in rows})
        mixed = label is None

        def _pkg(i: int, r: dict[str, Any], en: str, pl: str, nl: str) -> str:
            name = CARRIER_DISPLAY_NAMES.get(r.get("carrier"), r.get("carrier") or "")
            if not mixed:
                return {"pl": f"{pl} {i}", "nl": f"{nl} {i}"}.get(lang, f"{en} {i}")
            return {
                "pl": f"{pl} {i} {name}",
                "nl": f"{name}-{nl.lower()} {i}",
            }.get(lang, f"{name} {en.lower()} {i}")

        if lang == "pl":
            count = len(rows)
            noun = _pl_plural(
                count,
                "doręczoną paczkę",
                "doręczone paczki",
                "doręczonych paczek",
            )
            parts = [f"Masz {count} {noun}{f' {label}' if label else ''}."]
            for i, r in enumerate(rows, start=1):
                parts.append(
                    f"{_pkg(i, r, 'Package', 'Paczka', 'Pakket')} od {r['sender']} doręczona {r.get('delivered_day_label') or 'niedawno'} o {r.get('delivered_at_hhmm') or 'nieznanej godzinie'}, lokalizacja {r.get('delivery_location') or 'brak danych'}."
                )
            return " ".join(parts)

        if lang == "nl":
            noun = "pakket" if len(rows) == 1 else "pakketten"
            parts = [
                f"Je hebt {len(rows)} bezorgde {f'{label}-' if label else ''}{noun}."
            ]
            for i, r in enumerate(rows, start=1):
                parts.append(
                    f"{_pkg(i, r, 'Package', 'Paczka', 'Pakket')} van {r['sender']} bezorgd {r.get('delivered_day_label') or 'recent'} om {r.get('delivered_at_hhmm') or 'onbekende tijd'}, locatie {r.get('delivery_location') or 'geen gegevens'}."
                )
            return " ".join(parts)

        noun = "parcel" if len(rows) == 1 else "parcels"
        parts = [
            f"You have {len(rows)} delivered{f' {label}' if label else ''} {noun}."
        ]
        for i, r in enumerate(rows, start=1):
            parts.append(
                f"{_pkg(i, r, 'Package', 'Paczka', 'Pakket')} from {r['sender']} delivered {r.get('delivered_day_label') or 'recently'} at {r.get('delivered_at_hhmm') or 'unknown time'}, location {r.get('delivery_location') or 'no data'}."
            )
        return " ".join(parts)

    @property
    def native_value(self) -> str:
        lang = getattr(self.coordinator, "summary_language", "en")
        value = self._summary(self._rows(lang), lang)
        return value[:250] if len(value) > 250 else value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        lang = getattr(self.coordinator, "summary_language", "en")
        rows = self._rows(lang)
        summary = self._summary(rows, lang)
        return {
            "summary_language": lang,
            "delivered_count": len(rows),
            "delivered": rows,
            "voice_summary": summary,
        }


class AmazonOrdersSensor(CoordinatorEntity[DHLParcelNLCoordinator], SensorEntity):
    """Diagnostic sensor: result of the last Amazon.nl order poll."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:cart-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DHLParcelNLCoordinator, entry_id: str) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_amazon_orders"
        self._attr_name = "Amazon Orders"

    @property
    def native_value(self) -> str:
        """Return ok, waiting, auth_required, unavailable or error."""
        return str(self.coordinator.amazon_status.get("state") or "waiting")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Details of the last poll."""
        status = dict(self.coordinator.amazon_status)
        status.pop("state", None)
        return status
