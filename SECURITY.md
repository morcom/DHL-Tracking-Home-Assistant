# Security Policy

## Supported Versions

Security fixes are provided for the latest release and selected recent versions.

| Version | Supported |
| ------- | --------- |
| 1.7.x   | :white_check_mark: |
| < 1.7.0 | :x: |

If you report an issue on an older version, you may be asked to first reproduce it on the latest release.

## Reporting a Vulnerability

Please **do not** open public GitHub issues for security vulnerabilities.

Report privately by email:
- **Email:** ma@borkowski.nl
- **Subject:** `[SECURITY] DHL Tracking - <short summary>`

Please include:
- affected version (`manifest.json` version)
- deployment details (Home Assistant version, install method)
- reproduction steps and expected vs actual behavior
- impact assessment (what an attacker can do)
- logs/screenshots/PoC (sanitized)

## Sensitive Data Handling During Reports

Before sending logs or config data, remove or mask:
- DHL credentials (`email`, `password`, `user_id`, `api_key`)
- access/refresh/session tokens
- cookies (`Cookie`, `Set-Cookie`, XSRF values)
- personal parcel data (names, addresses, phone numbers, tracking codes)

If you need to share raw artifacts (for example HAR files), sanitize secrets first.

## Response Process

- Initial acknowledgment target: **within 72 hours**
- Triage/update target: **within 7 days**
- Fix timeline depends on severity, exploitability, and upstream API constraints

After a fix is available, a coordinated disclosure note may be published in release notes.

## Scope

In scope:
- this repository's Home Assistant custom integration code
- credential handling in integration configuration/runtime
- service/event/sensor behavior that could expose sensitive data

Out of scope:
- vulnerabilities in Home Assistant core or third-party dependencies (report to their maintainers)
- account compromises caused by reused/weak passwords or host-level compromise
- availability changes caused by DHL private/consumer endpoint changes

## Best Practices for Users

- Prefer business API credentials over consumer credentials when possible
- Keep Home Assistant and this integration updated
- Restrict access to Home Assistant, backups, and logs
- Rotate credentials if you suspect exposure
