"""DHL Parcel NL carrier (wraps the existing DHLParcelNLAPI client)."""

from __future__ import annotations

from typing import Any

from aiohttp import ClientResponseError

from ..api import DHLParcelNLAPI
from .base import CARRIER_DHL, TrackingNotFound


class DHLCarrier:
    """DHL carrier adapter; behavior is unchanged from earlier versions."""

    name = CARRIER_DHL
    supports_account = True

    def __init__(self, api: DHLParcelNLAPI, language: str = "en") -> None:
        """Initialize the adapter."""
        self.api = api
        self.language = language

    @property
    def has_account(self) -> bool:
        """Return True when account credentials are configured."""
        api = self.api
        return bool((api.email and api.password) or (api.user_id and api.api_key))

    async def async_track(
        self, code: str, postal_code: str | None = None
    ) -> dict[str, Any]:
        """Return normalized DHL tracking data."""
        try:
            data = await self.api.get_tracking_info(code, postal_code)
        except ClientResponseError as err:
            if err.status == 404:
                raise TrackingNotFound(code) from err
            raise
        if not data:
            raise TrackingNotFound(code)
        await self.api.enrich_delivery_location(data, self.language)
        return data

    async def async_list_account(self) -> list[str]:
        """Return tracking codes from the DHL account ([] on errors)."""
        return await self.api.get_all_shipments()
