"""Dragonfly (Intelcom) public tracking client.

Endpoint used by the dragonflyshipping.nl tracking page (unofficial):
``GET https://dragonflyshipping.nl/cfworker/v3/tracking/{id}`` returns
``{"success": bool, "data": {"code": "found"|"not_found", "result": {...}}}``.

Verified ``result`` fields: ``tracking_id``, ``eta``, ``public_eta{from,to}``,
``last_status`` and ``status_list[]`` (newest first) with ``status`` (int),
``label``, ``step`` (1-4), ``timestamp`` (ms), ``isDelivered``,
``labels.shortLabel{en,nl}`` and ``package_location.address``.
Observed statuses: 0 Data received, 105 Received, 300 Loaded,
601 Delivered - No Signature.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

import aiohttp

from .base import (
    CARRIER_DRAGONFLY,
    USER_AGENT,
    CarrierBackoff,
    TrackingError,
    TrackingNotFound,
)

_LOGGER = logging.getLogger(__name__)

TRACKING_URL = "https://dragonflyshipping.nl/cfworker/v3/tracking/{code}"
PUBLIC_TRACKING_URL = "https://dragonflyshipping.nl/tracking/{code}"

SUBSTATUS_PREFIX = "DF_"

# Step reported by the tracking page: 1 registered, 2 at depot, 3 on the
# way, 4 final (delivered or failed).
_STEP_CATEGORY = {1: "DATA_RECEIVED", 2: "UNDERWAY", 3: "IN_DELIVERY"}


def _ms_to_iso(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _substatus_code(entry: dict[str, Any]) -> str | None:
    label = entry.get("label")
    if isinstance(label, str) and label.strip():
        slug = re.sub(r"[^A-Z0-9]+", "_", label.upper()).strip("_")
        if slug:
            return f"{SUBSTATUS_PREFIX}{slug}"
    status = entry.get("status")
    return f"{SUBSTATUS_PREFIX}{status}" if status is not None else None


def _category(entry: dict[str, Any]) -> str:
    """Map a status entry to the shared status category.

    The tracking page treats ``task_type == "last_mile_pickup"`` as a pickup
    point and a negative ``step`` as an error at step ``abs(step)``.
    """
    if entry.get("isDelivered"):
        return "DELIVERED"
    label = str(entry.get("label") or "").lower()
    step = entry.get("step")
    if isinstance(step, int) and step < 0:
        if "return" in label:
            return "EXCEPTION"
        return "PROBLEM"
    if entry.get("task_type") == "last_mile_pickup" or any(
        word in label for word in ("pickup", "pick-up", "pick up")
    ):
        return "READY_FOR_PICKUP"
    if "return" in label:
        return "EXCEPTION"
    if any(word in label for word in ("attempt", "fail", "not home", "missed")):
        return "PROBLEM"
    if isinstance(step, int) and step in _STEP_CATEGORY:
        return _STEP_CATEGORY[step]
    if isinstance(step, int) and step >= 4:
        return "EXCEPTION"
    return "UNKNOWN"


def _location_text(entry: dict[str, Any]) -> str | None:
    location = entry.get("package_location")
    address = location.get("address") if isinstance(location, dict) else None
    if not isinstance(address, dict):
        return None
    parts = [
        str(address.get(key)).strip()
        for key in ("city", "state", "country")
        if address.get(key)
    ]
    return ", ".join(parts) if parts else None


def _description(entry: dict[str, Any], language: str) -> str | None:
    labels = entry.get("labels")
    short = labels.get("shortLabel") if isinstance(labels, dict) else None
    if isinstance(short, dict):
        text = short.get("nl" if language == "nl" else "en") or short.get("en")
        if text:
            return text
    return entry.get("label")


def parse_dragonfly(result: dict[str, Any], language: str = "en") -> dict[str, Any]:
    """Normalize a Dragonfly ``data.result`` payload."""
    history = [e for e in (result.get("status_list") or []) if isinstance(e, dict)]
    history = [e for e in history if not e.get("hidden")]
    history.sort(key=lambda e: e.get("timestamp") or 0)
    last = result.get("last_status")
    if not isinstance(last, dict) or last.get("hidden"):
        last = history[-1] if history else {}

    events = [
        {
            "timestamp": _ms_to_iso(entry.get("timestamp")),
            "local_timestamp": None,
            "category": _category(entry),
            "status": _substatus_code(entry),
            "description": _description(entry, language),
            "location": _location_text(entry),
            "country_code": None,
        }
        for entry in history
    ]

    status_category = _category(last) if last else "UNKNOWN"
    is_delivered = status_category == "DELIVERED"
    delivered_at = None
    delivery_location = None
    if is_delivered:
        delivered_at = _ms_to_iso(last.get("timestamp"))
        delivery_location = _location_text(last)
    elif status_category == "READY_FOR_PICKUP":
        delivery_location = _location_text(last)

    window = result.get("public_eta")
    timeframe = None
    if isinstance(window, dict) and window.get("from") and window.get("to"):
        timeframe = f"{window['from']}/{window['to']}"
    eta = result.get("eta") or result.get("live_buffered_eta")
    delivery_date = (timeframe or eta or "").split("/")[0][:10] or None

    code = str(result.get("tracking_id") or "")
    return {
        "barcode": code or None,
        "status_category": status_category,
        "status_text": _description(last, language) if last else None,
        "is_delivered": is_delivered,
        "delivery_date": delivery_date,
        "delivery_timeframe": timeframe,
        "events": events,
        "sender": None,
        "sender_name": "Amazon" if code.upper().startswith("AMZN") else None,
        "shipper_name": None,
        "recipient": None,
        "product": result.get("service_type"),
        "weight": None,
        "parcel_shop": None,
        "estimated_delivery": eta,
        "delivered_at": delivered_at,
        "delivery_location": delivery_location,
        "last_event_status": _substatus_code(last) if last else None,
        "tracking_url": PUBLIC_TRACKING_URL.format(code=code),
        "raw": result,
    }


class DragonflyCarrier:
    """Dragonfly public tracking (no account support)."""

    name = CARRIER_DRAGONFLY
    supports_account = False

    def __init__(self, session: aiohttp.ClientSession, language: str = "en") -> None:
        """Initialize client."""
        self.session = session
        self.language = language

    async def async_track(
        self, code: str, postal_code: str | None = None
    ) -> dict[str, Any]:
        """Fetch and normalize one Dragonfly parcel."""
        url = TRACKING_URL.format(code=code)
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        try:
            async with asyncio.timeout(15):
                response = await self.session.get(url, headers=headers)
                if response.status == 429 or response.status >= 500:
                    raise CarrierBackoff(f"Dragonfly HTTP {response.status}")
                if response.status == 404:
                    raise TrackingNotFound(code)
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CarrierBackoff(f"Dragonfly request failed: {err}") from err

        if not isinstance(payload, dict):
            raise TrackingError("Unexpected Dragonfly response")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if not payload.get("success") or data.get("code") == "not_found":
            raise TrackingNotFound(code)
        result = data.get("result")
        if not isinstance(result, dict):
            raise TrackingNotFound(code)
        return parse_dragonfly(result, self.language)

    async def async_list_account(self) -> list[str]:
        """Dragonfly has no consumer account API."""
        return []
