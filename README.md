# DHL Parcel Netherlands (Home Assistant custom integration)

Author: Michal Borkowski (ma@borkowski.nl)

License: GNU GPL v3 or later (see `LICENSE`)

This integration tracks DHL parcels for consumer and business accounts,
creates dynamic parcel sensors, emits automation events, and exposes
voice-assistant friendly summary entities.

## Account modes

### 1) Consumer account (`my.dhlecommerce.nl` / My DHL app)

Required:
- `email`
- `password`

Not required:
- `user_id`
- `api_key`

### 2) Business account (DHL API Gateway)

Required:
- `user_id`
- `api_key`

Not required:
- `email`
- `password`

Do not fill all fields. Choose one account type and provide only that credential pair.

## Business API requirements (DHL)

For business mode, enable/create credentials in My DHL Portal:

1. Log in to `https://my.dhlecommerce.nl` with your business account.
2. Go to `Settings -> API Keys`.
3. Create an API key and copy:
   - `userId`
   - `key`
4. Use those values in integration config (`user_id`, `api_key`).

Relevant DHL docs:
- Developer portal (entry point): `https://developer.dhl.com/`
- Authentication: `https://api-gw.dhlparcel.nl/docs/guide/chapters/1-authentication-and-authorization.html`
- Track & Trace: `https://api-gw.dhlparcel.nl/docs/guide/chapters/05-track-and-trace.html`
- API overview: `https://api-gw.dhlparcel.nl/docs/`

Important distinction:
- `developer.dhl.com` is DHL's global API portal and catalog.
- This integration uses DHL Parcel NL / My DHL Portal APIs (`api-gw.dhlparcel.nl`), which are the relevant endpoints for NL business accounts.

### Which APIs must be enabled for this integration

For this add-on in **business mode**, you need access to DHL Parcel NL APIs via My DHL Portal credentials:

Mandatory for this integration:
- `Authentication and Authorization` (`/authenticate/api-key`, `/authenticate/refresh-token`)
- `Track and Trace` (`/track-trace`)
- `Shipments` (`/shipments`) for automatic parcel discovery

How to enable (business user):
1. Log in to `https://my.dhlecommerce.nl`.
2. Go to `Settings -> API Keys`.
3. Create API key and copy `userId` + `key`.
4. Ensure your business account has shipment/tracking access for your organization.

Optional, not used directly by this integration:
- `Track and Trace Pusher` (webhook push model): configured in `Settings -> Integrations`.
- `Shipment Tracking - Unified` on `developer.dhl.com` (`api-eu.dhl.com/track/shipments`).

Important:
- This integration does **not** call the Unified Tracking API endpoint (`api-eu.dhl.com/track/shipments`).
- If you only enabled Unified Tracking on `developer.dhl.com` but did not create My DHL Portal API keys, business mode in this integration will not authenticate.

### What must be provided in HA for business mode

In integration configuration:
- `account_type = business`
- `user_id` (from My DHL Portal API Keys)
- `api_key` (from My DHL Portal API Keys)

Optional fields:
- `postal_code` (can improve details for some track-trace responses)
- `refresh_interval_minutes`
- `refresh_start_time` and `refresh_end_time`
- `delivered_keep_days`
- `summary_language` (`en`, `pl`, `nl`) for voice summary output

Notes:
- Integration uses `authenticate/api-key` and `authenticate/refresh-token` for business token lifecycle.
- Parcel details are read from Track & Trace (`/track-trace`) and account shipment listing (`/shipments`).

## Consumer credential storage (at-rest obfuscation)

For consumer mode, email/password can be stored obfuscated in config entry data
(`email_encrypted` / `password_encrypted`). On startup, the integration decrypts
them in memory before API login.

Important:
- This is local at-rest obfuscation (defense-in-depth), not enterprise-grade key management.
- Runtime values still exist in memory while integration is active.
- For maximum security, prefer business mode with API keys and secure your HA host/backups.

## Supported data fields

Per parcel sensor (`sensor.dhl_parcel_<tracking>`):
- tracking code / barcode
- status and raw status category
- sender + shipper name
- delivery date and timeframe
- delivery_time_from / delivery_time_to
- delivery_day_label (`today`, `tomorrow`, `in two days`, or date)
- delivered_at timestamp
- event history and latest event

Summary sensors:
- `sensor.dhl_parcel_count`
- `sensor.dhl_tracking_details`
- `sensor.dhl_parcel_voice_summary`

Voice summary sensor provides structured `parcels` list for assistants:
- sender
- status (`status`) and translated labels (`status_en`, `status_pl`)
- time window
- delivered time
- tracking code and mapped entity_id

`sensor.dhl_parcel_voice_summary` includes:
- `voice_summary` (localized by `summary_language`)
- `voice_summary_en`
- `voice_summary_pl`
- `voice_summary_nl`

The summary text includes per-package details:
- package index
- sender/shipper name
- current status
- expected delivery day + time window when available
- delivered time when already delivered

`sensor.dhl_tracking_details` includes a per-tracking matrix for assistants:
- `dhl_tracking_numbers` (comma-separated `tracking|sender`)
- `dhl_tracking_numbers_extended` (comma-separated `tracking|sender|status|day|window`)
- `tracking_details` (list of dicts per tracking code)
- `tracking_by_number` (object keyed by tracking code)

Status mapping covers DHL Track & Trace categories such as:
- `DATA_RECEIVED`
- `UNDERWAY`
- `IN_DELIVERY`
- `DELIVERED`
- `CUSTOMS`
- `INTERVENTION`
- `PROBLEM`
- `UNKNOWN`

## Refresh and quiet hours

Configurable in integration options:
- `refresh_interval_minutes` (default 5)
- `refresh_start_time` (default `06:00`)
- `refresh_end_time` (default `23:00`)

Outside refresh window, polling is skipped.

## Events for automations

- `dhl_parcel_discovered`
- `dhl_parcel_status_changed`
- `dhl_parcel_substatus_changed`
- `dhl_parcel_delivery_window_changed`
- `dhl_parcel_updated`
- `dhl_parcel_removed`

Event payload localization:
- each major event includes `language` and `status_localized`
- status change events include `old_status_localized` and `new_status_localized`
- this uses integration option `summary_language` (`en`, `pl`, `nl`)

## Business automation examples

### A) Status changed (business account)

Use event entity trigger in HA 2026:

```yaml
alias: DHL Business - Status Changed
triggers:
  - trigger: event.received
    entity_id: event.dhl_parcel_netherlands_dhl_parcel_status_changed
    event_type: dhl_parcel_status_changed
actions:
  - variables:
      attrs: "{{ trigger.to_state.attributes if trigger is defined and trigger.to_state is defined and trigger.to_state else {} }}"
      sender: "{{ attrs.get('sender') or attrs.get('data', {}).get('shipper_name') or 'unknown sender' }}"
      tracking: "{{ attrs.get('tracking_code', 'unknown') }}"
      new_status: "{{ attrs.get('new_status', 'unknown') }}"
  - action: notify.notify
    data:
      message: "Business parcel {{ tracking }} from {{ sender }} changed to {{ new_status }}."
```

### B) Delivery window announced (business account)

```yaml
alias: DHL Business - Delivery Window
triggers:
  - trigger: event.received
    entity_id: event.dhl_parcel_netherlands_dhl_parcel_delivery_window_changed
    event_type: dhl_parcel_delivery_window_changed
actions:
  - variables:
      attrs: "{{ trigger.to_state.attributes if trigger is defined and trigger.to_state is defined and trigger.to_state else {} }}"
      tf_raw: "{{ attrs.get('new_delivery_timeframe') or attrs.get('data', {}).get('delivery_timeframe') }}"
      tf_from: "{{ tf_raw.split('/')[0] if tf_raw is string and '/' in tf_raw else None }}"
      tf_to: "{{ tf_raw.split('/')[1] if tf_raw is string and '/' in tf_raw else None }}"
  - action: notify.notify
    data:
      message: >
        Delivery window: {{ as_datetime(tf_from).strftime('%H:%M') if tf_from else 'unknown' }} -
        {{ as_datetime(tf_to).strftime('%H:%M') if tf_to else 'unknown' }}
```

## Voice assistant usage

Expose at least these entities to Assist/LLM:
- `sensor.dhl_parcel_voice_summary`
- `sensor.dhl_parcel_count`

Optional:
- selected dynamic parcel sensors (`sensor.dhl_parcel_<tracking>`)

For Polish voice assistants, `sensor.dhl_parcel_voice_summary` includes `voice_summary_pl`.
For Dutch voice assistants, `sensor.dhl_parcel_voice_summary` includes `voice_summary_nl`.

## Notes

- Consumer endpoints are unofficial/private and may change.
- Business endpoints are documented and should be preferred for long-term stability.
