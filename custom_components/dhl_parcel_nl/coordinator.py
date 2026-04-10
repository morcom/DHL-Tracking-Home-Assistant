"""Data update coordinator for DHL Parcel NL."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import logging
from typing import Any, Dict, List

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DHLParcelNLAPI
from .const import (
    DEFAULT_REFRESH_END_TIME,
    DEFAULT_REFRESH_START_TIME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_SUMMARY_LANGUAGE,
    DOMAIN,
    EVENT_DHL_PARCEL_DISCOVERED,
    EVENT_DHL_PARCEL_DELIVERY_WINDOW_CHANGED,
    EVENT_DHL_PARCEL_REMOVED,
    EVENT_DHL_PARCEL_STATUS_CHANGED,
    EVENT_DHL_PARCEL_SUBSTATUS_CHANGED,
    EVENT_DHL_PARCEL_UPDATED,
    STATUS_CATEGORIES,
    STATUS_CATEGORIES_NL,
    STATUS_CATEGORIES_PL,
)

_LOGGER = logging.getLogger(__name__)


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


def _status_for_lang(status: str | None, lang: str) -> str:
    """Map status category to configured language."""
    code = status or "UNKNOWN"
    if lang == "pl":
        return STATUS_CATEGORIES_PL.get(code, code)
    if lang == "nl":
        return STATUS_CATEGORIES_NL.get(code, code)
    return STATUS_CATEGORIES.get(code, code)


class DHLParcelNLCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Class to manage fetching DHL Parcel data."""

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
        tracking_codes: List[str] | None = None,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=max(1, int(refresh_interval_minutes))),
        )

        self.api = DHLParcelNLAPI(
            session=async_get_clientsession(hass),
            account_type=account_type,
            user_id=user_id,
            api_key=api_key,
            email=email,
            password=password,
        )
        self.postal_code = postal_code
        self.delivered_keep_days = delivered_keep_days
        self.refresh_interval_minutes = max(1, int(refresh_interval_minutes))
        self.refresh_start_time = self._parse_time(
            refresh_start_time, DEFAULT_REFRESH_START_TIME
        )
        self.refresh_end_time = self._parse_time(
            refresh_end_time, DEFAULT_REFRESH_END_TIME
        )
        self.summary_language = (
            summary_language
            if summary_language in ("en", "pl", "nl")
            else DEFAULT_SUMMARY_LANGUAGE
        )
        self.tracking_codes = tracking_codes or []
        self.manual_tracking_codes = set(self.tracking_codes)
        self.previous_states: Dict[str, str] = {}
        self.previous_substatuses: Dict[str, str | None] = {}
        self.previous_delivery_timeframes: Dict[str, Any] = {}
        self.delivered_since: Dict[str, datetime] = {}
        self.expired_tracking_codes: set[str] = set()

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from API."""
        if not self._is_in_refresh_window():
            _LOGGER.debug("Outside refresh window; skipping DHL poll")
            return dict(self.data) if isinstance(self.data, dict) else {}

        data = {}
        previous_data = dict(self.data) if isinstance(self.data, dict) else {}
        newly_discovered_codes: set[str] = set()

        # Automatically sync parcels from account if authenticated
        if (self.api.email and self.api.password) or (
            self.api.user_id and self.api.api_key
        ):
            try:
                account_parcels = await self.api.get_all_shipments()
                account_set = set(account_parcels)
                changed_tracking_set = False

                for tracking_code in account_set:
                    if tracking_code in self.expired_tracking_codes:
                        continue
                    if tracking_code not in self.tracking_codes:
                        _LOGGER.info(
                            "New parcel detected from account: %s", tracking_code
                        )
                        self.tracking_codes.append(tracking_code)
                        newly_discovered_codes.add(tracking_code)
                        changed_tracking_set = True

                removed_from_account: list[str] = []
                for tracking_code in list(self.tracking_codes):
                    if tracking_code in account_set:
                        continue
                    if tracking_code in self.manual_tracking_codes:
                        continue

                    prev_payload = previous_data.get(tracking_code, {})
                    if prev_payload.get("is_delivered"):
                        delivered_at_raw = prev_payload.get("delivered_at")
                        delivered_dt = None
                        if isinstance(delivered_at_raw, str):
                            try:
                                delivered_dt = datetime.fromisoformat(
                                    delivered_at_raw.replace("Z", "+00:00")
                                )
                            except Exception:
                                delivered_dt = None
                        self.delivered_since.setdefault(
                            tracking_code,
                            self._normalize_datetime(delivered_dt or datetime.now()),
                        )
                        continue

                    removed_from_account.append(tracking_code)

                for tracking_code in removed_from_account:
                    self.tracking_codes = [
                        c for c in self.tracking_codes if c != tracking_code
                    ]
                    self.previous_states.pop(tracking_code, None)
                    self.previous_substatuses.pop(tracking_code, None)
                    self.previous_delivery_timeframes.pop(tracking_code, None)
                    self.delivered_since.pop(tracking_code, None)
                    changed_tracking_set = True
                    self.hass.bus.async_fire(
                        EVENT_DHL_PARCEL_REMOVED,
                        {
                            "tracking_code": tracking_code,
                            "reason": "removed_from_account",
                            "language": self.summary_language,
                        },
                    )

                _LOGGER.debug(
                    "Account sync discovered %s tracking codes",
                    len(self.tracking_codes),
                )
                if changed_tracking_set:
                    # ensure platforms refresh and can create new entities
                    self.async_update_listeners()
            except Exception as err:
                _LOGGER.warning("Could not sync parcels from account: %s", err)

        # Ensure delivered parcels from retention remain visible even if removed
        # from account listing, until retention window expires.
        for tracking_code in list(self.delivered_since):
            if tracking_code not in self.tracking_codes:
                self.tracking_codes.append(tracking_code)

        for tracking_code in list(self.tracking_codes):
            try:
                tracking_data = await self.api.get_tracking_info(
                    tracking_code, self.postal_code
                )

                self._check_for_status_change(tracking_code, tracking_data)
                self._check_for_substatus_change(tracking_code, tracking_data)
                self._check_for_delivery_window_change(tracking_code, tracking_data)

                if tracking_data.get("is_delivered"):
                    delivered_at_raw = tracking_data.get("delivered_at")
                    delivered_dt = None
                    if isinstance(delivered_at_raw, str):
                        try:
                            delivered_dt = datetime.fromisoformat(
                                delivered_at_raw.replace("Z", "+00:00")
                            )
                        except Exception:
                            delivered_dt = None
                    self.delivered_since.setdefault(
                        tracking_code,
                        self._normalize_datetime(delivered_dt or datetime.now()),
                    )
                else:
                    self.delivered_since.pop(tracking_code, None)

                self.hass.bus.async_fire(
                    EVENT_DHL_PARCEL_UPDATED,
                    {
                        "tracking_code": tracking_code,
                        "status": tracking_data.get("status_category"),
                        "status_localized": _status_for_lang(
                            tracking_data.get("status_category"), self.summary_language
                        ),
                        "delivery_date": tracking_data.get("delivery_date"),
                        "delivery_timeframe": tracking_data.get("delivery_timeframe"),
                        "sender": _to_sender_name(tracking_data.get("sender")),
                        "recipient": tracking_data.get("recipient"),
                        "delivered_at": tracking_data.get("delivered_at"),
                        "delivery_location": tracking_data.get("delivery_location"),
                        "language": self.summary_language,
                        "data": tracking_data,
                    },
                )
                if tracking_code in newly_discovered_codes and not tracking_data.get(
                    "is_delivered"
                ):
                    self.hass.bus.async_fire(
                        EVENT_DHL_PARCEL_DISCOVERED,
                        {
                            "tracking_code": tracking_code,
                            "source": "account_sync",
                            "sender": _to_sender_name(tracking_data.get("sender")),
                            "status": tracking_data.get("status_category"),
                            "status_localized": _status_for_lang(
                                tracking_data.get("status_category"),
                                self.summary_language,
                            ),
                            "delivered_at": tracking_data.get("delivered_at"),
                            "delivery_location": tracking_data.get("delivery_location"),
                            "language": self.summary_language,
                            "data": tracking_data,
                        },
                    )
                data[tracking_code] = tracking_data

            except Exception as err:
                _LOGGER.error("Error updating tracking %s: %s", tracking_code, err)
                if tracking_code in previous_data:
                    data[tracking_code] = previous_data[tracking_code]

        self._remove_expired_delivered(data)

        return data

    def _remove_expired_delivered(self, data: Dict[str, Any]) -> None:
        """Remove delivered parcels after configured retention period."""
        if self.delivered_keep_days < 0:
            return

        now = datetime.now(timezone.utc)
        to_remove: list[str] = []

        for tracking_code, delivered_at in self.delivered_since.items():
            age_days = (now - self._normalize_datetime(delivered_at)).days
            if age_days >= self.delivered_keep_days:
                to_remove.append(tracking_code)

        for tracking_code in to_remove:
            _LOGGER.info(
                "Removing delivered parcel %s after %s days",
                tracking_code,
                self.delivered_keep_days,
            )
            self.tracking_codes = [c for c in self.tracking_codes if c != tracking_code]
            self.delivered_since.pop(tracking_code, None)
            self.previous_states.pop(tracking_code, None)
            self.previous_substatuses.pop(tracking_code, None)
            self.previous_delivery_timeframes.pop(tracking_code, None)
            data.pop(tracking_code, None)
            self.expired_tracking_codes.add(tracking_code)
            self.hass.bus.async_fire(
                EVENT_DHL_PARCEL_REMOVED,
                {
                    "tracking_code": tracking_code,
                    "reason": "delivered_retention_expired",
                    "retention_days": self.delivered_keep_days,
                },
            )

    def _check_for_status_change(
        self, tracking_code: str, tracking_data: Dict[str, Any]
    ) -> None:
        """Check if status changed and fire event."""
        current_status = tracking_data.get("status_category", "UNKNOWN")
        previous_status = self.previous_states.get(tracking_code)

        if previous_status and previous_status != current_status:
            _LOGGER.debug(
                "Status changed for %s: %s -> %s",
                tracking_code,
                previous_status,
                current_status,
            )

            self.hass.bus.async_fire(
                EVENT_DHL_PARCEL_STATUS_CHANGED,
                {
                    "tracking_code": tracking_code,
                    "old_status": previous_status,
                    "new_status": current_status,
                    "old_status_localized": _status_for_lang(
                        previous_status, self.summary_language
                    ),
                    "new_status_localized": _status_for_lang(
                        current_status, self.summary_language
                    ),
                    "sender": _to_sender_name(tracking_data.get("sender")),
                    "delivered_at": tracking_data.get("delivered_at"),
                    "delivery_location": tracking_data.get("delivery_location"),
                    "language": self.summary_language,
                    "data": tracking_data,
                },
            )

        self.previous_states[tracking_code] = current_status

    def _check_for_substatus_change(
        self, tracking_code: str, tracking_data: Dict[str, Any]
    ) -> None:
        """Check if substatus changed and fire event."""
        current_substatus = tracking_data.get("last_event_status")
        previous_substatus = self.previous_substatuses.get(tracking_code)

        if (
            current_substatus
            and previous_substatus
            and previous_substatus != current_substatus
        ):
            self.hass.bus.async_fire(
                EVENT_DHL_PARCEL_SUBSTATUS_CHANGED,
                {
                    "tracking_code": tracking_code,
                    "old_substatus": previous_substatus,
                    "new_substatus": current_substatus,
                    "status": tracking_data.get("status_category"),
                    "status_localized": _status_for_lang(
                        tracking_data.get("status_category"), self.summary_language
                    ),
                    "sender": _to_sender_name(tracking_data.get("sender")),
                    "delivered_at": tracking_data.get("delivered_at"),
                    "delivery_location": tracking_data.get("delivery_location"),
                    "language": self.summary_language,
                    "data": tracking_data,
                },
            )

        self.previous_substatuses[tracking_code] = current_substatus

    def _check_for_delivery_window_change(
        self, tracking_code: str, tracking_data: Dict[str, Any]
    ) -> None:
        """Check if delivery window changed and fire event."""
        current_window = tracking_data.get("delivery_timeframe")
        previous_window = self.previous_delivery_timeframes.get(tracking_code)

        if current_window and current_window != previous_window:
            self.hass.bus.async_fire(
                EVENT_DHL_PARCEL_DELIVERY_WINDOW_CHANGED,
                {
                    "tracking_code": tracking_code,
                    "old_delivery_timeframe": previous_window,
                    "new_delivery_timeframe": current_window,
                    "status": tracking_data.get("status_category"),
                    "status_localized": _status_for_lang(
                        tracking_data.get("status_category"), self.summary_language
                    ),
                    "sender": _to_sender_name(tracking_data.get("sender")),
                    "delivered_at": tracking_data.get("delivered_at"),
                    "delivery_location": tracking_data.get("delivery_location"),
                    "language": self.summary_language,
                    "data": tracking_data,
                },
            )

        self.previous_delivery_timeframes[tracking_code] = current_window

    def add_tracking_code(self, tracking_code: str) -> None:
        """Add new tracking code to monitor."""
        if tracking_code not in self.tracking_codes:
            self.tracking_codes.append(tracking_code)
            self.hass.async_create_task(self.async_request_refresh())

    def remove_tracking_code(self, tracking_code: str) -> None:
        """Remove tracking code from monitoring."""
        if tracking_code in self.tracking_codes:
            self.tracking_codes.remove(tracking_code)
            self.previous_states.pop(tracking_code, None)
            if self.data and tracking_code in self.data:
                del self.data[tracking_code]

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
        """Check if current local time is within allowed refresh window."""
        now = datetime.now().time()
        start = self.refresh_start_time
        end = self.refresh_end_time
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end

    def _normalize_datetime(self, value: datetime) -> datetime:
        """Normalize datetime to timezone-aware UTC."""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
