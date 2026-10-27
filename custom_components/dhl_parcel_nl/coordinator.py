"""Data update coordinator for Parcel Tracking NL (DHL, PostNL, Dragonfly)."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import DHLParcelNLAPI
from .carriers import (
    CARRIER_DISPLAY_NAMES,
    detect_carrier,
    is_ambiguous,
    normalize_code,
)
from .carriers.base import (
    CARRIER_DHL,
    CARRIER_DRAGONFLY,
    CARRIER_POSTNL,
    CARRIERS,
    SOURCE_AMAZON,
    SOURCE_DHL_ACCOUNT,
    SOURCE_MANUAL,
    SOURCE_POSTNL_ACCOUNT,
    CarrierBackoff,
    MissingPostalCode,
    TrackingNotFound,
    unknown_parcel,
)
from .carriers.dhl import DHLCarrier
from .carriers.dragonfly import DragonflyCarrier
from .carriers.postnl import PostNLAuth, PostNLAuthError, PostNLCarrier
from .const import (
    DEFAULT_REFRESH_END_TIME,
    DEFAULT_REFRESH_START_TIME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_SUMMARY_LANGUAGE,
    DOMAIN,
    EVENT_DHL_PARCEL_DELIVERY_WINDOW_CHANGED,
    EVENT_DHL_PARCEL_DISCOVERED,
    EVENT_DHL_PARCEL_REMOVED,
    EVENT_DHL_PARCEL_STATUS_CHANGED,
    EVENT_DHL_PARCEL_SUBSTATUS_CHANGED,
    EVENT_DHL_PARCEL_UPDATED,
    ISSUE_AMAZON_AUTH,
    ISSUE_AMAZON_UNAVAILABLE,
    ISSUE_POSTNL_POSTAL_CODE,
    status_for_lang,
    substatus_for_lang,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 2
SAVE_DELAY_SECONDS = 10
# Keep event payloads small: they are stored by the recorder (events table)
# and as attributes of the event entities (16 KiB attribute limit).
EVENT_PAYLOAD_MAX_EVENTS = 5
PENDING_ORDER_MAX_AGE = timedelta(days=14)
# Event "source" for parcels found by an account sync (unchanged from v1).
DISCOVERY_SOURCE_ACCOUNT = "account_sync"
# Carriers tried, in order, for ambiguous codes (3S + 4 letters) without an
# explicit carrier.
AMBIGUOUS_CARRIERS: tuple[str, ...] = (CARRIER_DHL, CARRIER_POSTNL)


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


def _without_raw(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of tracking data without the raw API response."""
    return {key: value for key, value in payload.items() if key != "raw"}


def _event_safe_data(tracking_data: dict[str, Any]) -> dict[str, Any]:
    """Build a compact tracking payload for HA bus events."""
    payload = _without_raw(tracking_data)
    events = payload.get("events") or []
    payload["events"] = events[-EVENT_PAYLOAD_MAX_EVENTS:]
    return payload


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an ISO timestamp into an aware UTC datetime.

    Naive timestamps are interpreted in the Home Assistant time zone.
    """
    if isinstance(value, datetime):
        return dt_util.as_utc(value)
    if not isinstance(value, str) or not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    return dt_util.as_utc(parsed)


class ParcelStateStore(Store[dict[str, Any]]):
    """Parcel state store with migration from version 1."""

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Migrate v1 (DHL only) to v2 (multi carrier)."""
        data = dict(old_data or {})
        if old_major_version < 2:
            data["tracked_parcels"] = {
                code: {"carrier": CARRIER_DHL, "source": SOURCE_DHL_ACCOUNT}
                for code in data.pop("account_tracking_codes", None) or []
            }
            # A v1 store means the DHL account was already synced once, so
            # its parcels must not be reported as "discovered" again.
            data["synced_sources"] = [SOURCE_DHL_ACCOUNT]
            data.setdefault("pending_orders", {})
        return data


class DHLParcelNLCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch parcel data from all enabled carriers and sources."""

    def __init__(
        self,
        hass: HomeAssistant,
        account_type: str | None = None,
        user_id: str | None = None,
        api_key: str | None = None,
        email: str | None = None,
        password: str | None = None,
        postal_code: str | None = None,
        delivered_keep_days: int = 7,
        refresh_interval_minutes: int = DEFAULT_SCAN_INTERVAL_MINUTES,
        refresh_start_time: str = DEFAULT_REFRESH_START_TIME,
        refresh_end_time: str = DEFAULT_REFRESH_END_TIME,
        summary_language: str = DEFAULT_SUMMARY_LANGUAGE,
        tracking_codes: list[str] | None = None,
        config_entry: ConfigEntry | None = None,
        manual_carriers: dict[str, str] | None = None,
        enabled_carriers: set[str] | None = None,
        postnl_auth: PostNLAuth | None = None,
        amazon_source: Any = None,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=max(1, int(refresh_interval_minutes))),
        )
        entry_id = config_entry.entry_id if config_entry else None
        self.entry_id = entry_id
        self.summary_language = (
            summary_language
            if summary_language in ("en", "pl", "nl")
            else DEFAULT_SUMMARY_LANGUAGE
        )
        session = async_get_clientsession(hass)

        self.api = DHLParcelNLAPI(
            session=session,
            account_type=account_type,
            user_id=user_id,
            api_key=api_key,
            email=email,
            password=password,
        )
        self.enabled_carriers: set[str] = (
            set(CARRIERS) if enabled_carriers is None else set(enabled_carriers)
        )
        self.carriers: dict[str, Any] = {
            CARRIER_DHL: DHLCarrier(self.api, self.summary_language),
            CARRIER_POSTNL: PostNLCarrier(session, self.summary_language, postnl_auth),
            CARRIER_DRAGONFLY: DragonflyCarrier(session, self.summary_language),
        }
        self.amazon_source = amazon_source

        self.postal_code = postal_code
        self.delivered_keep_days = delivered_keep_days
        self.refresh_interval_minutes = max(1, int(refresh_interval_minutes))
        self.refresh_start_time = self._parse_time(
            refresh_start_time, DEFAULT_REFRESH_START_TIME
        )
        self.refresh_end_time = self._parse_time(
            refresh_end_time, DEFAULT_REFRESH_END_TIME
        )

        manual_carriers = manual_carriers or {}
        self.tracking_codes: list[str] = []
        self.parcel_meta: dict[str, dict[str, Any]] = {}
        for code in dict.fromkeys(tracking_codes or []):
            carrier = manual_carriers.get(code) or detect_carrier(code)
            auto = code not in manual_carriers and is_ambiguous(code)
            if carrier not in self.enabled_carriers:
                if not auto:
                    continue
                carrier = next(
                    (c for c in AMBIGUOUS_CARRIERS if c in self.enabled_carriers), None
                )
                if carrier is None:
                    continue
            self.tracking_codes.append(code)
            self.parcel_meta[code] = {"carrier": carrier, "source": SOURCE_MANUAL}
            if auto:
                self.parcel_meta[code]["auto"] = True
        # Codes from the options field; they live in the options, not in
        # the store.
        self.manual_tracking_codes = set(self.tracking_codes)

        self.previous_states: dict[str, str] = {}
        self.previous_substatuses: dict[str, str | None] = {}
        self.previous_delivery_timeframes: dict[str, Any] = {}
        self.delivered_since: dict[str, datetime] = {}
        self.expired_tracking_codes: set[str] = set()
        # ISO timestamp of the last real change of each parcel's data.
        self.parcel_last_changed: dict[str, str] = {}
        # Amazon orders without a tracking number yet (no entity).
        self.pending_orders: dict[str, dict[str, Any]] = {}
        # Sources that completed their first sync; the first sync of a source
        # only records existing parcels and fires no "discovered" events.
        self.synced_sources: set[str] = set()
        # Result of the last Amazon poll, shown by the diagnostic sensor.
        self.amazon_status: dict[str, Any] = {"state": "waiting"}
        # Parcels of disabled carriers/sources kept for when they return.
        self._dormant_parcels: dict[str, dict[str, Any]] = {}
        # Carrier found for ambiguous manual codes (3S...), persisted so the
        # carriers are not probed again after a restart.
        self.resolved_carriers: dict[str, str] = {}
        # Codes added since the last update, with their discovery source.
        self._new_parcels: dict[str, str] = {}
        # Parcels skipped this cycle after HTTP 429/5xx.
        self._backoff_codes: set[str] = set()
        self._postnl_reauth_started = False

        self._store: ParcelStateStore | None = (
            ParcelStateStore(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}_state")
            if entry_id
            else None
        )
        # Events produced by the current update; fired after coordinator data
        # (and therefore all sensors) have been updated.
        self._pending_events: list[tuple[str, dict[str, Any]]] = []
        # Events produced before platforms/automations are ready.
        self._events_enabled = False
        self._deferred_events: list[tuple[str, dict[str, Any]]] = []

    # ------------------------------------------------------------------
    # Parcel metadata
    # ------------------------------------------------------------------

    def carrier_of(self, code: str) -> str:
        """Return the carrier of a tracked parcel."""
        return (self.parcel_meta.get(code) or {}).get("carrier") or CARRIER_DHL

    def source_of(self, code: str) -> str:
        """Return how a tracked parcel was added."""
        return (self.parcel_meta.get(code) or {}).get("source") or SOURCE_MANUAL

    def _source_active(self, source: str | None) -> bool:
        if source == SOURCE_DHL_ACCOUNT:
            return self.dhl_account_enabled
        if source == SOURCE_POSTNL_ACCOUNT:
            return self.postnl_account_enabled
        if source == SOURCE_AMAZON:
            return self.amazon_source is not None
        return True

    @property
    def dhl_account_enabled(self) -> bool:
        """Return True when the DHL account sync is configured."""
        return (
            CARRIER_DHL in self.enabled_carriers
            and self.carriers[CARRIER_DHL].has_account
        )

    @property
    def postnl_account_enabled(self) -> bool:
        """Return True when the PostNL account sync is configured."""
        return (
            CARRIER_POSTNL in self.enabled_carriers
            and self.carriers[CARRIER_POSTNL].has_account
        )

    def _has_account_credentials(self) -> bool:
        """Kept for compatibility: DHL account credentials configured."""
        return self.carriers[CARRIER_DHL].has_account

    def _add_parcel(
        self,
        code: str,
        carrier: str,
        source: str,
        discovery_source: str | None = None,
        **extra: Any,
    ) -> bool:
        """Start tracking a parcel. Returns True when it was new."""
        if code in self.tracking_codes:
            meta = self.parcel_meta.setdefault(code, {})
            for key, value in extra.items():
                if value and not meta.get(key):
                    meta[key] = value
            return False
        self.tracking_codes.append(code)
        meta = {"carrier": carrier, "source": source}
        meta.update({k: v for k, v in extra.items() if v})
        self.parcel_meta[code] = meta
        if discovery_source:
            self._new_parcels[code] = discovery_source
        return True

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def async_load_state(self) -> None:
        """Restore change-detection state persisted across restarts."""
        if self._store is None:
            return
        try:
            stored = await self._store.async_load()
        except Exception as err:  # corrupted storage must not block setup
            _LOGGER.warning("Could not load stored parcel state: %s", err)
            stored = None

        if not isinstance(stored, dict):
            return

        self.previous_states = dict(stored.get("previous_states") or {})
        self.previous_substatuses = dict(stored.get("previous_substatuses") or {})
        self.previous_delivery_timeframes = dict(
            stored.get("previous_delivery_timeframes") or {}
        )
        self.expired_tracking_codes = set(stored.get("expired_tracking_codes") or [])
        self.parcel_last_changed = dict(stored.get("parcel_last_changed") or {})
        self.synced_sources = set(stored.get("synced_sources") or [])
        self.pending_orders = dict(stored.get("pending_orders") or {})
        self.resolved_carriers = dict(stored.get("resolved_carriers") or {})
        for code, carrier in self.resolved_carriers.items():
            meta = self.parcel_meta.get(code)
            if meta and meta.get("auto") and carrier in self.enabled_carriers:
                meta["carrier"] = carrier
                meta.pop("auto", None)

        self.delivered_since = {}
        for code, raw in (stored.get("delivered_since") or {}).items():
            parsed = _parse_datetime(raw)
            if parsed is not None:
                self.delivered_since[code] = parsed

        # Restore parcels from accounts, Amazon and the track_parcel service
        # so they are not reported as "discovered" again after a restart.
        for code, meta in (stored.get("tracked_parcels") or {}).items():
            if not isinstance(meta, dict) or code in self.expired_tracking_codes:
                continue
            if code in self.tracking_codes:
                continue
            carrier = meta.get("carrier") or CARRIER_DHL
            if carrier not in self.enabled_carriers or not self._source_active(
                meta.get("source")
            ):
                self._dormant_parcels[code] = dict(meta)
                continue
            self.tracking_codes.append(code)
            self.parcel_meta[code] = dict(meta)

    @callback
    def _state_to_store(self) -> dict[str, Any]:
        """Serialize change-detection state."""
        tracked = dict(self._dormant_parcels)
        tracked.update(
            {
                code: dict(self.parcel_meta.get(code) or {"carrier": CARRIER_DHL})
                for code in self.tracking_codes
                if code not in self.manual_tracking_codes
            }
        )
        return {
            "tracked_parcels": tracked,
            "previous_states": dict(self.previous_states),
            "previous_substatuses": dict(self.previous_substatuses),
            "previous_delivery_timeframes": dict(self.previous_delivery_timeframes),
            "delivered_since": {
                code: value.isoformat() for code, value in self.delivered_since.items()
            },
            "expired_tracking_codes": sorted(self.expired_tracking_codes),
            "parcel_last_changed": dict(self.parcel_last_changed),
            "synced_sources": sorted(self.synced_sources),
            "pending_orders": dict(self.pending_orders),
            "resolved_carriers": {
                code: carrier
                for code, carrier in self.resolved_carriers.items()
                if code in self.tracking_codes
            },
        }

    @callback
    def _schedule_save(self) -> None:
        if self._store is not None:
            self._store.async_delay_save(self._state_to_store, SAVE_DELAY_SECONDS)

    async def async_save_state(self) -> None:
        """Persist state immediately (used on unload)."""
        if self._store is not None:
            await self._store.async_save(self._state_to_store())

    # ------------------------------------------------------------------
    # Event dispatching
    # ------------------------------------------------------------------

    def _queue_event(self, event_type: str, event_data: dict[str, Any]) -> None:
        self._pending_events.append((event_type, event_data))

    @callback
    def async_enable_events(self) -> None:
        """Start firing events (call after platforms and HA are started)."""
        self._events_enabled = True
        deferred, self._deferred_events = self._deferred_events, []
        for event_type, event_data in deferred:
            self.hass.bus.async_fire(event_type, event_data)

    async def _async_refresh(self, *args: Any, **kwargs: Any) -> None:
        """Refresh data, then fire events once sensors hold the new data."""
        self._pending_events = []
        try:
            await super()._async_refresh(*args, **kwargs)
        finally:
            events, self._pending_events = self._pending_events, []
            if events and self.last_update_success:
                if self._events_enabled:
                    for event_type, event_data in events:
                        self.hass.bus.async_fire(event_type, event_data)
                else:
                    self._deferred_events.extend(events)
            self._schedule_save()

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from all carriers."""
        # Always fetch on the first refresh, otherwise a restart outside the
        # refresh window would leave all sensors empty until the next window.
        if (
            self.data is not None
            and not self._new_parcels
            and not self._is_in_refresh_window()
        ):
            _LOGGER.debug("Outside refresh window; skipping parcel poll")
            return dict(self.data)

        data: dict[str, Any] = {}
        previous_data = dict(self.data) if isinstance(self.data, dict) else {}
        known_before = set(previous_data) | set(self.parcel_last_changed)

        if self.dhl_account_enabled:
            await self._sync_account(CARRIER_DHL, SOURCE_DHL_ACCOUNT, previous_data)
        if self.postnl_account_enabled:
            await self._sync_account(
                CARRIER_POSTNL, SOURCE_POSTNL_ACCOUNT, previous_data
            )

        # Ensure delivered parcels from retention remain visible even if removed
        # from account listing, until retention window expires.
        for tracking_code in list(self.delivered_since):
            if tracking_code not in self.tracking_codes and (
                tracking_code in self.parcel_meta
            ):
                self.tracking_codes.append(tracking_code)

        new_parcels, self._new_parcels = self._new_parcels, {}
        backoff, self._backoff_codes = self._backoff_codes, set()
        postal_code_missing = False

        for tracking_code in list(self.tracking_codes):
            carrier_name = self.carrier_of(tracking_code)
            if carrier_name not in self.enabled_carriers:
                continue
            carrier = self.carriers[carrier_name]
            meta = self.parcel_meta.get(tracking_code) or {}

            if tracking_code in backoff and tracking_code in previous_data:
                data[tracking_code] = previous_data[tracking_code]
                continue

            postal_code = meta.get("postal_code") or self.postal_code
            try:
                if meta.get("auto"):
                    carrier_name, tracking_data = await self._async_track_ambiguous(
                        tracking_code, carrier_name, postal_code
                    )
                    meta = self.parcel_meta.get(tracking_code) or {}
                else:
                    tracking_data = await carrier.async_track(
                        tracking_code, postal_code
                    )
            except TrackingNotFound:
                _LOGGER.debug(
                    "%s does not know parcel %s yet",
                    CARRIER_DISPLAY_NAMES.get(carrier_name, carrier_name),
                    tracking_code,
                )
                tracking_data = previous_data.get(tracking_code) or unknown_parcel(
                    tracking_code
                )
            except MissingPostalCode:
                postal_code_missing = True
                continue
            except CarrierBackoff as err:
                _LOGGER.warning(
                    "Skipping parcel %s for one cycle: %s", tracking_code, err
                )
                self._backoff_codes.add(tracking_code)
                if tracking_code in previous_data:
                    data[tracking_code] = previous_data[tracking_code]
                continue
            except Exception as err:
                _LOGGER.error("Error updating tracking %s: %s", tracking_code, err)
                if tracking_code in previous_data:
                    data[tracking_code] = previous_data[tracking_code]
                continue

            if not tracking_data:
                if tracking_code in previous_data:
                    data[tracking_code] = previous_data[tracking_code]
                continue

            tracking_data = dict(tracking_data)
            tracking_data["carrier"] = carrier_name
            tracking_data["source"] = meta.get("source") or SOURCE_MANUAL
            tracking_data["order_id"] = meta.get("order_id")
            if meta.get("store"):
                tracking_data["store"] = meta["store"]
            if not tracking_data.get("sender_name") and meta.get("sender"):
                tracking_data["sender_name"] = meta["sender"]

            self._process_parcel(
                tracking_code,
                tracking_data,
                previous_data.get(tracking_code),
                new_parcels.get(tracking_code),
            )
            data[tracking_code] = tracking_data

        self._update_postal_code_issue(postal_code_missing)
        self._remove_expired_delivered(data, known_before)

        return data

    async def _async_track_ambiguous(
        self, code: str, current: str, postal_code: str | None
    ) -> tuple[str, dict[str, Any]]:
        """Track a 3S code whose carrier is unknown: try DHL, then PostNL.

        The carrier that knows the code is remembered (the parcel sensor is
        re-created with that carrier's entity id). Raises TrackingNotFound
        when no carrier knows it yet; the next update tries again.
        """
        order = [current] + [c for c in AMBIGUOUS_CARRIERS if c != current]
        missing_postal = False
        for name in order:
            if name not in self.enabled_carriers:
                continue
            try:
                data = await self.carriers[name].async_track(code, postal_code)
            except TrackingNotFound:
                continue
            except MissingPostalCode:
                missing_postal = True
                continue
            meta = self.parcel_meta.setdefault(code, {"source": SOURCE_MANUAL})
            meta.pop("auto", None)
            if meta.get("carrier") != name:
                _LOGGER.info(
                    "Parcel %s belongs to %s", code, CARRIER_DISPLAY_NAMES[name]
                )
                self._forget(code)
            meta["carrier"] = name
            self.resolved_carriers[code] = name
            return name, data
        if missing_postal and current != CARRIER_POSTNL:
            _LOGGER.debug("Parcel %s unknown to DHL; PostNL needs a postal code", code)
        raise TrackingNotFound(code)

    def _process_parcel(
        self,
        tracking_code: str,
        tracking_data: dict[str, Any],
        previous_payload: dict[str, Any] | None,
        discovery_source: str | None,
    ) -> None:
        """Detect changes of one parcel and queue events."""
        previous_status = self.previous_states.get(tracking_code)
        self._check_for_status_change(tracking_code, tracking_data)
        self._check_for_substatus_change(
            tracking_code,
            tracking_data,
            status_changed=previous_status is not None
            and previous_status != tracking_data.get("status_category", "UNKNOWN"),
        )
        self._check_for_delivery_window_change(tracking_code, tracking_data)

        if tracking_data.get("is_delivered"):
            self.delivered_since.setdefault(
                tracking_code,
                _parse_datetime(tracking_data.get("delivered_at")) or dt_util.utcnow(),
            )
        else:
            self.delivered_since.pop(tracking_code, None)

        if (
            previous_payload is None and tracking_code not in self.parcel_last_changed
        ) or (
            previous_payload is not None
            and _without_raw(previous_payload) != _without_raw(tracking_data)
        ):
            self.parcel_last_changed[tracking_code] = dt_util.utcnow().isoformat()

        common = self._common_event_fields(tracking_data)
        self._queue_event(
            EVENT_DHL_PARCEL_UPDATED,
            {
                "tracking_code": tracking_code,
                "carrier": common["carrier"],
                "status": tracking_data.get("status_category"),
                "status_localized": common["status_localized"],
                "delivery_date": tracking_data.get("delivery_date"),
                "delivery_timeframe": tracking_data.get("delivery_timeframe"),
                "sender": common["sender"],
                "recipient": tracking_data.get("recipient"),
                "delivered_at": tracking_data.get("delivered_at"),
                "delivery_location": tracking_data.get("delivery_location"),
                "language": self.summary_language,
                "data": common["data"],
            },
        )
        if discovery_source and not tracking_data.get("is_delivered"):
            self._queue_event(
                EVENT_DHL_PARCEL_DISCOVERED,
                {
                    "tracking_code": tracking_code,
                    "carrier": common["carrier"],
                    "source": discovery_source,
                    "order_id": tracking_data.get("order_id"),
                    "store": tracking_data.get("store"),
                    "sender": common["sender"],
                    "status": tracking_data.get("status_category"),
                    "status_localized": common["status_localized"],
                    "delivery_timeframe": tracking_data.get("delivery_timeframe"),
                    "delivered_at": tracking_data.get("delivered_at"),
                    "delivery_location": tracking_data.get("delivery_location"),
                    "language": self.summary_language,
                    "data": common["data"],
                },
            )

    async def _sync_account(
        self, carrier_name: str, source: str, previous_data: dict[str, Any]
    ) -> None:
        """Add new parcels from a carrier account, drop removed ones."""
        carrier = self.carriers[carrier_name]
        suppress = source not in self.synced_sources
        try:
            account_parcels = await carrier.async_list_account()
        except PostNLAuthError as err:
            _LOGGER.warning("PostNL login expired; reauthentication required: %s", err)
            carrier.disable_account()
            if not self._postnl_reauth_started and self.config_entry is not None:
                self._postnl_reauth_started = True
                self.config_entry.async_start_reauth(
                    self.hass, data={"reauth_target": "postnl"}
                )
            return
        except Exception as err:
            _LOGGER.warning(
                "Could not sync parcels from %s account: %s",
                CARRIER_DISPLAY_NAMES[carrier_name],
                err,
            )
            return

        account_set = set(account_parcels)
        hints = getattr(carrier, "account_hints", {}) or {}
        for tracking_code in account_parcels:
            if tracking_code in self.expired_tracking_codes:
                continue
            hint = hints.get(tracking_code) or {}
            if self._add_parcel(
                tracking_code,
                carrier_name,
                source,
                None if suppress else DISCOVERY_SOURCE_ACCOUNT,
                postal_code=hint.get("postal_code"),
                sender=hint.get("sender"),
            ):
                _LOGGER.info(
                    "New parcel detected from %s account: %s",
                    CARRIER_DISPLAY_NAMES[carrier_name],
                    tracking_code,
                )

        self.synced_sources.add(source)

        # An empty list may mean an error; do not treat that as "every parcel
        # was removed from the account".
        if account_set:
            self._remove_parcels_missing_from_account(
                account_set, previous_data, source
            )

    def _remove_parcels_missing_from_account(
        self,
        account_set: set[str],
        previous_data: dict[str, Any],
        source: str = SOURCE_DHL_ACCOUNT,
    ) -> None:
        """Stop tracking non-delivered parcels that disappeared from the account."""
        removed_from_account: list[str] = []
        for tracking_code in list(self.tracking_codes):
            if tracking_code in account_set:
                continue
            if tracking_code in self.manual_tracking_codes:
                continue
            if self.source_of(tracking_code) != source:
                continue

            prev_payload = previous_data.get(tracking_code, {})
            if prev_payload.get("is_delivered") or tracking_code in (
                self.delivered_since
            ):
                self.delivered_since.setdefault(
                    tracking_code,
                    _parse_datetime(prev_payload.get("delivered_at"))
                    or dt_util.utcnow(),
                )
                continue

            removed_from_account.append(tracking_code)

        for tracking_code in removed_from_account:
            carrier = self.carrier_of(tracking_code)
            self._stop_tracking(tracking_code)
            self._queue_event(
                EVENT_DHL_PARCEL_REMOVED,
                {
                    "tracking_code": tracking_code,
                    "carrier": carrier,
                    "reason": "removed_from_account",
                    "sender": _to_sender_name(
                        (previous_data.get(tracking_code) or {}).get("sender_name")
                        or (previous_data.get(tracking_code) or {}).get("sender")
                    ),
                    "language": self.summary_language,
                },
            )

    def _stop_tracking(self, tracking_code: str) -> None:
        self.tracking_codes = [c for c in self.tracking_codes if c != tracking_code]
        self._forget(tracking_code)
        self.parcel_meta.pop(tracking_code, None)

    def _forget(self, tracking_code: str) -> None:
        """Drop all change-detection state of a parcel."""
        self.previous_states.pop(tracking_code, None)
        self.previous_substatuses.pop(tracking_code, None)
        self.previous_delivery_timeframes.pop(tracking_code, None)
        self.delivered_since.pop(tracking_code, None)
        self.parcel_last_changed.pop(tracking_code, None)

    def _common_event_fields(self, tracking_data: dict[str, Any]) -> dict[str, Any]:
        carrier = tracking_data.get("carrier") or CARRIER_DHL
        return {
            "carrier": carrier,
            "sender": tracking_data.get("sender_name")
            or tracking_data.get("shipper_name")
            or _to_sender_name(tracking_data.get("sender")),
            "status_localized": status_for_lang(
                tracking_data.get("status_category"), self.summary_language, carrier
            ),
            "data": _event_safe_data(tracking_data),
        }

    def _update_postal_code_issue(self, missing: bool) -> None:
        if self.entry_id is None:
            return
        issue_id = f"{ISSUE_POSTNL_POSTAL_CODE}_{self.entry_id}"
        if missing:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_POSTNL_POSTAL_CODE,
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _remove_expired_delivered(
        self, data: dict[str, Any], known_before: set[str] | None = None
    ) -> None:
        """Remove delivered parcels after configured retention period.

        ``known_before`` holds parcels that were already exposed to Home
        Assistant before this update. Old delivered parcels seen for the first
        time (e.g. account history on a fresh install) are dropped silently.
        """
        if self.delivered_keep_days < 0:
            return

        now = dt_util.utcnow()
        to_remove: list[str] = []

        for tracking_code, delivered_at in self.delivered_since.items():
            age_days = (now - dt_util.as_utc(delivered_at)).days
            if age_days >= self.delivered_keep_days:
                to_remove.append(tracking_code)

        for tracking_code in to_remove:
            _LOGGER.info(
                "Removing delivered parcel %s after %s days",
                tracking_code,
                self.delivered_keep_days,
            )
            payload = data.get(tracking_code) or {}
            carrier = self.carrier_of(tracking_code)
            self._stop_tracking(tracking_code)
            data.pop(tracking_code, None)
            self.expired_tracking_codes.add(tracking_code)
            if known_before is not None and tracking_code not in known_before:
                continue
            self._queue_event(
                EVENT_DHL_PARCEL_REMOVED,
                {
                    "tracking_code": tracking_code,
                    "carrier": carrier,
                    "reason": "delivered_retention_expired",
                    "retention_days": self.delivered_keep_days,
                    "sender": payload.get("sender_name")
                    or payload.get("shipper_name")
                    or _to_sender_name(payload.get("sender")),
                    "language": self.summary_language,
                },
            )

    def _check_for_status_change(
        self, tracking_code: str, tracking_data: dict[str, Any]
    ) -> None:
        """Check if status changed and queue event."""
        current_status = tracking_data.get("status_category", "UNKNOWN")
        previous_status = self.previous_states.get(tracking_code)

        if previous_status and previous_status != current_status:
            _LOGGER.debug(
                "Status changed for %s: %s -> %s",
                tracking_code,
                previous_status,
                current_status,
            )
            common = self._common_event_fields(tracking_data)
            carrier = common["carrier"]
            self._queue_event(
                EVENT_DHL_PARCEL_STATUS_CHANGED,
                {
                    "tracking_code": tracking_code,
                    "carrier": carrier,
                    "old_status": previous_status,
                    "new_status": current_status,
                    "old_status_localized": status_for_lang(
                        previous_status, self.summary_language, carrier
                    ),
                    "new_status_localized": status_for_lang(
                        current_status, self.summary_language, carrier
                    ),
                    "status": current_status,
                    "status_localized": common["status_localized"],
                    "substatus": tracking_data.get("last_event_status"),
                    "substatus_localized": substatus_for_lang(
                        tracking_data.get("last_event_status"),
                        self.summary_language,
                        tracking_data.get("last_event_description"),
                    ),
                    "sender": common["sender"],
                    "delivered_at": tracking_data.get("delivered_at"),
                    "delivery_location": tracking_data.get("delivery_location"),
                    "delivery_timeframe": tracking_data.get("delivery_timeframe"),
                    "language": self.summary_language,
                    "data": common["data"],
                },
            )

        self.previous_states[tracking_code] = current_status

    def _check_for_substatus_change(
        self,
        tracking_code: str,
        tracking_data: dict[str, Any],
        status_changed: bool = False,
    ) -> None:
        """Check if substatus changed and queue event.

        ``status_changed`` tells automations that a dhl_parcel_status_changed
        event was fired for the same update, so they can avoid double
        announcements.
        """
        current_substatus = tracking_data.get("last_event_status")
        previous_substatus = self.previous_substatuses.get(tracking_code)

        if (
            current_substatus
            and previous_substatus
            and previous_substatus != current_substatus
        ):
            common = self._common_event_fields(tracking_data)
            self._queue_event(
                EVENT_DHL_PARCEL_SUBSTATUS_CHANGED,
                {
                    "tracking_code": tracking_code,
                    "carrier": common["carrier"],
                    "old_substatus": previous_substatus,
                    "new_substatus": current_substatus,
                    "old_substatus_localized": substatus_for_lang(
                        previous_substatus, self.summary_language
                    ),
                    "new_substatus_localized": substatus_for_lang(
                        current_substatus, self.summary_language
                    ),
                    "status_changed": status_changed,
                    "status": tracking_data.get("status_category"),
                    "status_localized": common["status_localized"],
                    "sender": common["sender"],
                    "delivered_at": tracking_data.get("delivered_at"),
                    "delivery_location": tracking_data.get("delivery_location"),
                    "language": self.summary_language,
                    "data": common["data"],
                },
            )

        if current_substatus:
            self.previous_substatuses[tracking_code] = current_substatus

    def _check_for_delivery_window_change(
        self, tracking_code: str, tracking_data: dict[str, Any]
    ) -> None:
        """Check if delivery window changed and queue event.

        The first observation of a parcel only records the window; it is not
        a change. This prevents an event burst after restarts/reloads and
        when new parcels are discovered.
        """
        current_window = tracking_data.get("delivery_timeframe")
        known = tracking_code in self.previous_delivery_timeframes
        previous_window = self.previous_delivery_timeframes.get(tracking_code)

        if known and current_window and current_window != previous_window:
            common = self._common_event_fields(tracking_data)
            self._queue_event(
                EVENT_DHL_PARCEL_DELIVERY_WINDOW_CHANGED,
                {
                    "tracking_code": tracking_code,
                    "carrier": common["carrier"],
                    "old_delivery_timeframe": previous_window,
                    "new_delivery_timeframe": current_window,
                    "status": tracking_data.get("status_category"),
                    "status_localized": common["status_localized"],
                    "sender": common["sender"],
                    "delivered_at": tracking_data.get("delivered_at"),
                    "delivery_location": tracking_data.get("delivery_location"),
                    "language": self.summary_language,
                    "data": common["data"],
                },
            )

        self.previous_delivery_timeframes[tracking_code] = current_window

    # ------------------------------------------------------------------
    # Services
    # ------------------------------------------------------------------

    async def async_track_parcel(
        self,
        tracking_code: str,
        carrier: str | None = None,
        postal_code: str | None = None,
    ) -> str:
        """Start tracking a parcel (track_parcel service). Returns carrier."""
        code = normalize_code(tracking_code)
        auto = carrier is None and is_ambiguous(code)
        carrier_name = carrier or detect_carrier(code)
        if auto and carrier_name not in self.enabled_carriers:
            carrier_name = next(
                (c for c in AMBIGUOUS_CARRIERS if c in self.enabled_carriers),
                carrier_name,
            )
        if carrier_name not in CARRIERS:
            raise ValueError(f"Unsupported carrier: {carrier}")
        if carrier_name not in self.enabled_carriers:
            raise ValueError(f"Carrier {carrier_name} is disabled in the options")
        self.expired_tracking_codes.discard(code)
        self._dormant_parcels.pop(code, None)
        postal = (postal_code or "").replace(" ", "").upper() or None
        self._add_parcel(
            code, carrier_name, SOURCE_MANUAL, SOURCE_MANUAL, postal_code=postal
        )
        if auto and code in self.parcel_meta:
            self.parcel_meta[code]["auto"] = True
        await self.async_refresh()
        return self.carrier_of(code)

    async def async_untrack_parcel(self, tracking_code: str) -> bool:
        """Stop tracking a parcel (untrack_parcel service)."""
        code = tracking_code.strip()
        match = next(
            (c for c in self.tracking_codes if c.upper() == code.upper()), None
        )
        if match is None:
            return False
        carrier = self.carrier_of(match)
        payload = (self.data or {}).get(match) or {}
        self._stop_tracking(match)
        self.manual_tracking_codes.discard(match)
        # Do not let an account sync or Amazon add it again.
        self.expired_tracking_codes.add(match)
        if self.data and match in self.data:
            data = dict(self.data)
            data.pop(match, None)
            self.async_set_updated_data(data)
        self.hass.bus.async_fire(
            EVENT_DHL_PARCEL_REMOVED,
            {
                "tracking_code": match,
                "carrier": carrier,
                "reason": "untracked",
                "sender": payload.get("sender_name"),
                "language": self.summary_language,
            },
        )
        self._schedule_save()
        return True

    def add_tracking_code(self, tracking_code: str) -> None:
        """Add new tracking code to monitor."""
        if self._add_parcel(
            tracking_code, detect_carrier(tracking_code), SOURCE_MANUAL
        ):
            self.hass.async_create_task(self.async_request_refresh())

    def remove_tracking_code(self, tracking_code: str) -> None:
        """Remove tracking code from monitoring."""
        if tracking_code in self.tracking_codes:
            self._stop_tracking(tracking_code)
            if self.data and tracking_code in self.data:
                del self.data[tracking_code]

    # ------------------------------------------------------------------
    # Amazon.nl source
    # ------------------------------------------------------------------

    def _set_amazon_status(self, state: str, **details: Any) -> None:
        """Record the result of the last Amazon poll (diagnostic sensor)."""
        self.amazon_status = {
            "state": state,
            "last_poll": dt_util.utcnow().isoformat(),
            **details,
        }
        self.async_update_listeners()

    async def async_poll_amazon(self, force: bool = False) -> None:
        """Poll Amazon.nl orders and add new shipments.

        Failures never affect carrier tracking. A login problem (captcha,
        OTP prompt, expired token) stops polling until reauthentication.
        """
        source = self.amazon_source
        if source is None or source.blocked:
            return
        if not force and not self._is_in_refresh_window():
            _LOGGER.debug("Outside refresh window; skipping Amazon.nl poll")
            return

        from .sources.amazon import AmazonAuthRequired, AmazonUnavailable

        try:
            shipments = await source.async_fetch_shipments()
        except AmazonAuthRequired as err:
            _LOGGER.warning("Amazon.nl needs reauthentication: %s", err)
            source.blocked = True
            self._set_amazon_status("auth_required", error=str(err))
            self._create_issue(ISSUE_AMAZON_AUTH)
            if self.config_entry is not None:
                self.config_entry.async_start_reauth(
                    self.hass, data={"reauth_target": "amazon"}
                )
            return
        except AmazonUnavailable as err:
            _LOGGER.warning("Amazon.nl source unavailable: %s", err)
            source.blocked = True
            self._set_amazon_status("unavailable", error=str(err))
            self._create_issue(ISSUE_AMAZON_UNAVAILABLE)
            return
        except Exception as err:
            _LOGGER.warning("Amazon.nl poll failed, retrying later: %s", err)
            self._set_amazon_status("error", error=str(err))
            return

        self._delete_issue(ISSUE_AMAZON_AUTH)
        self._delete_issue(ISSUE_AMAZON_UNAVAILABLE)
        added, summary = self._apply_amazon_shipments(shipments)
        summary["orders"] = getattr(source, "last_order_count", None)
        summary["stores"] = dict(getattr(source, "last_store_results", {}) or {})
        _LOGGER.info(
            "Amazon: %s orders (last %s days; per store: %s), %s shipments, %s "
            "with a tracking number, %s added, %s waiting for a tracking number, "
            "ignored carriers: %s",
            summary["orders"],
            14,
            summary["stores"],
            summary["shipments"],
            summary["with_tracking"],
            len(summary["added"]),
            summary["pending"],
            ", ".join(summary["ignored_carriers"]) or "none",
        )
        self._set_amazon_status("ok", **summary)
        if added:
            await self.async_refresh()
        else:
            self._schedule_save()

    def _apply_amazon_shipments(
        self, shipments: list[Any]
    ) -> tuple[bool, dict[str, Any]]:
        """Add Amazon shipments; returns (parcels added, poll summary).

        Delivered shipments are added too (like delivered DHL account
        parcels); they never fire "discovered" and are removed after the
        delivered retention days.
        """
        suppress = SOURCE_AMAZON not in self.synced_sources
        now = dt_util.utcnow()
        added: list[str] = []
        ignored: set[str] = set()
        seen_orders: set[str] = set()
        with_tracking = 0

        for shipment in shipments:
            order_id = shipment.order_id
            if not shipment.tracking_number:
                if shipment.delivered or not order_id:
                    continue
                seen_orders.add(order_id)
                pending = self.pending_orders.setdefault(
                    order_id, {"first_seen": now.isoformat()}
                )
                pending["carrier"] = shipment.carrier
                continue

            with_tracking += 1
            self.pending_orders.pop(order_id, None)
            code = normalize_code(shipment.tracking_number)
            carrier = detect_carrier(code, shipment.carrier)
            if carrier is None:
                ignored.add(str(shipment.carrier or "unknown"))
                _LOGGER.debug(
                    "Ignoring Amazon order %s: carrier %s is not supported",
                    order_id,
                    shipment.carrier,
                )
                continue
            if carrier not in self.enabled_carriers:
                ignored.add(CARRIER_DISPLAY_NAMES[carrier] + " (disabled)")
                continue
            if code in self.expired_tracking_codes or code in self.tracking_codes:
                continue
            if self._add_parcel(
                code,
                carrier,
                SOURCE_AMAZON,
                None if suppress or shipment.delivered else SOURCE_AMAZON,
                order_id=order_id,
                postal_code=shipment.postal_code,
                sender="Amazon",
                store=getattr(shipment, "store", None),
            ):
                _LOGGER.info(
                    "New %s parcel from Amazon order %s: %s",
                    CARRIER_DISPLAY_NAMES[carrier],
                    order_id,
                    code,
                )
                added.append(code)

        for order_id, pending in list(self.pending_orders.items()):
            first_seen = _parse_datetime(pending.get("first_seen")) or now
            if order_id not in seen_orders and now - first_seen > PENDING_ORDER_MAX_AGE:
                self.pending_orders.pop(order_id, None)

        self.synced_sources.add(SOURCE_AMAZON)
        return bool(added), {
            "shipments": len(shipments),
            "with_tracking": with_tracking,
            "added": added,
            "pending": len(self.pending_orders),
            "pending_orders": sorted(self.pending_orders),
            "ignored_carriers": sorted(ignored),
        }

    def _create_issue(self, key: str) -> None:
        if self.entry_id is None:
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{key}_{self.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=key,
        )

    def _delete_issue(self, key: str) -> None:
        if self.entry_id is not None:
            ir.async_delete_issue(self.hass, DOMAIN, f"{key}_{self.entry_id}")

    # ------------------------------------------------------------------
    # Refresh window
    # ------------------------------------------------------------------

    def _parse_time(self, value: str, fallback: str) -> time:
        """Parse HH:MM into time object."""
        raw = value or fallback
        try:
            parts = raw.split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            return time(hour=hour, minute=minute)
        except Exception:
            parts = fallback.split(":")
            return time(hour=int(parts[0]), minute=int(parts[1]))

    def _is_in_refresh_window(self) -> bool:
        """Check if current time (HA time zone) is within the refresh window."""
        now = dt_util.now().time()
        start = self.refresh_start_time
        end = self.refresh_end_time
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end


type DHLParcelConfigEntry = ConfigEntry[DHLParcelNLCoordinator]
