"""Timezone helpers for telemetry timestamps."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
PRESENTATION_TIMEZONE = ZoneInfo("America/Bogota")


def utc_now() -> datetime:
    """Return the current instant as an aware UTC datetime."""
    return datetime.now(UTC)


def normalize_utc(value: datetime) -> datetime:
    """Normalize an aware or naive datetime to an aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_timestamp(value: object, fallback: datetime) -> datetime:
    """Parse an ISO timestamp and normalize it, or use the reception instant."""
    if not isinstance(value, str):
        return normalize_utc(fallback)
    try:
        return normalize_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return normalize_utc(fallback)


def to_utc_iso(value: datetime) -> str:
    """Serialize a datetime using its canonical UTC representation."""
    return normalize_utc(value).isoformat()


def to_presentation_timezone(value: datetime) -> datetime:
    """Convert a timestamp to the presentation timezone without changing its instant."""
    return normalize_utc(value).astimezone(PRESENTATION_TIMEZONE)
