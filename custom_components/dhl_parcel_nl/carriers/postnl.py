"""PostNL carrier: public Track & Trace plus optional account sync.

Public tracking (no login, receiver postal code required)::

    GET https://jouw.postnl.nl/track-and-trace/api/trackAndTrace/
        {barcode}-{country}-{postcode}?language=nl|en

Account sync uses the PostNL app login (OAuth2 authorization code + PKCE,
the same unofficial client as arjenbos/ha-postnl). The user logs in in the
browser and pastes the final ``postnl://login?code=...`` URL. The tracked
shipments come from the account GraphQL API. Both APIs are unofficial.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

import aiohttp

from .base import (
    CARRIER_POSTNL,
    USER_AGENT,
    CarrierBackoff,
    MissingPostalCode,
    TrackingError,
    TrackingNotFound,
)

_LOGGER = logging.getLogger(__name__)

TRACK_URL = (
    "https://jouw.postnl.nl/track-and-trace/api/trackAndTrace/{key}?language={lang}"
)
PUBLIC_TRACKING_URL = "https://jouw.postnl.nl/track-and-trace/{code}-{cc}-{postcode}"
GRAPHQL_URL = "https://jouw.postnl.nl/account/api/graphql"

OAUTH_BASE = "https://login.postnl.nl/101112a0-4a0f-4bbb-8176-2f1b2d370d7c/login"
AUTHORIZE_URL = f"{OAUTH_BASE}/authorize"
TOKEN_URL = f"{OAUTH_BASE}/token"
CLIENT_ID = "deb0a372-6d72-4e09-83fe-997beacbd137"
REDIRECT_URI = "postnl://login"
SCOPES = "profile openid email address phone poa-profiles-api"

SUBSTATUS_PREFIX = "POSTNL_"

SHIPMENTS_QUERY = """query {
  trackedShipments {
    receiverShipments {
      key
      barcode
      title
      delivered
      deliveredTimeStamp
      deliveryWindowFrom
      deliveryWindowTo
      shipmentType
      sourceDisplayName
    }
  }
}"""

# statusPhase.index, same phases as PostNL's official Shipping Status API:
# 1 Collection, 2 Sorting, 3 Distribution, 4 Delivery. Only a fallback when
# the observation code is unknown; phase 4 alone does not mean delivered
# (it also covers "waiting at a PostNL point" and missed attempts).
_PHASE_CATEGORY = {
    1: "DATA_RECEIVED",
    2: "UNDERWAY",
    3: "IN_DELIVERY",
    4: "IN_DELIVERY",
}


class PostNLAuthError(TrackingError):
    """PostNL login is invalid or expired; reauthentication is needed."""


# ----------------------------------------------------------------------
# OAuth (PKCE paste flow)
# ----------------------------------------------------------------------


def new_pkce() -> dict[str, str]:
    """Create a PKCE verifier/challenge and state."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return {
        "code_verifier": verifier,
        "code_challenge": challenge,
        "state": secrets.token_urlsafe(24),
    }


def authorize_url(pkce: dict[str, str]) -> str:
    """Return the URL the user opens to log in."""
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "code_challenge": pkce["code_challenge"],
        "code_challenge_method": "S256",
        "state": pkce["state"],
    }
    return f"{AUTHORIZE_URL}?{urlencode(params, quote_via=quote)}"


def code_from_redirect(redirect: str, expected_state: str | None = None) -> str:
    """Extract the authorization code from the pasted redirect URL."""
    text = (redirect or "").strip()
    if not text:
        raise ValueError("empty")
    query = urlparse(text).query if "?" in text else text
    params = parse_qs(query)
    code = (params.get("code") or [None])[0]
    if not code:
        raise ValueError("no_code")
    state = (params.get("state") or [None])[0]
    if expected_state and state and state != expected_state:
        raise ValueError("state_mismatch")
    return code


class PostNLAuth:
    """Holds and refreshes PostNL OAuth tokens. Tokens are never logged."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: dict[str, Any] | None = None,
        on_token_update: Callable[[dict[str, Any]], Awaitable[None] | None]
        | None = None,
    ) -> None:
        """Initialize."""
        self.session = session
        self.token: dict[str, Any] = dict(token or {})
        self.on_token_update = on_token_update

    @property
    def has_refresh_token(self) -> bool:
        """Return True when a refresh token is available."""
        return bool(self.token.get("refresh_token"))

    async def _token_request(self, form: dict[str, str]) -> dict[str, Any]:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        try:
            async with asyncio.timeout(15):
                response = await self.session.post(
                    TOKEN_URL, data=form, headers=headers
                )
                if response.status in (400, 401, 403):
                    raise PostNLAuthError(f"token endpoint HTTP {response.status}")
                response.raise_for_status()
                data = await response.json(content_type=None)
        except PostNLAuthError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TrackingError(f"PostNL token request failed: {err}") from err
        if not isinstance(data, dict) or not data.get("access_token"):
            raise PostNLAuthError("token response without access_token")
        expires_in = int(data.get("expires_in") or 3600)
        token = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token")
            or self.token.get("refresh_token"),
            "expires_at": time.time() + expires_in,
        }
        self.token = token
        if self.on_token_update is not None:
            result = self.on_token_update(dict(token))
            if asyncio.iscoroutine(result):
                await result
        return token

    async def async_exchange_code(
        self, code: str, code_verifier: str
    ) -> dict[str, Any]:
        """Exchange an authorization code for tokens."""
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "code": code,
                "code_verifier": code_verifier,
            }
        )

    async def async_get_access_token(self) -> str:
        """Return a valid access token, refreshing it before expiry."""
        if (
            self.token.get("access_token")
            and float(self.token.get("expires_at") or 0) > time.time() + 120
        ):
            return self.token["access_token"]
        if not self.has_refresh_token:
            raise PostNLAuthError("no refresh token")
        token = await self._token_request(
            {
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": self.token["refresh_token"],
            }
        )
        return token["access_token"]


# ----------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------


def _first(data: Any, *keys: str) -> Any:
    if not isinstance(data, dict):
        return None
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _window(block: Any) -> tuple[str | None, str | None]:
    if not isinstance(block, dict):
        return None, None
    start = _first(block, "startDateTime", "start", "from", "startTime")
    end = _first(block, "endDateTime", "end", "to", "endTime")
    return start, end


def _format_address(addr: Any) -> str | None:
    if not isinstance(addr, dict):
        return None
    number = "".join(
        str(v)
        for v in (
            _first(addr, "houseNumber", "houseNr"),
            _first(addr, "houseNumberSuffix", "houseNrExt", "addition"),
        )
        if v
    )
    line1 = " ".join(str(v) for v in (addr.get("street"), number) if v)
    line2 = " ".join(
        str(v)
        for v in (
            _first(addr, "postalCode", "postcode"),
            addr.get("town") or addr.get("city"),
        )
        if v
    )
    country = _first(addr, "country", "countryCode")
    if country and str(country).upper() in ("NL", "NEDERLAND", "NETHERLANDS"):
        country = None
    parts = [p for p in (line1, line2, country) if p]
    return ", ".join(str(p) for p in parts) if parts else None


def _pickup_location(colli: dict[str, Any]) -> str | None:
    """Name and address of the PostNL point (``retailDeliveryLocation``)."""
    for key in ("retailDeliveryLocation", "retailLocation", "pickupLocation"):
        block = colli.get(key)
        if isinstance(block, dict):
            name = _first(block, "locationName", "name", "companyName")
            address = _format_address(block.get("address") or block)
            text = ", ".join(p for p in (name, address) if p)
            if text:
                return text
    return None


def _delivery_address(colli: dict[str, Any]) -> str | None:
    """Delivery address without names (like DHL's delivery_location)."""
    block = colli.get("deliveryAddress")
    if isinstance(block, dict):
        return _format_address(block.get("address"))
    return None


def _obs_time(obs: dict[str, Any]) -> datetime:
    raw = str(_first(obs, "observationDate", "dateTime", "date") or "")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _observations(colli: dict[str, Any]) -> list[dict[str, Any]]:
    """Customer-facing observations, oldest first.

    The API returns them newest first and leaves out the final delivered or
    collected scan (that one is only in ``analyticsInfo.allObservations``).
    """
    items = colli.get("observations")
    if not isinstance(items, list):
        return []
    obs = [o for o in items if isinstance(o, dict)]
    obs.sort(key=_obs_time)
    return obs


def _substatus(observation: dict[str, Any] | None) -> str | None:
    if not observation:
        return None
    code = _first(observation, "observationCode", "code")
    return f"{SUBSTATUS_PREFIX}{code}" if code else None


# Observation code -> shared status (verified in real responses).
_OBSERVATION_CATEGORY = {
    "A01": "DATA_RECEIVED",
    "A94": "INTERVENTION",
    "B01": "UNDERWAY",
    "J01": "UNDERWAY",
    "J40": "UNDERWAY",
    "J55": "UNDERWAY",
    "J05": "IN_DELIVERY",
    "J12": "READY_FOR_PICKUP",
    "I01": "DELIVERED",
}


def parse_postnl(
    colli: dict[str, Any], code: str, postcode: str = ""
) -> dict[str, Any]:
    """Normalize one ``colli`` item of the Track & Trace API.

    Verified against real responses: ``isDelivered``, ``deliveryDate``,
    ``isAtRetailLocation``, ``isPickedUpAtRetailLocation``,
    ``retailDeliveryLocation{locationName,address}``, ``deliveryAddress``,
    ``eta{type: WholeDay|Specific, start, end}``, ``statusPhase{index,message}``,
    ``observations[]{observationDate,observationCode,description}`` (newest
    first, without the final scan) and ``sender.names.companyName``.
    ``routeInformation`` windows follow ha-postnl.
    """
    observations = _observations(colli)
    last_obs = observations[-1] if observations else None
    last_code = str(_first(last_obs or {}, "observationCode") or "")

    phase = (
        colli.get("statusPhase") if isinstance(colli.get("statusPhase"), dict) else {}
    )
    phase_index = phase.get("index")
    phase_message = phase.get("message") or phase.get("header")
    message = str(phase_message or "").lower()
    route = (
        colli.get("routeInformation")
        if isinstance(colli.get("routeInformation"), dict)
        else {}
    )

    picked_up = bool(colli.get("isPickedUpAtRetailLocation"))
    is_delivered = bool(colli.get("isDelivered") or picked_up)

    if is_delivered:
        status = "DELIVERED"
    elif colli.get("isAtRetailLocation") or last_code == "J12":
        status = "READY_FOR_PICKUP"
    elif last_code in _OBSERVATION_CATEGORY:
        status = _OBSERVATION_CATEGORY[last_code]
    elif any(
        word in message
        for word in ("bezorger is onderweg", "driver is en route", "out for delivery")
    ):
        status = "IN_DELIVERY"
    elif isinstance(phase_index, int) and phase_index in _PHASE_CATEGORY:
        status = _PHASE_CATEGORY[phase_index]
    elif message:
        status = "UNDERWAY"
    else:
        status = "UNKNOWN"
    if status == "DELIVERED":
        is_delivered = True

    start, end = _window(route.get("expectedDeliveryTimeWindow"))
    if not start:
        start, end = _window(route.get("plannedDeliveryTimeWindow"))
    eta = colli.get("eta") if isinstance(colli.get("eta"), dict) else {}
    if not start and eta.get("type") != "WholeDay":
        start, end = _window(eta)
    timeframe = f"{start}/{end}" if start and end and not is_delivered else None
    planned = (
        start
        or route.get("expectedDeliveryTime")
        or route.get("plannedDeliveryTime")
        or eta.get("start")
    )
    delivery_date = str(planned or "")[:10] or None

    delivered_at = None
    if is_delivered:
        delivered_at = _first(colli, "deliveryDate", "deliveredAt")
        if not delivered_at and last_obs:
            delivered_at = _first(last_obs, "observationDate", "dateTime")

    sender = colli.get("sender")
    sender_name = None
    if isinstance(sender, dict):
        names = sender.get("names") if isinstance(sender.get("names"), dict) else {}
        sender_name = _first(names, "companyName", "personName") or _first(
            sender, "companyName", "name"
        )
    elif isinstance(sender, str):
        sender_name = sender

    events = [
        {
            "timestamp": _first(obs, "observationDate", "dateTime", "date"),
            "local_timestamp": None,
            "category": _OBSERVATION_CATEGORY.get(
                str(_first(obs, "observationCode") or "")
            ),
            "status": _substatus(obs),
            "description": obs.get("description"),
            "location": None,
            "country_code": None,
        }
        for obs in observations
    ]

    last_status = _substatus(last_obs)
    last_description = (last_obs or {}).get("description")
    if is_delivered:
        # The final scan is missing from ``observations``; add it.
        last_status = f"{SUBSTATUS_PREFIX}{'PICKED_UP' if picked_up else 'I01'}"
        last_description = phase_message or last_description
        events.append(
            {
                "timestamp": delivered_at,
                "local_timestamp": None,
                "category": "DELIVERED",
                "status": last_status,
                "description": phase_message,
                "location": None,
                "country_code": None,
            }
        )
    elif not last_status and phase_message:
        last_description = phase_message

    if status == "READY_FOR_PICKUP" or picked_up:
        delivery_location = _pickup_location(colli)
    elif is_delivered:
        delivery_location = _delivery_address(colli)
    else:
        delivery_location = None

    return {
        "barcode": colli.get("barcode") or code,
        "status_category": status,
        "status_text": phase_message,
        "is_delivered": is_delivered,
        "delivery_date": delivery_date,
        "delivery_timeframe": timeframe,
        "events": events,
        "sender": None,
        "sender_name": sender_name,
        "shipper_name": None,
        "recipient": None,
        "product": (colli.get("context") or {}).get("shipmentType")
        if isinstance(colli.get("context"), dict)
        else None,
        "weight": None,
        "parcel_shop": None,
        "estimated_delivery": planned,
        "delivered_at": delivered_at,
        "delivery_location": delivery_location,
        "last_event_status": last_status,
        "last_event_description": last_description or phase_message,
        "tracking_url": PUBLIC_TRACKING_URL.format(
            code=code, cc="NL", postcode=postcode
        )
        if postcode
        else None,
        "raw": colli,
    }


# ----------------------------------------------------------------------
# Carrier
# ----------------------------------------------------------------------


class PostNLCarrier:
    """PostNL carrier client."""

    name = CARRIER_POSTNL
    supports_account = True

    def __init__(
        self,
        session: aiohttp.ClientSession,
        language: str = "en",
        auth: PostNLAuth | None = None,
    ) -> None:
        """Initialize."""
        self.session = session
        self.language = "nl" if language == "nl" else "en"
        self.auth = auth
        self._account_disabled = False
        # code -> {"key": ..., "postal_code": ..., "sender": ...}
        self.account_hints: dict[str, dict[str, Any]] = {}

    @property
    def has_account(self) -> bool:
        """Return True when the PostNL account login is usable."""
        return bool(
            self.auth and self.auth.has_refresh_token and not self._account_disabled
        )

    def disable_account(self) -> None:
        """Stop account calls until reauthentication."""
        self._account_disabled = True

    async def _get_json(self, url: str, headers: dict[str, str]) -> Any:
        try:
            async with asyncio.timeout(15):
                response = await self.session.get(url, headers=headers)
                if response.status == 429 or response.status >= 500:
                    raise CarrierBackoff(f"PostNL HTTP {response.status}")
                if response.status in (401, 403):
                    raise PostNLAuthError(f"HTTP {response.status}")
                if response.status == 404:
                    raise TrackingNotFound(url)
                response.raise_for_status()
                return await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CarrierBackoff(f"PostNL request failed: {err}") from err

    async def async_track(
        self, code: str, postal_code: str | None = None
    ) -> dict[str, Any]:
        """Fetch and normalize one PostNL parcel."""
        hint = self.account_hints.get(code) or {}
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        postcode = (
            (postal_code or hint.get("postal_code") or "").replace(" ", "").upper()
        )
        key = hint.get("key")
        if key and self.has_account:
            try:
                token = await self.auth.async_get_access_token()
                headers["Authorization"] = f"Bearer {token}"
            except PostNLAuthError:
                key = None
                headers.pop("Authorization", None)
        else:
            key = None
        if not key:
            if not postcode:
                raise MissingPostalCode(code)
            key = f"{code}-NL-{postcode}"

        payload = await self._get_json(
            TRACK_URL.format(key=quote(key, safe=""), lang=self.language), headers
        )
        colli_map = payload.get("colli") if isinstance(payload, dict) else None
        if not isinstance(colli_map, dict) or not colli_map:
            raise TrackingNotFound(code)
        colli = colli_map.get(code) or next(iter(colli_map.values()))
        if not isinstance(colli, dict):
            raise TrackingNotFound(code)
        parsed = parse_postnl(colli, code, postcode)
        if not parsed.get("sender_name") and hint.get("sender"):
            parsed["sender_name"] = hint["sender"]
        if not parsed.get("delivery_timeframe") and hint.get("window"):
            parsed["delivery_timeframe"] = hint["window"]
        return parsed

    async def async_list_account(self) -> list[str]:
        """Return barcodes of tracked shipments in the PostNL account."""
        if not self.has_account:
            return []
        token = await self.auth.async_get_access_token()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        }
        try:
            async with asyncio.timeout(15):
                response = await self.session.post(
                    GRAPHQL_URL, json={"query": SHIPMENTS_QUERY}, headers=headers
                )
                if response.status in (401, 403):
                    raise PostNLAuthError(f"GraphQL HTTP {response.status}")
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TrackingError(f"PostNL account request failed: {err}") from err

        for error in (payload or {}).get("errors") or []:
            code = ((error or {}).get("extensions") or {}).get("code")
            if code in ("UNAUTHENTICATED", "UNAUTHORIZED"):
                raise PostNLAuthError(f"GraphQL {code}")
        shipments = (
            ((payload or {}).get("data") or {}).get("trackedShipments") or {}
        ).get("receiverShipments") or []
        codes: list[str] = []
        delivered: list[str] = []
        for shipment in shipments:
            if not isinstance(shipment, dict) or not shipment.get("barcode"):
                continue
            code = str(shipment["barcode"]).upper()
            hint: dict[str, Any] = {"key": shipment.get("key")}
            if shipment.get("sourceDisplayName"):
                hint["sender"] = shipment["sourceDisplayName"]
            if shipment.get("deliveryWindowFrom") and shipment.get("deliveryWindowTo"):
                hint["window"] = (
                    f"{shipment['deliveryWindowFrom']}/{shipment['deliveryWindowTo']}"
                )
            self.account_hints[code] = hint
            if shipment.get("delivered"):
                hint["delivered_at"] = shipment.get("deliveredTimeStamp")
                delivered.append(code)
            else:
                codes.append(code)
        return list(dict.fromkeys(codes + delivered))
