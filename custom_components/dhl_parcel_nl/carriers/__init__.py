"""Carrier layer: DHL, PostNL and Dragonfly clients plus carrier detection."""

from __future__ import annotations

import re

from .base import (
    CARRIER_DHL,
    CARRIER_DISPLAY_NAMES,
    CARRIER_DRAGONFLY,
    CARRIER_POSTNL,
    CARRIERS,
)

__all__ = [
    "CARRIERS",
    "CARRIER_DHL",
    "CARRIER_DISPLAY_NAMES",
    "CARRIER_DRAGONFLY",
    "CARRIER_POSTNL",
    "carrier_from_name",
    "detect_carrier",
    "is_ambiguous",
    "normalize_code",
    "parse_code_spec",
]

_DRAGONFLY_RE = re.compile(r"^AMZNL\d{12}$")
_DHL_RE = re.compile(r"^(JVGL|JJD|JD\d|3SDHL)[A-Z0-9]+$")
# PostNL domestic (3S + 4 letter customer code + digits), KG barcodes and
# international UPU S10 codes ending in NL.
_POSTNL_RE = re.compile(r"^(KG[A-Z0-9]{6,}|[A-Z]{2}\d{9}NL)$")
_AMBIGUOUS_3S_RE = re.compile(r"^3S[A-Z]{4}\d+$")

_NAME_HINTS: tuple[tuple[str, str], ...] = (
    ("postnl", CARRIER_POSTNL),
    ("post nl", CARRIER_POSTNL),
    ("dragonfly", CARRIER_DRAGONFLY),
    ("intelcom", CARRIER_DRAGONFLY),
    ("dhl", CARRIER_DHL),
)


def normalize_code(code: str) -> str:
    """Upper-case a tracking code and drop whitespace."""
    return re.sub(r"\s+", "", str(code or "")).upper()


def carrier_from_name(name: str | None) -> str | None:
    """Map a free-text carrier name (e.g. from Amazon) to a supported carrier."""
    if not name:
        return None
    lowered = str(name).strip().lower()
    if lowered in CARRIERS:
        return lowered
    for needle, carrier in _NAME_HINTS:
        if needle in lowered:
            return carrier
    return None


def detect_carrier(
    code: str,
    carrier_name: str | None = None,
    default: str | None = CARRIER_DHL,
) -> str | None:
    """Return the carrier of a tracking code.

    A carrier name reported by the source (Amazon, account sync) wins, because
    ``3S`` codes are used by both DHL and PostNL. A carrier name that is not
    supported returns ``None`` so the caller can ignore the shipment.
    Codes without a recognizable pattern fall back to ``default`` (DHL, the
    only carrier of earlier versions).
    """
    if carrier_name:
        return carrier_from_name(carrier_name)

    normalized = normalize_code(code)
    if _DRAGONFLY_RE.match(normalized):
        return CARRIER_DRAGONFLY
    if _DHL_RE.match(normalized):
        return CARRIER_DHL
    if _POSTNL_RE.match(normalized):
        return CARRIER_POSTNL
    if _AMBIGUOUS_3S_RE.match(normalized):
        return default
    return default


def is_ambiguous(code: str) -> bool:
    """Return True for codes used by more than one carrier (3S + 4 letters)."""
    return bool(_AMBIGUOUS_3S_RE.match(normalize_code(code)))


def parse_code_spec(spec: str) -> tuple[str | None, str]:
    """Parse ``carrier:code`` (or a bare code) from the manual codes option.

    Returns ``(carrier, code)``; ``carrier`` is ``None`` when it must be
    auto-detected. Bare codes are only stripped (not upper-cased) so codes
    configured in earlier versions keep their sensor unique_id.
    """
    raw = str(spec or "").strip()
    if ":" in raw:
        prefix, _, rest = raw.partition(":")
        if prefix.strip().lower() in CARRIERS:
            return prefix.strip().lower(), normalize_code(rest)
    return None, raw
