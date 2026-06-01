"""Lightweight credentials obfuscation helpers.

This is local at-rest obfuscation (not strong cryptographic security).
"""

from __future__ import annotations

import base64
import hashlib

from homeassistant.core import HomeAssistant

from .const import DOMAIN


def _derive_key(hass: HomeAssistant) -> bytes:
    """Derive a stable local key from Home Assistant instance id."""
    seed = f"{hass.config.location_name}|{hass.config.currency}|{DOMAIN}"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _xor(data: bytes, key: bytes) -> bytes:
    """XOR data with repeating key bytes."""
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def encrypt_text(hass: HomeAssistant, value: str) -> str:
    """Obfuscate text for storage."""
    if not value:
        return ""
    key = _derive_key(hass)
    payload = _xor(value.encode("utf-8"), key)
    return base64.urlsafe_b64encode(payload).decode("ascii")


def decrypt_text(hass: HomeAssistant, value: str) -> str:
    """De-obfuscate text from storage."""
    if not value:
        return ""
    key = _derive_key(hass)
    payload = base64.urlsafe_b64decode(value.encode("ascii"))
    return _xor(payload, key).decode("utf-8")
