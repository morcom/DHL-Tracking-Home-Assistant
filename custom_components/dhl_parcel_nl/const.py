"""Constants for DHL Parcel NL integration."""

from typing import Final

DOMAIN: Final = "dhl_parcel_nl"
NAME: Final = "DHL Parcel Netherlands"

DEFAULT_SCAN_INTERVAL: Final = 300  # 5 minutes
DEFAULT_SCAN_INTERVAL_MINUTES: Final = 5

CONF_API_KEY: Final = "api_key"
CONF_ACCOUNT_TYPE: Final = "account_type"
CONF_USER_ID: Final = "user_id"
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"
CONF_EMAIL_ENCRYPTED: Final = "email_encrypted"
CONF_PASSWORD_ENCRYPTED: Final = "password_encrypted"
CONF_CREDENTIALS_ENCRYPTED: Final = "credentials_encrypted"
CONF_POSTAL_CODE: Final = "postal_code"
CONF_DELIVERED_KEEP_DAYS: Final = "delivered_keep_days"
CONF_REFRESH_INTERVAL_MINUTES: Final = "refresh_interval_minutes"
CONF_REFRESH_START_TIME: Final = "refresh_start_time"
CONF_REFRESH_END_TIME: Final = "refresh_end_time"
CONF_SUMMARY_LANGUAGE: Final = "summary_language"
DEFAULT_DELIVERED_KEEP_DAYS: Final = 7
DEFAULT_REFRESH_START_TIME: Final = "06:00"
DEFAULT_REFRESH_END_TIME: Final = "23:00"
DEFAULT_SUMMARY_LANGUAGE: Final = "en"

ACCOUNT_TYPE_CONSUMER: Final = "consumer"
ACCOUNT_TYPE_BUSINESS: Final = "business"

ATTR_TRACKING_CODE: Final = "tracking_code"
ATTR_STATUS: Final = "status"
ATTR_DELIVERY_DATE: Final = "delivery_date"
ATTR_DELIVERY_TIMEFRAME: Final = "delivery_timeframe"
ATTR_EVENTS: Final = "events"
ATTR_SENDER: Final = "sender"
ATTR_RECIPIENT: Final = "recipient"
ATTR_IS_DELIVERED: Final = "is_delivered"
ATTR_ESTIMATED_DELIVERY: Final = "estimated_delivery"
ATTR_PARCEL_SHOP: Final = "parcel_shop"
ATTR_PRODUCT: Final = "product"
ATTR_WEIGHT: Final = "weight"
ATTR_BARCODE: Final = "barcode"
ATTR_LATEST_EVENT: Final = "latest_event"
ATTR_RAW_STATUS: Final = "raw_status"
ATTR_EVENT_COUNT: Final = "event_count"
ATTR_LAST_UPDATE_SOURCE: Final = "last_update_source"
ATTR_SENDER_NAME: Final = "sender_name"
ATTR_SHIPPER_NAME: Final = "shipper_name"
ATTR_DELIVERED_AT: Final = "delivered_at"
ATTR_LAST_EVENT_STATUS: Final = "last_event_status"
ATTR_DELIVERY_TIME_FROM: Final = "delivery_time_from"
ATTR_DELIVERY_TIME_TO: Final = "delivery_time_to"
ATTR_DELIVERY_DAY_LABEL: Final = "delivery_day_label"
ATTR_STATUS_PL: Final = "status_pl"
ATTR_DELIVERY_LOCATION: Final = "delivery_location"

STATUS_CATEGORIES = {
    "CUSTOMS": "Customs processing",
    "DATA RECEIVED": "Data received",
    "DATA_RECEIVED": "Data received",
    "IN DELIVERY": "Out for delivery",
    "UNDERWAY": "Underway",
    "IN_DELIVERY": "Out for delivery",
    "INTERVENTION": "Delivery change requested",
    "READY_FOR_PICKUP": "Ready for pickup at a DHL ParcelShop",
    "LEG": "Shipment registered",
    "PRE_TRANSPORT": "Picked up",
    "IN_TRANSIT": "In transit",
    "PROBLEM": "Delivery problem",
    "ARRIVED_AT_SORTING": "At sorting center",
    "OUT_FOR_DELIVERY": "Out for delivery",
    "DELIVERED": "Delivered",
    "DELIVERED_AT_NEIGHBOUR": "Delivered at neighbour",
    "DELIVERED_AT_SERVICEPOINT": "Delivered at service point",
    "NOT_HOME": "Not at home",
    "EXCEPTION": "Exception",
    "UNKNOWN": "Unknown",
}

STATUS_CATEGORIES_PL = {
    "CUSTOMS": "Odprawa celna",
    "DATA RECEIVED": "Dane odebrane",
    "DATA_RECEIVED": "Dane odebrane",
    "IN DELIVERY": "W doręczeniu",
    "UNDERWAY": "W drodze",
    "IN_DELIVERY": "W doręczeniu",
    "INTERVENTION": "Zmiana terminu lub miejsca doręczenia",
    "READY_FOR_PICKUP": "Do odbioru w punkcie DHL",
    "LEG": "Zarejestrowana",
    "PRE_TRANSPORT": "Odebrana",
    "IN_TRANSIT": "W transporcie",
    "PROBLEM": "Problem z doręczeniem",
    "ARRIVED_AT_SORTING": "W sortowni",
    "OUT_FOR_DELIVERY": "Wydana do doręczenia",
    "DELIVERED": "Doręczona",
    "DELIVERED_AT_NEIGHBOUR": "Doręczona do sąsiada",
    "DELIVERED_AT_SERVICEPOINT": "Doręczona do punktu odbioru",
    "NOT_HOME": "Nie zastano odbiorcy",
    "EXCEPTION": "Zakłócenie w doręczeniu",
    "UNKNOWN": "Nieznany",
}

STATUS_CATEGORIES_NL = {
    "CUSTOMS": "Douaneafhandeling",
    "DATA RECEIVED": "Gegevens ontvangen",
    "DATA_RECEIVED": "Gegevens ontvangen",
    "IN DELIVERY": "Onderweg voor levering",
    "UNDERWAY": "Onderweg",
    "IN_DELIVERY": "Onderweg voor levering",
    "INTERVENTION": "Wijziging van bezorging aangevraagd",
    "READY_FOR_PICKUP": "Klaar om op te halen bij een DHL ParcelShop",
    "LEG": "Zending geregistreerd",
    "PRE_TRANSPORT": "Opgehaald",
    "IN_TRANSIT": "In transport",
    "PROBLEM": "Leveringsprobleem",
    "ARRIVED_AT_SORTING": "In sorteercentrum",
    "OUT_FOR_DELIVERY": "Onderweg voor levering",
    "DELIVERED": "Bezorgd",
    "DELIVERED_AT_NEIGHBOUR": "Bezorgd bij de buren",
    "DELIVERED_AT_SERVICEPOINT": "Bezorgd bij servicepunt",
    "NOT_HOME": "Niet thuis",
    "EXCEPTION": "Uitzondering",
    "UNKNOWN": "Onbekend",
}

# Derived status: parcel was delivered to a ParcelShop and waits for pickup.
# DHL reports this with category IN_DELIVERY, which would be misleading.
STATUS_READY_FOR_PICKUP: Final = "READY_FOR_PICKUP"
PICKUP_WAITING_SUBSTATUSES: Final = frozenset(
    {
        "DELIVERED_AT_PARCELSHOP",
        "NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT",
    }
)

# Detailed DHL event statuses (substatus), verified against real DHL Parcel NL
# Track & Trace responses. Unknown codes fall back to a readable form.
SUBSTATUS_EN = {
    "PRENOTIFICATION_RECEIVED": "Shipment data received from the sender",
    "PARCEL_SORTED_AT_HUB": "Parcel sorted at the hub",
    "INFORMATION_ON_DELIVERY_TRANSMITTED": "Delivery information sent",
    "DELIVERY_PLANNED_IN_ROUTE": "Delivery planned in the courier route",
    "PARCEL_ARRIVED_AT_LOCAL_DEPOT": "Parcel arrived at the local depot",
    "PARCEL_SCANNED_INTO_HAND-TERMINAL": "Parcel scanned by the courier",
    "PARCEL_HANDED_OVER_TO_COURIER": "Parcel handed over to the courier",
    "OUT_FOR_DELIVERY": "The courier is on the way",
    "DELIVERED": "Delivered",
    "DELIVERED_IN_MAILBOX": "Delivered in the mailbox",
    "DELIVERED_AT_NEIGHBOURS": "Delivered at the neighbours",
    "DELIVERED_AT_PARCELSHOP": "Parcel is waiting at the DHL ParcelShop",
    "NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT": "Pickup notification sent",
    "COLLECTED_AT_PARCELSHOP": "Collected at the DHL ParcelShop",
    "NEW_DELIVERY_ATTEMPT": "New delivery attempt",
    "PARCEL_RETURNED_FROM_ROUTE": "Parcel returned from the route to the depot",
    "DELIVERY_DATE_CHANGED_BY_DEPOT_MANAGER": "Delivery date changed by the depot",
    "INTERVENTION_RECEIVER_REQUESTS_DELIVERY_AT_ANOTHER_TIME/DATE": "Receiver requested delivery at another time",
    "INTERVENTION_DELIVERY_SCHEDULED_AT_ANOTHER_TIME/DATE": "Delivery rescheduled",
    "INTERVENTION_RECEIVER_REQUESTS_DELIVERY_AT_PARCELSHOP": "Receiver requested delivery at a DHL ParcelShop",
    "INTERVENTION_DELIVERY_ADDRESS_CHANGED_INTO_PARCELSHOP": "Delivery address changed to a DHL ParcelShop",
    "INTERVENTION_PROCESSED_FOR_COLLECTION_AT_PARCELSHOP": "Parcel will be brought to a DHL ParcelShop",
    "INTERVENTION_RECEIVER_REQUESTS_DELIVERY_AT_SAFEPLACE": "Receiver requested delivery at a safe place",
    "MISSORT": "Parcel was missorted and is being redirected",
}

SUBSTATUS_PL = {
    "PRENOTIFICATION_RECEIVED": "Nadawca przekazał dane przesyłki",
    "PARCEL_SORTED_AT_HUB": "Paczka posortowana w centrum sortowania",
    "INFORMATION_ON_DELIVERY_TRANSMITTED": "Przekazano informacje o doręczeniu",
    "DELIVERY_PLANNED_IN_ROUTE": "Doręczenie zaplanowane na trasie kuriera",
    "PARCEL_ARRIVED_AT_LOCAL_DEPOT": "Paczka dotarła do lokalnego oddziału",
    "PARCEL_SCANNED_INTO_HAND-TERMINAL": "Kurier zeskanował paczkę",
    "PARCEL_HANDED_OVER_TO_COURIER": "Paczka przekazana kurierowi",
    "OUT_FOR_DELIVERY": "Kurier jest w drodze z paczką",
    "DELIVERED": "Doręczona",
    "DELIVERED_IN_MAILBOX": "Doręczona do skrzynki pocztowej",
    "DELIVERED_AT_NEIGHBOURS": "Doręczona do sąsiada",
    "DELIVERED_AT_PARCELSHOP": "Paczka czeka na odbiór w punkcie DHL",
    "NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT": "Wysłano powiadomienie o odbiorze w punkcie DHL",
    "COLLECTED_AT_PARCELSHOP": "Odebrana w punkcie DHL",
    "NEW_DELIVERY_ATTEMPT": "Nowa próba doręczenia",
    "PARCEL_RETURNED_FROM_ROUTE": "Paczka wróciła z trasy do oddziału",
    "DELIVERY_DATE_CHANGED_BY_DEPOT_MANAGER": "Oddział zmienił datę doręczenia",
    "INTERVENTION_RECEIVER_REQUESTS_DELIVERY_AT_ANOTHER_TIME/DATE": "Odbiorca poprosił o doręczenie w innym terminie",
    "INTERVENTION_DELIVERY_SCHEDULED_AT_ANOTHER_TIME/DATE": "Doręczenie przełożone na inny termin",
    "INTERVENTION_RECEIVER_REQUESTS_DELIVERY_AT_PARCELSHOP": "Odbiorca poprosił o doręczenie do punktu DHL",
    "INTERVENTION_DELIVERY_ADDRESS_CHANGED_INTO_PARCELSHOP": "Adres doręczenia zmieniony na punkt DHL",
    "INTERVENTION_PROCESSED_FOR_COLLECTION_AT_PARCELSHOP": "Paczka zostanie przekazana do punktu DHL",
    "INTERVENTION_RECEIVER_REQUESTS_DELIVERY_AT_SAFEPLACE": "Odbiorca poprosił o pozostawienie paczki w bezpiecznym miejscu",
    "MISSORT": "Paczka została błędnie posortowana i jest przekierowywana",
}

SUBSTATUS_NL = {
    "PRENOTIFICATION_RECEIVED": "Zendinggegevens ontvangen van de afzender",
    "PARCEL_SORTED_AT_HUB": "Pakket gesorteerd in het sorteercentrum",
    "INFORMATION_ON_DELIVERY_TRANSMITTED": "Bezorginformatie verstuurd",
    "DELIVERY_PLANNED_IN_ROUTE": "Bezorging ingepland in de route",
    "PARCEL_ARRIVED_AT_LOCAL_DEPOT": "Pakket aangekomen bij het lokale depot",
    "PARCEL_SCANNED_INTO_HAND-TERMINAL": "Pakket gescand door de bezorger",
    "PARCEL_HANDED_OVER_TO_COURIER": "Pakket overgedragen aan de bezorger",
    "OUT_FOR_DELIVERY": "De bezorger is onderweg",
    "DELIVERED": "Bezorgd",
    "DELIVERED_IN_MAILBOX": "Bezorgd in de brievenbus",
    "DELIVERED_AT_NEIGHBOURS": "Bezorgd bij de buren",
    "DELIVERED_AT_PARCELSHOP": "Pakket ligt klaar bij de DHL ParcelShop",
    "NOTIFICATION_FOR_PARCELSHOP_COLLECTION_HAS_BEEN_SENT": "Ophaalbericht verstuurd",
    "COLLECTED_AT_PARCELSHOP": "Opgehaald bij de DHL ParcelShop",
    "NEW_DELIVERY_ATTEMPT": "Nieuwe bezorgpoging",
    "PARCEL_RETURNED_FROM_ROUTE": "Pakket teruggebracht van de route naar het depot",
    "DELIVERY_DATE_CHANGED_BY_DEPOT_MANAGER": "Bezorgdatum gewijzigd door het depot",
    "INTERVENTION_RECEIVER_REQUESTS_DELIVERY_AT_ANOTHER_TIME/DATE": "Ontvanger vroeg om bezorging op een ander moment",
    "INTERVENTION_DELIVERY_SCHEDULED_AT_ANOTHER_TIME/DATE": "Bezorging verplaatst",
    "INTERVENTION_RECEIVER_REQUESTS_DELIVERY_AT_PARCELSHOP": "Ontvanger vroeg om bezorging bij een DHL ParcelShop",
    "INTERVENTION_DELIVERY_ADDRESS_CHANGED_INTO_PARCELSHOP": "Bezorgadres gewijzigd naar een DHL ParcelShop",
    "INTERVENTION_PROCESSED_FOR_COLLECTION_AT_PARCELSHOP": "Pakket wordt naar een DHL ParcelShop gebracht",
    "INTERVENTION_RECEIVER_REQUESTS_DELIVERY_AT_SAFEPLACE": "Ontvanger vroeg om bezorging op een veilige plek",
    "MISSORT": "Pakket verkeerd gesorteerd en wordt omgeleid",
}


# Dragonfly substatus codes are "DF_" + slug of the tracking page label
# (verified: 0 Data received, 105 Received, 300 Loaded, 601 Delivered - No
# Signature).
SUBSTATUS_EN.update(
    {
        "DF_DATA_RECEIVED": "Shipment data received, the parcel is still with the sender",
        "DF_RECEIVED": "Dragonfly received the parcel",
        "DF_LOADED": "The courier is on the way",
        "DF_DELIVERED_NO_SIGNATURE": "Delivered without signature",
        "DF_DELIVERED_SIGNATURE": "Delivered with signature",
        "DF_DELIVERED": "Delivered",
    }
)
SUBSTATUS_PL.update(
    {
        "DF_DATA_RECEIVED": "Nadawca zgłosił przesyłkę, paczka jest jeszcze u nadawcy",
        "DF_RECEIVED": "Dragonfly odebrał paczkę",
        "DF_LOADED": "Kurier jest w drodze z paczką",
        "DF_DELIVERED_NO_SIGNATURE": "Doręczona bez podpisu",
        "DF_DELIVERED_SIGNATURE": "Doręczona za podpisem",
        "DF_DELIVERED": "Doręczona",
    }
)
SUBSTATUS_NL.update(
    {
        "DF_DATA_RECEIVED": "Zending aangemeld, het pakket is nog bij de afzender",
        "DF_RECEIVED": "Dragonfly heeft het pakket ontvangen",
        "DF_LOADED": "De bezorger is onderweg",
        "DF_DELIVERED_NO_SIGNATURE": "Bezorgd zonder handtekening",
        "DF_DELIVERED_SIGNATURE": "Bezorgd met handtekening",
        "DF_DELIVERED": "Bezorgd",
    }
)

# PostNL substatus codes are "POSTNL_" + observation code (verified in real
# Track & Trace responses). PICKED_UP / I01 are added by the parser because
# the final scan is not part of the customer-facing observations.
SUBSTATUS_EN.update(
    {
        "POSTNL_A01": "Shipment announced, not yet received by PostNL",
        "POSTNL_A94": "Delivery changed by the receiver",
        "POSTNL_B01": "Parcel received by PostNL",
        "POSTNL_J01": "Parcel sorted",
        "POSTNL_J40": "Parcel sorted into the delivery route",
        "POSTNL_J05": "The courier is on the way",
        "POSTNL_J55": "Parcel is on its way to the PostNL point",
        "POSTNL_J12": "Parcel is waiting at the PostNL point",
        "POSTNL_I01": "Delivered",
        "POSTNL_PICKED_UP": "Collected at the PostNL point",
    }
)
SUBSTATUS_PL.update(
    {
        "POSTNL_A01": "Przesyłka zgłoszona, PostNL jeszcze jej nie otrzymał",
        "POSTNL_A94": "Odbiorca zmienił sposób doręczenia",
        "POSTNL_B01": "PostNL przyjął paczkę",
        "POSTNL_J01": "Paczka posortowana",
        "POSTNL_J40": "Paczka przydzielona do trasy kuriera",
        "POSTNL_J05": "Kurier jest w drodze z paczką",
        "POSTNL_J55": "Paczka jedzie do punktu PostNL",
        "POSTNL_J12": "Paczka czeka na odbiór w punkcie PostNL",
        "POSTNL_I01": "Doręczona",
        "POSTNL_PICKED_UP": "Odebrana w punkcie PostNL",
    }
)
SUBSTATUS_NL.update(
    {
        "POSTNL_A01": "Pakket is aangemeld, nog niet door PostNL ontvangen",
        "POSTNL_A94": "Bezorging gewijzigd door de ontvanger",
        "POSTNL_B01": "Pakket is ontvangen door PostNL",
        "POSTNL_J01": "Pakket is gesorteerd",
        "POSTNL_J40": "Pakket is gesorteerd op de bezorgrit",
        "POSTNL_J05": "De bezorger is onderweg",
        "POSTNL_J55": "Pakket is onderweg naar het PostNL-punt",
        "POSTNL_J12": "Pakket ligt klaar bij het PostNL-punt",
        "POSTNL_I01": "Bezorgd",
        "POSTNL_PICKED_UP": "Afgehaald bij het PostNL-punt",
    }
)

# Carrier prefixes of substatus codes; stripped for the readable fallback.
SUBSTATUS_PREFIXES: Final = ("DF_", "POSTNL_")


def substatus_for_lang(
    code: str | None, lang: str, fallback: str | None = None
) -> str | None:
    """Translate an event status code.

    Unknown codes use the carrier's own description (``fallback``) when
    available, otherwise a readable form of the code.
    """
    if not code:
        return fallback or None
    table = {"pl": SUBSTATUS_PL, "nl": SUBSTATUS_NL}.get(lang, SUBSTATUS_EN)
    if code in table:
        return table[code]
    if fallback:
        return fallback
    text = code
    for prefix in SUBSTATUS_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    readable = text.replace("_", " ").replace("-", " ").replace("/", " / ").lower()
    return readable[:1].upper() + readable[1:]


# Pickup status for carriers other than DHL (no "DHL ParcelShop").
READY_FOR_PICKUP_GENERIC: Final = {
    "en": "Ready for pickup at a pickup point",
    "pl": "Do odbioru w punkcie odbioru",
    "nl": "Klaar om op te halen bij een afhaalpunt",
}


def status_for_lang(status: str | None, lang: str, carrier: str | None = None) -> str:
    """Map a shared status category to the configured language."""
    code = status or "UNKNOWN"
    if code == STATUS_READY_FOR_PICKUP and carrier not in (None, "dhl"):
        return READY_FOR_PICKUP_GENERIC.get(lang, READY_FOR_PICKUP_GENERIC["en"])
    if lang == "pl":
        return STATUS_CATEGORIES_PL.get(code, code)
    if lang == "nl":
        return STATUS_CATEGORIES_NL.get(code, code)
    return STATUS_CATEGORIES.get(code, code)


def status_texts(status: str | None, carrier: str | None = None) -> dict[str, str]:
    """Status text in all supported languages."""
    return {lang: status_for_lang(status, lang, carrier) for lang in ("en", "pl", "nl")}


# Repair issue translation keys
ISSUE_POSTNL_POSTAL_CODE: Final = "postnl_postal_code_missing"
ISSUE_AMAZON_AUTH: Final = "amazon_auth_required"
ISSUE_AMAZON_UNAVAILABLE: Final = "amazon_unavailable"

# Options
CONF_ENABLED_CARRIERS: Final = "enabled_carriers"
CONF_POSTNL_TOKEN_ENCRYPTED: Final = "postnl_token_encrypted"
CONF_AMAZON_ENABLED: Final = "amazon_enabled"
CONF_AMAZON_EMAIL_ENCRYPTED: Final = "amazon_email_encrypted"
CONF_AMAZON_PASSWORD_ENCRYPTED: Final = "amazon_password_encrypted"
CONF_AMAZON_OTP_ENCRYPTED: Final = "amazon_otp_secret_encrypted"
CONF_AMAZON_POLL_MINUTES: Final = "amazon_poll_minutes"
CONF_AMAZON_LOGIN_METHOD: Final = "amazon_login_method"
CONF_AMAZON_TOKEN_ENCRYPTED: Final = "amazon_token_encrypted"
AMAZON_LOGIN_BROWSER: Final = "browser"
AMAZON_LOGIN_BROWSER_PASTE: Final = "browser_paste"
AMAZON_LOGIN_PASSWORD: Final = "password"
AMAZON_LOGIN_COOKIES: Final = "cookies"
CONF_AMAZON_COOKIES_ENCRYPTED: Final = "amazon_cookies_encrypted"
CONF_AMAZON_STORES: Final = "amazon_stores"
DEFAULT_AMAZON_POLL_MINUTES: Final = 45
ACCOUNT_TYPE_NONE: Final = "none"

SERVICE_TRACK_PARCEL: Final = "track_parcel"
SERVICE_UNTRACK_PARCEL: Final = "untrack_parcel"


EVENT_DHL_PARCEL_STATUS_CHANGED = "dhl_parcel_status_changed"
EVENT_DHL_PARCEL_DISCOVERED = "dhl_parcel_discovered"
EVENT_DHL_PARCEL_UPDATED = "dhl_parcel_updated"
EVENT_DHL_PARCEL_REMOVED = "dhl_parcel_removed"
EVENT_DHL_PARCEL_DELIVERY_WINDOW_CHANGED = "dhl_parcel_delivery_window_changed"
EVENT_DHL_PARCEL_SUBSTATUS_CHANGED = "dhl_parcel_substatus_changed"

SERVICE_DHL_PARCEL_REFRESH = "dhl_parcel_refresh"
