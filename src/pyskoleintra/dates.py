"""Shared timezone handling, independent of the host's local timezone."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "Europe/Copenhagen"


def resolve_timezone(value: str | ZoneInfo) -> ZoneInfo:
    if isinstance(value, ZoneInfo):
        return value
    if not isinstance(value, str) or not value:
        raise ValueError("source_timezone must be an IANA timezone name")
    return ZoneInfo(value)


def localize(naive: datetime, zone: ZoneInfo) -> datetime | None:
    """Reject nonexistent/ambiguous wall times instead of choosing a DST fold."""
    instants = {}
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        utc = candidate.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == naive:
            instants.setdefault(utc, candidate)
    return next(iter(instants.values())) if len(instants) == 1 else None


def in_timezone(value: datetime, zone: ZoneInfo) -> datetime:
    """Interpret naive inputs in the instance zone; convert aware instants."""
    if value.utcoffset() is not None:
        return value.astimezone(zone)
    result = localize(value, zone)
    if result is None:
        raise ValueError("Ambiguous or nonexistent local time; provide an aware datetime")
    return result
