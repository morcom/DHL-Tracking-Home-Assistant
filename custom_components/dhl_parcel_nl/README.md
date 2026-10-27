# Parcel Tracking NL - DHL, PostNL, Dragonfly (Home Assistant custom integration)

Author: Michal Borkowski (ma@borkowski.nl)

License: GNU GPL v3 or later (see `LICENSE`)

This integration tracks DHL, PostNL and Dragonfly parcels (optionally discovered
from DHL/PostNL accounts and Amazon.nl orders), creates dynamic parcel sensors,
emits automation events, and exposes voice-assistant friendly summary entities.
Version 2.0.0 requires Home Assistant 2026.10; see `CHANGELOG.md` in the repository.

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

### 3) No DHL account (`account_type: none`)

For PostNL/Dragonfly-only setups or manual DHL codes. No credentials needed.

Do not fill all fields. Choose one account type and provide only that credential pair.

## Business API requirements (DHL)

For business mode, use **both** DHL systems correctly:

- `developer.dhl.com` -> API catalog, product onboarding, access status
- `my.dhlecommerce.nl` (My DHL Portal) -> generate the actual `userId` + `key`

For DHL Parcel NL API auth used by this integration, credentials are created in My DHL Portal:

1. Log in to `https://my.dhlecommerce.nl` with your business account.
2. Go to `Settings -> API Keys`.
3. Create an API key and copy:
   - `userId`
   - `key`
4. Use those values in integration config (`user_id`, `api_key`).

If you do not see the API Keys page, your account likely misses API permissions/role.
Ask DHL support/account manager to enable API Gateway access for your business account.

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

## Carriers and sources

| Carrier | Tracking | Account sync | Entity id / unique id |
|---|---|---|---|
| DHL | DHL Parcel NL Track & Trace | consumer or business account | `sensor.dhl_parcel_<code>` / `<entry_id>_<code>` (unchanged) |
| PostNL | public Track & Trace (needs the receiver's postal code) | PostNL login (optional) | `sensor.postnl_parcel_<code>` / `<entry_id>_postnl_<code>` |
| Dragonfly | public tracking page API (`AMZNL...` codes) | - | `sensor.dragonfly_parcel_<code>` / `<entry_id>_dragonfly_<code>` |

Carriers are enabled in **Options -> General and carriers**. Parcels are added by:
- account sync (DHL, PostNL) - `source: dhl_account` / `postnl_account`
- Amazon orders (experimental) - `source: amazon`, with `order_id` and `store`
- the manual codes option or the `track_parcel` action - `source: manual`

Manual codes are comma separated. Use `carrier:code` (`dhl:`, `postnl:`, `dragonfly:`) or a
bare code; the carrier is then detected from the code (`AMZNL` + 12 digits -> Dragonfly,
`JVGL`/`JJD`/`JD` -> DHL, `KG...` / `..123456789NL` -> PostNL, anything else -> DHL).
`3S...` codes are used by DHL and PostNL. The carrier reported by the source (account,
Amazon) wins; for a bare manual `3S` code DHL is asked first and, if DHL does not know it,
PostNL (needs the postal code). The carrier that answers is remembered.

The unofficial Dragonfly and PostNL endpoints may change. A carrier failure never stops
the other carriers: the parcel keeps its last data, HTTP 429/5xx skips it for one cycle, and
a code the carrier does not know yet is shown with status `UNKNOWN`.

### PostNL

- Public tracking needs the receiver's postal code. It comes from the PostNL account or
  the Amazon order, otherwise from the `postal_code` option. Without one, a repair issue
  "PostNL needs your postal code" is shown and the parcel is skipped.
- Account sync (optional, **Options -> PostNL account**) uses the login of the PostNL app
  (OAuth2 with PKCE, the same unofficial app client as `arjenbos/ha-postnl`; PostNL could
  revoke it):
  1. Open the link shown in the dialog and log in to PostNL.
  2. The browser then tries to open `postnl://login?code=...` and may show an error. Copy
     that complete address (address bar, or developer tools -> Network) and paste it.
  3. The refresh token is stored obfuscated in the config entry and renewed automatically.
     When it expires, Home Assistant asks you to repeat the login (reauthentication).
- The first account sync only records existing parcels (no `dhl_parcel_discovered`).

### Amazon orders (experimental, off by default)

**Warning:** the Amazon Conditions of Use forbid automated access. Amazon may ask for
captchas or restrict your account. Use at your own risk.

Set up in **Options -> Amazon orders**. Select every store you order from
(amazon.nl, amazon.de, amazon.com.be, amazon.fr, amazon.es, amazon.it, amazon.co.uk,
amazon.com). Orders exist only in the store they were placed in, so each store is read
separately; a store that fails is skipped and the others continue.

Login methods:

- **Log in on Amazon through Home Assistant (recommended, nothing to copy)** - works like
  Alexa Media Player:
  1. A new window opens with the real Amazon sign-in page, served through Home Assistant
     (`/api/dhl_parcel_nl/amazon_login/<flow id>`). Log in as usual; captchas and
     verification codes appear there and are solved by you. For all European stores the
     sign-in page is amazon.de (amazon.nl has none of its own); it is the same account.
  2. When the login is complete, the window closes and the dialog continues by itself.
  3. The integration registers a device "Parcel Tracking NL" in your Amazon account and keeps
     only the refresh token, obfuscated. No password is stored. Before every poll the token
     is exchanged for fresh cookies of each selected store, which keeps the session alive.
     Removing the device under "Your devices" revokes the login.
  4. The dialog checks right away that the stores accept the login for the order history.
     If anything fails, it falls back to the paste login below.
  The login page is reachable without a Home Assistant login (the new window has no HA
  token), but only for the random id of a running dialog, for at most 15 minutes and only
  until the login finished. Your password passes through Home Assistant's memory only; it is
  never stored or logged. The proxy is vendored from `authcaptureproxy` 1.3.7 (Apache-2.0,
  see `sources/authproxy/NOTICE.md`). Allow pop-ups for Home Assistant if the window does
  not open. European stores and amazon.com cannot be combined in one browser login.
- **Log in on Amazon, paste the address (fallback)** - the same login in your own browser:
  open the link, log in, then copy the address of the "page not found" page
  (`https://www.amazon.de/ap/maplanding?...`) and paste it within a few minutes.
- **Cookies from your browser** - log in on each store in your browser, open your orders,
  copy the `Cookie` request header from the developer tools (F12 -> Network) and paste it,
  one line per store. The store is recognized by its sign-in cookie (`x-acbnl`, `x-acbde`,
  `x-acbfr`, `x-acbes`, ...). When Amazon ends a session, Home Assistant asks for new cookies.
- **Email and password** - uses the `amazon-orders` login (one login per store) with an
  optional TOTP secret. It stops when Amazon asks for a captcha.

All methods:
- The `amazon-orders` library (5.2.0) is installed only when this source is enabled.
  It needs `beautifulsoup4>=4.14`, while the core `scrape` integration pins 4.13.3; do not
  enable Amazon if you use `scrape`.
- Read-only: orders of the last 14 days are read every 30-60 minutes (configurable), only
  inside the refresh window. The first poll after setup, a reload or a restart always runs.
- Shipments delivered by DHL, PostNL or Dragonfly ("Delivery By DHL_CONNECT",
  "Delivery By DRAGONFLY") are added with `source: amazon`, `order_id`, `store` and
  `sender: Amazon` and fire `dhl_parcel_discovered` (except on the very first Amazon sync).
  Already delivered shipments are added too, without an event, and disappear after the
  delivered retention days. Other carriers are ignored. Orders without a tracking number yet
  are remembered (no sensor) and checked again on each poll for up to 14 days.
- The diagnostic sensor `Amazon Orders` shows the result of the last poll (`ok`, `waiting`,
  `auth_required`, `unavailable`, `error`) with the number of orders (in total and per store),
  shipments, shipments with a tracking number, added parcels, orders waiting for a tracking
  number and ignored carriers. The same summary is written to the log on every poll.
- **No captcha solving.** When the login expires or Amazon needs the user, Amazon polling
  stops, a repair issue is shown and Home Assistant asks you to log in again. Carrier
  tracking continues.
- The unofficial Amazon endpoints (`/auth/register`, `/ap/exchangetoken/cookies`, the order
  pages) may change at any time.

### Actions

```yaml
action: dhl_parcel_nl.track_parcel
data:
  tracking_code: AMZNL000000000001
  carrier: auto        # auto | dhl | postnl | dragonfly
  postal_code: 1234AB  # optional, PostNL only
```

`dhl_parcel_nl.untrack_parcel` (`tracking_code`) stops tracking a parcel and fires
`dhl_parcel_removed` with `reason: untracked`; account sync and Amazon will not add it again.
`dhl_parcel_nl.dhl_parcel_refresh` refreshes all carriers (and Amazon orders when enabled).

## Supported data fields

Per parcel sensor (`sensor.dhl_parcel_<tracking>`, `sensor.postnl_parcel_<tracking>`,
`sensor.dragonfly_parcel_<tracking>`):
- tracking code / barcode
- `carrier` (`dhl`, `postnl`, `dragonfly`), `carrier_name`, `source`
  (`dhl_account`, `postnl_account`, `amazon`, `manual`) and `order_id` (Amazon)
- status and raw status category
- sender + shipper name
- delivery date and timeframe
- delivery_time_from / delivery_time_to (ISO) and `delivery_time_from_hhmm` / `delivery_time_to_hhmm` (HA time zone)
- delivery_day_label in `summary_language` (e.g. `today` / `dzisiaj` / `vandaag`, or date)
- delivered_at timestamp, `delivered_at_hhmm`, `delivered_day_label`
- status in all languages (`status`, `status_en`, `status_pl`, `status_nl`)
- detailed step `last_event_status` (DHL e.g. `PARCEL_ARRIVED_AT_LOCAL_DEPOT`, Dragonfly
  `DF_LOADED`, PostNL `POSTNL_<observation code>`) and its translation
  `last_event_status_localized` (e.g. "Paczka dotarła do lokalnego oddziału"; unknown
  PostNL codes use PostNL's own description)
- `tracking_url` of the carrier's tracking page
- event history and latest event
- delivery_location (when available from DHL events)
- `last_updated` - time of the last real change of the parcel data

All `*_hhmm` values and day labels are calculated in the Home Assistant time zone.
Day labels are refreshed shortly after midnight even when no poll happens.

Summary sensors:
- `sensor.dhl_parcel_count` - number of **active** (not delivered) parcels; attributes
  `active_count`, `delivered_count`, `total_count` and a `parcels` list
- `sensor.dhl_tracking_details`
- `sensor.dhl_delivered_details`
- `sensor.dhl_parcel_voice_summary`
- `sensor.dhl_parcel_delivered_summary`

Voice summary sensor provides structured `parcels` list (active parcels only) for assistants:
- sender
- status (`status`) and translated labels (`status_en`, `status_pl`, `status_nl`, `status_localized`)
- time window
- tracking code and the real entity_id of the parcel sensor

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

Delivered parcels are summarized by `sensor.dhl_parcel_delivered_summary`.

`sensor.dhl_tracking_details` includes a per-tracking matrix for assistants:
- `dhl_tracking_numbers` (comma-separated `tracking|sender`)
- `dhl_tracking_numbers_extended` (comma-separated `tracking|sender|status|day|window`)
- `tracking_details` (list of dicts per tracking code)
- `tracking_by_number` (object keyed by tracking code)

`sensor.dhl_delivered_details` includes a delivered-only matrix:
- `dhl_delivered_numbers` (comma-separated `tracking|sender|delivered_day|delivered_time`)
- `dhl_delivered_numbers_extended` (comma-separated `tracking|sender|status|delivered_day|delivered_time|delivery_location`)
- `delivered_tracking_numbers`
- `delivered_details` (list sorted newest first)
- `delivered_by_number` (object keyed by tracking code)

Status mapping covers DHL Track & Trace categories such as:
- `DATA_RECEIVED`
- `UNDERWAY`
- `IN_DELIVERY`
- `DELIVERED`
- `CUSTOMS`
- `INTERVENTION` (the receiver or DHL changed the delivery time/place)
- `EXCEPTION`
- `PROBLEM`
- `UNKNOWN`

Additional status set by the integration:
- `READY_FOR_PICKUP` - the parcel waits at a DHL ParcelShop (DHL itself still reports
  `IN_DELIVERY` here) or at a PostNL/Dragonfly pickup point. `delivery_location` then
  contains the pickup point address.

PostNL and Dragonfly statuses are mapped to the same categories, so automations work
for every carrier. Voice summaries name the carrier once ("Masz 2 paczki DHL.") or, with
parcels from several carriers, per parcel ("Paczka 1 Dragonfly od Amazon ...").

Postal code: DHL Track & Trace returns the sender and addresses only when the receiver's
postal code is part of the request. For consumer accounts it is taken automatically from
the account parcel list; for manual tracking codes set `postal_code` in the options.

## Refresh and quiet hours

Configurable in integration options:
- `refresh_interval_minutes` (default 5)
- `refresh_start_time` (default `06:00`)
- `refresh_end_time` (default `23:00`)

Outside refresh window, polling is skipped. The first refresh after a Home Assistant
start or integration reload always runs, so sensors are never empty after a restart.
Times are evaluated in the Home Assistant time zone.

## Events for automations

- `dhl_parcel_discovered`
- `dhl_parcel_status_changed`
- `dhl_parcel_substatus_changed`
- `dhl_parcel_delivery_window_changed`
- `dhl_parcel_updated`
- `dhl_parcel_removed`

Each bus event is mirrored by an event entity (`event.dhl_parcel_netherlands_dhl_parcel_*`,
except `dhl_parcel_updated`). The event data is stored in the entity attributes.
Event names keep the `dhl_` prefix for compatibility but are fired for all carriers;
every event carries `carrier`. `dhl_parcel_discovered` also carries `source`
(`account_sync`, `amazon`, `manual`) and `order_id` (Amazon). The device is now
called "Parcel Tracking NL"; the event entity ids are unchanged.

Event delivery guarantees:
- events are fired **after** all sensors have been updated, so automations can read
  `sensor.dhl_delivered_details` etc. and get the current data
- change-detection state is persisted (`.storage/dhl_parcel_nl.<entry_id>_state`), so a
  restart or reload does not produce repeated "discovered"/"delivery window" events and a
  delivery that happened during a restart is still reported as `dhl_parcel_status_changed`
- the first observation of a parcel only records its state (no `delivery_window_changed`)
- on the very first sync of each account (DHL, PostNL) and of Amazon, parcels already
  there do not trigger `dhl_parcel_discovered`
- events found during Home Assistant startup are fired once HA has started
- the `data` field contains the parsed parcel data without the raw DHL response and with
  only the last 5 tracking events (keeps the recorder and entity attributes small)

Event payload localization:
- each major event includes `language` and `status_localized`
- status change events include `old_status_localized` and `new_status_localized`
- substatus events include `old_substatus_localized` / `new_substatus_localized`, and
  status change events include `substatus` / `substatus_localized`
- substatus events include `status_changed: true` when the same update also fired
  `dhl_parcel_status_changed` (use it to avoid double announcements)
- this uses integration option `summary_language` (`en`, `pl`, `nl`)

Delivered-event fields:
- `delivered_at`
- `delivery_location` (human-readable place/address when available; GPS only as fallback)

Reverse-geocoding for delivery location:
- preferred free source: OpenStreetMap Nominatim
- fallback free source: BigDataCloud reverse-geocode-client
- result is cached in memory and used to replace raw GPS in summaries/events when possible

Entity cleanup behavior:
- parcels removed from DHL account are automatically removed from active tracking
- delivered parcels remain visible until `delivered_keep_days` expires
- stale/unavailable parcel entities are removed from registry automatically

## Business automation examples

### A) Status changed (business account)

Use the event entity trigger (HA 2026). `target` and `options` are required;
the event data is available in `trigger.to_state.attributes`:

```yaml
alias: DHL Business - Status Changed
triggers:
  - trigger: event.received
    target:
      entity_id: event.dhl_parcel_netherlands_dhl_parcel_status_changed
    options:
      event_type: dhl_parcel_status_changed
actions:
  - variables:
      sender: "{{ trigger.to_state.attributes.get('sender') or 'unknown sender' }}"
      tracking: "{{ trigger.to_state.attributes.get('tracking_code', 'unknown') }}"
      new_status: "{{ trigger.to_state.attributes.get('new_status_localized') or trigger.to_state.attributes.get('new_status', 'unknown') }}"
  - action: notify.notify
    data:
      message: "Business parcel {{ tracking }} from {{ sender }} changed to {{ new_status }}."
```

Version-independent alternative with the classic event trigger (data in `trigger.event.data`):

```yaml
triggers:
  - trigger: event
    event_type: dhl_parcel_status_changed
actions:
  - action: notify.notify
    data:
      message: "Parcel {{ trigger.event.data.tracking_code }} changed to {{ trigger.event.data.new_status_localized }}."
```

### B) Delivery window announced (business account)

```yaml
alias: DHL Business - Delivery Window
triggers:
  - trigger: event.received
    target:
      entity_id: event.dhl_parcel_netherlands_dhl_parcel_delivery_window_changed
    options:
      event_type: dhl_parcel_delivery_window_changed
actions:
  - variables:
      tf_raw: "{{ trigger.to_state.attributes.get('new_delivery_timeframe') }}"
      tf_from: "{{ tf_raw.split('/')[0] if tf_raw is string and '/' in tf_raw else '' }}"
      tf_to: "{{ tf_raw.split('/')[1] if tf_raw is string and '/' in tf_raw else '' }}"
  - action: notify.notify
    data:
      message: >
        {% set f = as_datetime(tf_from, None) %}{% set t = as_datetime(tf_to, None) %}
        Delivery window: {{ (f | as_local).strftime('%H:%M') if f else 'unknown' }} -
        {{ (t | as_local).strftime('%H:%M') if t else 'unknown' }}
```

## Voice assistant usage

Expose at least these entities to Assist/LLM:
- `sensor.dhl_parcel_voice_summary`
- `sensor.dhl_parcel_count`
- `sensor.dhl_tracking_details`
- `sensor.dhl_parcel_delivered_summary`

Optional:
- selected dynamic parcel sensors (`sensor.dhl_parcel_<tracking>`)

For Polish voice assistants, `sensor.dhl_parcel_voice_summary` includes `voice_summary_pl`.
For Dutch voice assistants, `sensor.dhl_parcel_voice_summary` includes `voice_summary_nl`.

## Example files (up-to-date)

Automation examples:
- `automation-examples-pl.yaml`
- `automation-examples-en.yaml`
- `automation-examples-nl.yaml`

Lovelace card examples:
- `lovelace-card-example-pl.yaml`
- `lovelace-card-example-en.yaml`
- `lovelace-card-example-nl.yaml`

Default aliases/legacy files:
- `automation-examples.yaml` (Polish default)
- `lovelace-card-example.yaml` (Polish default)

## Notes

- Consumer endpoints are unofficial/private and may change.
- Business endpoints are documented and should be preferred for long-term stability.

## Update notes (1.7.4 -> 1.7.8)

- `1.7.5`
  - Consumer parcel sync aligned with My DHL web app request shape.
  - Added `tab=incoming` on consumer parcels endpoint.
  - Added consumer `x-xsrf-token` header support from session cookies.
  - Added one-time consumer re-auth + retry on HTTP 401 for `/receiver-parcel-api/parcels`.
  - Consumer authentication test no longer requires bearer token presence.

- `1.7.6`
  - Unload flow hardened to tolerate partial/failed platform load state during reload.

- `1.7.7`
  - Reload listener changed to Home Assistant managed reload (`hass.config_entries.async_reload(...)`) to avoid manual unload/setup race conditions.

- `1.7.8`
  - Added `services.yaml` metadata for `dhl_parcel_refresh` to remove HA warning: `Failed to load services.yaml for integration`.
