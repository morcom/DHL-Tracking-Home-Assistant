"""DHL Parcel NL API client."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from aiohttp import ClientError, ClientResponseError

from .const import ACCOUNT_TYPE_BUSINESS, ACCOUNT_TYPE_CONSUMER

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api-gw.dhlparcel.nl"
CONSUMER_BASE_URL = "https://my.dhlecommerce.nl"
TRACK_TRACE_ENDPOINT = "/track-trace"
AUTH_API_KEY_ENDPOINT = "/authenticate/api-key"
REFRESH_TOKEN_ENDPOINT = "/authenticate/refresh-token"
SHIPMENTS_ENDPOINT = "/shipments"
CONSUMER_LOGIN_ENDPOINT = "/api/user/login"
CONSUMER_PARCELS_ENDPOINT = "/receiver-parcel-api/parcels"


class DHLParcelNLAPI:
    """DHL Parcel NL API client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        account_type: str | None = None,
        user_id: str | None = None,
        api_key: str | None = None,
        email: str | None = None,
        password: str | None = None,
    ) -> None:
        """Initialize API client."""
        self.account_type = account_type or ACCOUNT_TYPE_CONSUMER
        self.session = session
        self.user_id = user_id
        self.api_key = api_key
        self.email = email
        self.password = password
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.token_expires_at: datetime | None = None
        self.is_consumer_account = False

    async def get_tracking_info(
        self, tracking_code: str, postal_code: str | None = None
    ) -> dict[str, Any]:
        """Get tracking information for a parcel."""
        params = {"key": tracking_code}
        if postal_code:
            params["key"] = f"{tracking_code}+{postal_code}"

        headers = {"Accept": "application/json"}

        try:
            async with asyncio.timeout(10):
                response = await self.session.get(
                    f"{BASE_URL}{TRACK_TRACE_ENDPOINT}",
                    params=params,
                    headers=headers,
                )
                response.raise_for_status()
                data = await response.json()

                if isinstance(data, list) and len(data) > 0:
                    return self._parse_tracking_response(data[0])
                if isinstance(data, dict):
                    return self._parse_tracking_response(data)
                return {}

        except ClientResponseError as err:
            _LOGGER.error("API error fetching tracking %s: %s", tracking_code, err)
            raise
        except ClientError as err:
            _LOGGER.error("Network error fetching tracking %s: %s", tracking_code, err)
            raise

    def _parse_tracking_response(self, data: dict[str, Any]) -> dict[str, Any]:
        """Parse and normalize tracking response."""
        events = []
        status_category = "UNKNOWN"
        is_delivered = False
        delivery_date = None
        delivery_timeframe = None

        raw = data
        raw_events = (
            raw.get("events", []) if isinstance(raw.get("events"), list) else []
        )
        if "events" in data:
            for event in data["events"]:
                parsed_event = self._parse_event(event)
                events.append(parsed_event)

                if parsed_event["category"] == "DELIVERED":
                    is_delivered = True

                status_category = parsed_event["category"]

        if "plannedDeliveryDate" in data:
            delivery_date = data["plannedDeliveryDate"]

        if raw.get("plannedDeliveryTimeframe") and not delivery_timeframe:
            delivery_timeframe = raw.get("plannedDeliveryTimeframe")

        if not delivery_timeframe and raw_events:
            for event in reversed(raw_events):
                timeframe = event.get("plannedDeliveryTimeframe")
                if timeframe:
                    delivery_timeframe = timeframe
                    break

        if (
            not delivery_date
            and delivery_timeframe
            and isinstance(delivery_timeframe, str)
        ):
            delivery_date = delivery_timeframe.split("/")[0]

        delivered_at = raw.get("deliveredAt")
        if not delivered_at and raw_events:
            for event in reversed(raw_events):
                if event.get("category") == "DELIVERED":
                    delivered_at = (
                        event.get("momentIndication")
                        or event.get("localTimestamp")
                        or event.get("timestamp")
                    )
                    break

        sender = data.get("sender") or data.get("shipper")
        sender_name = None
        if isinstance(sender, dict):
            sender_name = sender.get("name") or sender.get("companyName")
        elif isinstance(sender, str):
            sender_name = sender

        shipper_name = None
        if isinstance(raw.get("shipper"), dict):
            shipper_name = raw["shipper"].get("name")

        if "plannedDeliveryTimeframe" in data:
            delivery_timeframe = data["plannedDeliveryTimeframe"]

        return {
            "barcode": data.get("barcode"),
            "status_category": status_category,
            "status_text": data.get("status") or data.get("statusText"),
            "is_delivered": is_delivered,
            "delivery_date": delivery_date,
            "delivery_timeframe": delivery_timeframe,
            "events": events,
            "sender": sender,
            "sender_name": sender_name,
            "shipper_name": shipper_name,
            "recipient": data.get("recipient"),
            "product": data.get("product"),
            "weight": data.get("weight"),
            "parcel_shop": data.get("parcelShop"),
            "estimated_delivery": data.get("estimatedDeliveryTime")
            or data.get("estimatedTimeOfDelivery"),
            "delivered_at": delivered_at,
            "last_event_status": events[-1].get("status") if events else None,
            "raw": data,
        }

    def _parse_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Parse individual tracking event."""
        return {
            "timestamp": event.get("date") or event.get("timestamp"),
            "local_timestamp": event.get("localTimestamp")
            or event.get("momentIndication"),
            "category": event.get("category", "UNKNOWN"),
            "status": event.get("status"),
            "description": event.get("description"),
            "location": event.get("location", {}).get("name")
            if event.get("location")
            else None,
            "country_code": event.get("location", {}).get("countryCode")
            if event.get("location")
            else None,
        }

    async def authenticate(self) -> None:
        """Authenticate with API."""
        if self.access_token and self.token_expires_at:
            if datetime.now() < self.token_expires_at - timedelta(minutes=5):
                return
            if self.account_type == ACCOUNT_TYPE_BUSINESS and self.refresh_token:
                refreshed = await self._refresh_business_token()
                if refreshed:
                    return

        if self.account_type == ACCOUNT_TYPE_CONSUMER:
            if not (self.email and self.password):
                raise ValueError("Consumer account requires email and password")
            await self._authenticate_consumer()
            self.is_consumer_account = True
            return

        if self.account_type == ACCOUNT_TYPE_BUSINESS:
            if not (self.user_id and self.api_key):
                raise ValueError("Business account requires user_id and api_key")
            await self._authenticate_business()
            return

        raise ValueError("Unknown account type configured")

    async def _authenticate_consumer(self) -> None:
        """Authenticate consumer account with email/password."""
        try:
            async with asyncio.timeout(10):
                response = await self.session.post(
                    f"{CONSUMER_BASE_URL}{CONSUMER_LOGIN_ENDPOINT}",
                    json={"email": self.email, "password": self.password},
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                data = await response.json()

                self.access_token = data.get("token") or data.get("accessToken")
                # Consumer API often authenticates via session cookies.
                # Keep working even when no bearer token is returned.
                self.token_expires_at = datetime.now() + timedelta(hours=1)
                _LOGGER.debug("Successfully authenticated with consumer DHL API")

        except ClientResponseError as err:
            _LOGGER.error("Consumer authentication failed: %s", err)
            raise

    async def _authenticate_business(self) -> None:
        """Authenticate business account with user_id/api_key."""
        try:
            async with asyncio.timeout(10):
                response = await self.session.post(
                    f"{BASE_URL}{AUTH_API_KEY_ENDPOINT}",
                    json={"userId": self.user_id, "key": self.api_key},
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                data = await response.json()

                self.access_token = data.get("accessToken")
                self.refresh_token = data.get("refreshToken")
                expires_in = data.get("accessTokenExpiration", 3600)
                self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)

                _LOGGER.debug("Successfully authenticated with business DHL API")

        except ClientResponseError as err:
            _LOGGER.error("Business authentication failed: %s", err)
            raise

    async def _refresh_business_token(self) -> bool:
        """Refresh business access token."""
        if not self.refresh_token:
            return False
        try:
            async with asyncio.timeout(10):
                response = await self.session.post(
                    f"{BASE_URL}{REFRESH_TOKEN_ENDPOINT}",
                    json={"refreshToken": self.refresh_token},
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                data = await response.json()
                self.access_token = data.get("accessToken") or self.access_token
                self.refresh_token = data.get("refreshToken") or self.refresh_token
                expires_in = data.get("accessTokenExpiration", 3600)
                self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                _LOGGER.debug("Successfully refreshed business DHL API token")
                return True
        except Exception as err:
            _LOGGER.warning("Business token refresh failed, re-authenticating: %s", err)
            return False

    async def get_all_shipments(self) -> list[str]:
        """Get all active shipments/parcels from account."""
        await self.authenticate()

        if self.account_type == ACCOUNT_TYPE_BUSINESS and not self.access_token:
            return []

        try:
            if self.is_consumer_account:
                return await self._get_consumer_shipments()
            else:
                return await self._get_business_shipments()

        except Exception as err:
            _LOGGER.error("Error fetching shipments: %s", err)
            return []

    async def _get_consumer_shipments(self) -> list[str]:
        """Get parcels from consumer account."""
        headers = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        async with asyncio.timeout(10):
            response = await self.session.get(
                f"{CONSUMER_BASE_URL}{CONSUMER_PARCELS_ENDPOINT}",
                headers=headers,
            )
            response.raise_for_status()
            data = await response.json()

            tracking_codes = []
            parcels: list[dict[str, Any]] = []
            if (
                isinstance(data, dict)
                and "parcels" in data
                and isinstance(data["parcels"], list)
            ):
                parcels = data["parcels"]
            elif isinstance(data, list):
                parcels = data

            for parcel in parcels:
                tracker_code = (
                    parcel.get("trackerCode")
                    or parcel.get("trackingCode")
                    or parcel.get("barcode")
                )
                category = parcel.get("category") or parcel.get("status")
                if tracker_code and category != "DELIVERED":
                    tracking_codes.append(str(tracker_code))

            _LOGGER.debug(
                "Found %d active parcels in consumer account", len(tracking_codes)
            )
            return list(dict.fromkeys(tracking_codes))

    async def _get_business_shipments(self) -> list[str]:
        """Get parcels from business account."""
        async with asyncio.timeout(10):
            response = await self.session.get(
                f"{BASE_URL}{SHIPMENTS_ENDPOINT}",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.access_token}",
                },
                params={"status": "active", "limit": 50},
            )
            response.raise_for_status()
            data = await response.json()

            tracking_codes: list[str] = []
            shipments: list[dict[str, Any]] = []

            if isinstance(data, list):
                shipments = data
            elif isinstance(data, dict):
                for key in ("shipments", "results", "items", "content"):
                    if isinstance(data.get(key), list):
                        shipments = data[key]
                        break

            for shipment in shipments:
                tracker_code = (
                    shipment.get("trackerCode")
                    or shipment.get("trackingCode")
                    or shipment.get("barcode")
                )
                if tracker_code:
                    tracking_codes.append(str(tracker_code))

            _LOGGER.debug(
                "Found %d active parcels in business account", len(tracking_codes)
            )
            return list(dict.fromkeys(tracking_codes))

    async def test_authentication(self) -> bool:
        """Test API authentication."""
        try:
            await self.authenticate()
            return self.access_token is not None
        except Exception:
            return False
