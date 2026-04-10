"""Sensor platform for DHL Parcel NL."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    ATTR_BARCODE,
    ATTR_DELIVERED_AT,
    ATTR_DELIVERY_DATE,
    ATTR_DELIVERY_LOCATION,
    ATTR_DELIVERY_DAY_LABEL,
    ATTR_DELIVERY_TIMEFRAME,
    ATTR_DELIVERY_TIME_FROM,
    ATTR_DELIVERY_TIME_TO,
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
    ATTR_STATUS_PL,
    ATTR_STATUS,
    ATTR_TRACKING_CODE,
    ATTR_WEIGHT,
    DOMAIN,
    STATUS_CATEGORIES,
    STATUS_CATEGORIES_NL,
    STATUS_CATEGORIES_PL,
)
from .coordinator import DHLParcelNLCoordinator


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


def _split_timeframe(timeframe: Any) -> tuple[str | None, str | None]:
    """Split DHL timeframe string to from/to ISO strings."""
    if isinstance(timeframe, str) and "/" in timeframe:
        start, end = timeframe.split("/", 1)
        return start, end
    if isinstance(timeframe, dict):
        return timeframe.get("from"), timeframe.get("to")
    return None, None


def _delivery_day_label(start_iso: str | None) -> str | None:
    """Human-friendly day label for voice usage."""
    if not start_iso:
        return None
    try:
        dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        today = datetime.now(dt.tzinfo).date() if dt.tzinfo else datetime.now().date()
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


def _status_for_lang(status: str | None, lang: str) -> str:
    """Map status category to selected language."""
    code = status or "UNKNOWN"
    if lang == "pl":
        return STATUS_CATEGORIES_PL.get(code, code)
    if lang == "nl":
        return STATUS_CATEGORIES_NL.get(code, code)
    return STATUS_CATEGORIES.get(code, code)


def _day_label_for_lang(start_iso: str | None, lang: str) -> str | None:
    """Map day label to selected language."""
    if lang == "pl":
        return _delivery_day_label_pl(start_iso)
    if lang == "nl":
        return _delivery_day_label_nl(start_iso)
    return _delivery_day_label(start_iso)


def _format_hhmm(dt_iso: str | None) -> str | None:
    """Format ISO datetime to HH:MM local-ish display."""
    if not dt_iso:
        return None
    try:
        dt = datetime.fromisoformat(dt_iso.replace("Z", "+00:00"))
        return dt.strftime("%H:%M")
    except Exception:
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up DHL sensors for this config entry."""
    coordinator: DHLParcelNLCoordinator = hass.data[DOMAIN][entry.entry_id]
    known_tracking_codes: set[str] = set()
    parcel_entities: dict[str, DHLParcelSensor] = {}

    def _cleanup_stale_registry_entities() -> None:
        """Remove sensor entities that are no longer provided."""
        registry = er.async_get(hass)
        current_codes = set(coordinator.tracking_codes)
        valid_unique_ids = {f"{entry.entry_id}_{code}" for code in current_codes}
        valid_unique_ids.update(
            {
                f"{entry.entry_id}_dhl_parcel_count",
                f"{entry.entry_id}_dhl_tracking_details",
                f"{entry.entry_id}_dhl_delivered_details",
                f"{entry.entry_id}_dhl_parcel_voice_summary",
                f"{entry.entry_id}_dhl_parcel_delivered_summary",
            }
        )

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
    if entities:
        async_add_entities(entities)

    _cleanup_stale_registry_entities()

    def _handle_coordinator_update() -> None:
        current_codes = set(coordinator.tracking_codes)
        stale_codes = [
            code for code in list(parcel_entities) if code not in current_codes
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
    """Representation of a DHL parcel."""

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
        self._attr_unique_id = f"{entry_id}_{tracking_code}"
        self._attr_name = f"DHL Parcel {tracking_code}"
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
        return _status_for_lang(raw, lang)

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

        return {
            ATTR_TRACKING_CODE: self._tracking_code,
            ATTR_BARCODE: data.get("barcode"),
            ATTR_STATUS: _status_for_lang(
                raw, getattr(self.coordinator, "summary_language", "en")
            ),
            ATTR_STATUS_PL: STATUS_CATEGORIES_PL.get(raw, raw),
            ATTR_RAW_STATUS: raw,
            ATTR_IS_DELIVERED: data.get("is_delivered", False),
            ATTR_DELIVERY_DATE: data.get("delivery_date"),
            ATTR_DELIVERY_TIMEFRAME: data.get("delivery_timeframe"),
            ATTR_DELIVERY_TIME_FROM: delivery_from,
            ATTR_DELIVERY_TIME_TO: delivery_to,
            ATTR_DELIVERY_DAY_LABEL: _delivery_day_label(delivery_from),
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
            ATTR_EVENTS: events[-10:],
            ATTR_EVENT_COUNT: len(events),
            ATTR_LATEST_EVENT: latest_event,
            ATTR_LAST_UPDATE_SOURCE: "dhl_parcel_nl",
            "tracking_url": f"https://www.dhlparcel.nl/nl/volg-uw-zending-0?tt={self._tracking_code}",
            "last_updated": datetime.now().isoformat(),
        }


class DHLParcelCountSensor(CoordinatorEntity[DHLParcelNLCoordinator], SensorEntity):
    """Summary sensor with number of tracked DHL parcels."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator: DHLParcelNLCoordinator, entry_id: str) -> None:
        """Initialize count sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_dhl_parcel_count"
        self._attr_name = "DHL Parcel Count"

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Enable by default for easier Assist setup."""
        return True

    @property
    def native_value(self) -> int:
        """Return number of currently tracked DHL parcels."""
        return len(self.coordinator.tracking_codes)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra details for notifications and templates."""
        if not self.coordinator.data:
            return {
                "tracked_codes": list(self.coordinator.tracking_codes),
                "parcels": [],
            }

        items = []
        for code, payload in self.coordinator.data.items():
            sender_name = (
                payload.get("sender_name")
                or payload.get("shipper_name")
                or _to_sender_name(payload.get("sender"))
            )
            delivery_from, delivery_to = _split_timeframe(
                payload.get("delivery_timeframe")
            )
            items.append(
                {
                    "tracking_code": code,
                    "entity_id": f"sensor.{slugify(f'dhl_parcel_{code}')}",
                    "status": payload.get("status_category"),
                    "status_en": STATUS_CATEGORIES.get(
                        payload.get("status_category", "UNKNOWN"),
                        payload.get("status_category", "UNKNOWN"),
                    ),
                    "status_pl": STATUS_CATEGORIES_PL.get(
                        payload.get("status_category", "UNKNOWN"),
                        payload.get("status_category", "UNKNOWN"),
                    ),
                    "sender": sender_name,
                    "delivery_date": payload.get("delivery_date"),
                    "delivery_time_from": delivery_from,
                    "delivery_time_to": delivery_to,
                    "delivery_day_label": _delivery_day_label(delivery_from),
                    "delivery_day_label_pl": _delivery_day_label_pl(delivery_from),
                    "delivered_at": payload.get("delivered_at"),
                    "delivery_location": payload.get("delivery_location"),
                }
            )

        return {
            "tracked_codes": list(self.coordinator.data.keys()),
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
            if payload.get("is_delivered") or payload.get("delivered_at"):
                continue
            sender = (
                payload.get("sender_name")
                or payload.get("shipper_name")
                or _to_sender_name(payload.get("sender"))
            )
            delivery_from, delivery_to = _split_timeframe(
                payload.get("delivery_timeframe")
            )
            parcels.append(
                {
                    "tracking_code": code,
                    "entity_id": f"sensor.{slugify(f'dhl_parcel_{code}')}",
                    "sender": sender,
                    "status": payload.get("status_category"),
                    "status_en": STATUS_CATEGORIES.get(
                        payload.get("status_category", "UNKNOWN"),
                        payload.get("status_category", "UNKNOWN"),
                    ),
                    "status_pl": STATUS_CATEGORIES_PL.get(
                        payload.get("status_category", "UNKNOWN"),
                        payload.get("status_category", "UNKNOWN"),
                    ),
                    "status_nl": STATUS_CATEGORIES_NL.get(
                        payload.get("status_category", "UNKNOWN"),
                        payload.get("status_category", "UNKNOWN"),
                    ),
                    "status_localized": _status_for_lang(
                        payload.get("status_category"), lang
                    ),
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
                    "delivered_at": payload.get("delivered_at"),
                    "delivery_location": payload.get("delivery_location"),
                    "delivered_at_hhmm": _format_hhmm(payload.get("delivered_at")),
                }
            )

        def _sort_key(item: dict[str, Any]) -> tuple[int, str]:
            start = item.get("delivery_time_from")
            if start:
                return (0, str(start))
            delivered = item.get("delivered_at")
            if delivered:
                return (2, str(delivered))
            return (1, item.get("tracking_code", ""))

        parcels.sort(key=_sort_key)
        return parcels

    def _build_voice_summaries(self, parcels: list[dict[str, Any]]) -> dict[str, str]:
        """Build multilingual voice summaries with parcel details."""
        if not parcels:
            return {
                "en": "No active DHL parcels.",
                "pl": "Brak aktywnych paczek DHL.",
                "nl": "Geen actieve DHL-pakketten.",
            }

        parts_en = [f"You have {len(parcels)} DHL parcels."]
        parts_pl = [f"Masz {len(parcels)} paczki DHL."]
        parts_nl = [f"Je hebt {len(parcels)} DHL-pakketten."]

        for index, p in enumerate(parcels, start=1):
            sender = p.get("sender") or "unknown sender"
            status_en = p.get("status_en") or p.get("status") or "unknown"
            status_pl = p.get("status_pl") or p.get("status") or "nieznany"
            status_nl = p.get("status_nl") or p.get("status") or "onbekend"
            from_hhmm = p.get("delivery_time_from_hhmm")
            to_hhmm = p.get("delivery_time_to_hhmm")
            day_en = p.get("delivery_day_label") or ""
            day_pl = p.get("delivery_day_label_pl") or day_en
            day_nl = p.get("delivery_day_label_nl") or day_en
            delivered_hhmm = p.get("delivered_at_hhmm")

            if delivered_hhmm:
                parts_en.append(
                    f"Package {index} from {sender} is {status_en} and was delivered at {delivered_hhmm}."
                )
                parts_pl.append(
                    f"Paczka {index} od {sender} ma status {status_pl} i została doręczona o {delivered_hhmm}."
                )
                parts_nl.append(
                    f"Pakket {index} van {sender} heeft status {status_nl} en is bezorgd om {delivered_hhmm}."
                )
                continue

            if from_hhmm and to_hhmm:
                parts_en.append(
                    f"Package {index} from {sender} is {status_en} and is expected {day_en} between {from_hhmm} and {to_hhmm}."
                )
                parts_pl.append(
                    f"Paczka {index} od {sender} ma status {status_pl} i jest przewidywana {day_pl} między {from_hhmm} a {to_hhmm}."
                )
                parts_nl.append(
                    f"Pakket {index} van {sender} heeft status {status_nl} en wordt verwacht {day_nl} tussen {from_hhmm} en {to_hhmm}."
                )
            elif from_hhmm:
                parts_en.append(
                    f"Package {index} from {sender} is {status_en} and is expected {day_en} at about {from_hhmm}."
                )
                parts_pl.append(
                    f"Paczka {index} od {sender} ma status {status_pl} i jest przewidywana {day_pl} około {from_hhmm}."
                )
                parts_nl.append(
                    f"Pakket {index} van {sender} heeft status {status_nl} en wordt verwacht {day_nl} rond {from_hhmm}."
                )
            else:
                parts_en.append(f"Package {index} from {sender} is {status_en}.")
                parts_pl.append(f"Paczka {index} od {sender} ma status {status_pl}.")
                parts_nl.append(
                    f"Pakket {index} van {sender} heeft status {status_nl}."
                )

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
                    "sender": sender,
                    "status": payload.get("status_category"),
                    "status_localized": _status_for_lang(
                        payload.get("status_category"), lang
                    ),
                    "delivery_time_from": delivery_from,
                    "delivery_time_to": delivery_to,
                    "delivery_time_from_hhmm": _format_hhmm(delivery_from),
                    "delivery_time_to_hhmm": _format_hhmm(delivery_to),
                    "delivery_day_label": _day_label_for_lang(delivery_from, lang),
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
                    "sender": sender,
                    "status": payload.get("status_category"),
                    "status_localized": _status_for_lang(
                        payload.get("status_category"), lang
                    ),
                    "delivered_at": delivered_at,
                    "delivered_at_hhmm": _format_hhmm(delivered_at),
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
        compact = ", ".join(f"{r['tracking_code']}:{r['sender']}" for r in rows)
        return compact[:250] if len(compact) > 250 else compact

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return delivered matrix/list details for AI voice assistants."""
        lang = getattr(self.coordinator, "summary_language", "en")
        rows = self._build_rows(lang)
        by_tracking = {row["tracking_code"]: row for row in rows}
        csv_simple = ", ".join(
            f"{row['tracking_code']}|{row['sender']}|{row.get('delivered_at_hhmm') or ''}"
            for row in rows
        )

        return {
            "summary_language": lang,
            "delivered_count": len(rows),
            "dhl_delivered_numbers": csv_simple,
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
                    "sender": sender,
                    "status_localized": _status_for_lang(
                        payload.get("status_category"), lang
                    ),
                    "delivered_at": payload.get("delivered_at"),
                    "delivered_at_hhmm": _format_hhmm(payload.get("delivered_at")),
                    "delivery_location": payload.get("delivery_location"),
                }
            )
        rows.sort(key=lambda x: str(x.get("delivered_at") or ""), reverse=True)
        return rows

    def _summary(self, rows: list[dict[str, Any]], lang: str) -> str:
        if not rows:
            if lang == "pl":
                return "Brak doręczonych paczek DHL."
            if lang == "nl":
                return "Geen bezorgde DHL-pakketten."
            return "No delivered DHL parcels."

        if lang == "pl":
            parts = [f"Masz {len(rows)} doręczone paczki DHL."]
            for i, r in enumerate(rows, start=1):
                parts.append(
                    f"Paczka {i} od {r['sender']} doręczona o {r.get('delivered_at_hhmm') or 'nieznanej godzinie'}, lokalizacja {r.get('delivery_location') or 'brak danych'}."
                )
            return " ".join(parts)

        if lang == "nl":
            parts = [f"Je hebt {len(rows)} bezorgde DHL-pakketten."]
            for i, r in enumerate(rows, start=1):
                parts.append(
                    f"Pakket {i} van {r['sender']} bezorgd om {r.get('delivered_at_hhmm') or 'onbekende tijd'}, locatie {r.get('delivery_location') or 'geen gegevens'}."
                )
            return " ".join(parts)

        parts = [f"You have {len(rows)} delivered DHL parcels."]
        for i, r in enumerate(rows, start=1):
            parts.append(
                f"Package {i} from {r['sender']} delivered at {r.get('delivered_at_hhmm') or 'unknown time'}, location {r.get('delivery_location') or 'no data'}."
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
