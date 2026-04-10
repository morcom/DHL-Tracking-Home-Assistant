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

STATUS_CATEGORIES = {
    "CUSTOMS": "Customs processing",
    "DATA RECEIVED": "Data received",
    "DATA_RECEIVED": "Data received",
    "IN DELIVERY": "Out for delivery",
    "UNDERWAY": "Underway",
    "IN_DELIVERY": "Out for delivery",
    "INTERVENTION": "Intervention needed",
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
    "CUSTOMS": "Customs processing",
    "EXCEPTION": "Exception",
    "UNKNOWN": "Unknown",
}

STATUS_CATEGORIES_PL = {
    "CUSTOMS": "Odprawa celna",
    "DATA RECEIVED": "Dane odebrane",
    "DATA_RECEIVED": "Dane odebrane",
    "IN DELIVERY": "W doreczeniu",
    "UNDERWAY": "W drodze",
    "IN_DELIVERY": "W doreczeniu",
    "INTERVENTION": "Wymagana interwencja",
    "LEG": "Zarejestrowana",
    "PRE_TRANSPORT": "Odebrana",
    "IN_TRANSIT": "W transporcie",
    "PROBLEM": "Problem z doreczeniem",
    "ARRIVED_AT_SORTING": "W sortowni",
    "OUT_FOR_DELIVERY": "Do doreczenia",
    "DELIVERED": "Doreczona",
    "DELIVERED_AT_NEIGHBOUR": "Doreczona do sasiada",
    "DELIVERED_AT_SERVICEPOINT": "Doreczona do punktu",
    "NOT_HOME": "Nie zastano odbiorcy",
    "EXCEPTION": "Wyjatek",
    "UNKNOWN": "Nieznany",
}

STATUS_CATEGORIES_NL = {
    "CUSTOMS": "Douaneafhandeling",
    "DATA RECEIVED": "Gegevens ontvangen",
    "DATA_RECEIVED": "Gegevens ontvangen",
    "IN DELIVERY": "Onderweg voor levering",
    "UNDERWAY": "Onderweg",
    "IN_DELIVERY": "Onderweg voor levering",
    "INTERVENTION": "Interventie nodig",
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

EVENT_DHL_PARCEL_STATUS_CHANGED = "dhl_parcel_status_changed"
EVENT_DHL_PARCEL_DISCOVERED = "dhl_parcel_discovered"
EVENT_DHL_PARCEL_UPDATED = "dhl_parcel_updated"
EVENT_DHL_PARCEL_REMOVED = "dhl_parcel_removed"
EVENT_DHL_PARCEL_DELIVERY_WINDOW_CHANGED = "dhl_parcel_delivery_window_changed"
EVENT_DHL_PARCEL_SUBSTATUS_CHANGED = "dhl_parcel_substatus_changed"

SERVICE_DHL_PARCEL_REFRESH = "dhl_parcel_refresh"
