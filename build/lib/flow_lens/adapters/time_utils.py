from __future__ import annotations

from datetime import datetime, timezone


def normalize_venue_timestamp_ms(value: object) -> int | None:
    """Normalize a venue timestamp to epoch milliseconds, or None if ambiguous."""
    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.isdigit():
            return _normalize_epoch_units(int(candidate))
        return _parse_iso_timestamp_ms(candidate)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return _normalize_epoch_units(value)
    if isinstance(value, float):
        if not value.is_integer():
            return None
        return _normalize_epoch_units(int(value))
    return None


def _parse_iso_timestamp_ms(value: str) -> int | None:
    cleaned = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _normalize_epoch_units(raw: int) -> int | None:
    if raw <= 0:
        return None
    digits = len(str(abs(raw)))
    if digits == 10:  # seconds
        return raw * 1000
    if digits == 13:  # milliseconds
        return raw
    if digits == 16:  # microseconds
        return raw // 1000
    if digits == 19:  # nanoseconds
        return raw // 1_000_000
    return None
