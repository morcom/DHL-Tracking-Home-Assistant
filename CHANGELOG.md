# Changelog

## 2.0.0

Requires Home Assistant 2026.10 or newer. New dependency: `http` (always loaded by default).

### New
- **Multi-carrier:** the integration now tracks DHL, **PostNL** and **Dragonfly** parcels
  (display name "Parcel Tracking NL (DHL, PostNL, Dragonfly)"). Carriers are enabled in
  the options. The domain `dhl_parcel_nl` is unchanged.
- **PostNL:** public Track & Trace (receiver postal code required; a repair issue is shown
  when it is missing) and optional account sync through the PostNL app login (paste flow,
  OAuth2 with PKCE, automatic token refresh, reauthentication when the login expires).
- **Dragonfly:** tracking through the public tracking page API. Statuses, the ETA window,
  the delivered time and pickup points are mapped; unknown codes are shown as `UNKNOWN`.
- **Amazon orders (experimental, off by default):** new DHL/PostNL/Dragonfly shipments
  from the last 14 days of every selected store (amazon.nl, .de, .com.be, .fr, .es, .it,
  .co.uk, .com) are added automatically with `source: amazon`, `order_id` and `store`.
  Logins: Amazon login through Home Assistant like Alexa Media Player (nothing to copy; only
  a refresh token is kept, no password), the same login with a pasted address, cookies pasted
  from your browser, or email and password. The login proxy is vendored from
  `authcaptureproxy` 1.3.7 (Apache-2.0) because the package would downgrade `multidict`. No captcha solving: when Amazon
  needs the user, polling stops and a repair issue plus a reauthentication are created.
  Diagnostic sensor `Amazon Orders` with the result of the last poll. The `amazon-orders`
  library is installed only when this source is enabled.
- Actions `track_parcel` (auto carrier detection, optional postal code) and `untrack_parcel`.
- Manual codes accept `carrier:code`.
- Attributes and events carry `carrier`; parcel sensors also carry `carrier_name`, `source`,
  `order_id` and the carrier's `tracking_url`. `dhl_parcel_discovered` has `source` and `order_id`.
- Voice summaries name the carrier, per parcel when several carriers are involved.
- DHL account is optional (`account_type: none`).

### Changed (breaking)
- Status texts were reworded (`status`, `status_en/pl/nl`, voice summaries).
- `sensor.dhl_parcel_count` counts active (not delivered) parcels only.
- The options dialog is a menu: General and carriers, DHL account, PostNL account,
  Amazon.nl orders.
- The device is named "Parcel Tracking NL"; event entity ids are unchanged.

### Compatibility
- Existing entity ids, DHL unique ids (`<entry_id>_<code>`), event names and automations
  keep working. New carriers use `sensor.<carrier>_parcel_<code>` and
  `<entry_id>_<carrier>_<code>`.
- Stored state (`.storage/dhl_parcel_nl.<entry_id>_state`) is migrated from version 1 to 2
  automatically; existing parcels are not reported again and no events are fired.

### Fixed (since 1.7.9)
- Event entities publish their state, so `event.received` triggers fire.
- Events are fired after the sensors are updated; change-detection state survives restarts
  (no repeated "discovered"/"delivery window" events).
- Times and day labels use the Home Assistant time zone; Polish plural forms.
- `READY_FOR_PICKUP` status with the ParcelShop address; translated substatus texts.
