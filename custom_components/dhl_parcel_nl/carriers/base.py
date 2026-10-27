"""Shared carrier contract.

Every carrier returns a normalized parcel dict with the keys the DHL parser
already produces, so sensors, events and automation examples work for all
carriers:

``status_category``, ``last_event_status``, ``is_delivered``, ``delivered_at``,
``delivery_timeframe`` (``{"from": iso, "to": iso}`` or ``"from/to"``),
``delivery_date``, ``sender_name``, ``delivery_location``, ``events`` and
``raw``. The coordinator adds ``carrier``, ``source`` and ``order_id``.
"""

from __future__ import annotations

from typing import Any, Protocol

CARRIER_DHL = "dhl"
CARRIER_POSTNL = "postnl"
CARRIER_DRAGONFLY = "dragonfly"
CARRIERS: tuple[str, ...] = (CARRIER_DHL, CARRIER_POSTNL, CARRIER_DRAGONFLY)

CARRIER_DISPLAY_NAMES: dict[str, str] = {
    CARRIER_DHL: "DHL",
    CARRIER_POSTNL: "PostNL",
    CARRIER_DRAGONFLY: "Dragonfly",
}

SOURCE_DHL_ACCOUNT = "dhl_account"
SOURCE_POSTNL_ACCOUNT = "postnl_account"
SOURCE_AMAZON = "amazon"
SOURCE_MANUAL = "manual"
ACCOUNT_SOURCES: frozenset[str] = frozenset({SOURCE_DHL_ACCOUNT, SOURCE_POSTNL_ACCOUNT})

USER_AGENT = "HomeAssistant-ParcelTrackingNL/2.0 (+https://github.com/morcom/DHL-Tracking-Home-Assistant)"


class TrackingError(Exception):
    """Tracking information could not be retrieved."""


class TrackingNotFound(TrackingError):
    """The carrier does not (yet) know the tracking code."""


class CarrierBackoff(TrackingError):
    """Carrier is rate limiting or failing (HTTP 429/5xx); skip one cycle."""


class MissingPostalCode(TrackingError):
    """The carrier needs the receiver's postal code for this parcel."""


class Carrier(Protocol):
    """Carrier client protocol."""

    name: str
    supports_account: bool

    async def async_track(
        self, code: str, postal_code: str | None = None
    ) -> dict[str, Any]:
        """Return normalized tracking data for one parcel."""

    async def async_list_account(self) -> list[str]:
        """Return tracking codes from the user's carrier account."""


def unknown_parcel(code: str) -> dict[str, Any]:
    """Normalized payload for a parcel the carrier does not know yet."""
    return {
        "barcode": code,
        "status_category": "UNKNOWN",
        "is_delivered": False,
        "delivery_date": None,
        "delivery_timeframe": None,
        "events": [],
        "sender": None,
        "sender_name": None,
        "shipper_name": None,
        "recipient": None,
        "delivered_at": None,
        "delivery_location": None,
        "last_event_status": None,
        "raw": {},
    }
