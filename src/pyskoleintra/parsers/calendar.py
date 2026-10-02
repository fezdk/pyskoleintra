"""Parser for Skoleintra calendar events (JSON endpoint)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from ..dates import DEFAULT_TIMEZONE, in_timezone
from ..models import CalendarEvent


def parse_calendar_events(
    json_text: str, *, source_timezone: ZoneInfo = ZoneInfo(DEFAULT_TIMEZONE),
) -> list[CalendarEvent]:
    """Parse the JSON response from ``/calendareventsource/SchoolEvents``.

    The endpoint returns an array of event objects. Dates may be in ISO 8601
    format or .NET ``/Date(milliseconds)/`` format.
    """
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    events: list[CalendarEvent] = []
    for item in data:
        try:
            start = _parse_datetime(
                item.get("startDate", item.get("start", item.get("Start", ""))), source_timezone,
            )
            end = _parse_datetime(
                item.get("endDate", item.get("end", item.get("End", ""))), source_timezone,
            )
        except (ValueError, TypeError, OverflowError, OSError):
            continue

        events.append(CalendarEvent(
            id=str(item.get("originalId", item.get("id", item.get("Id", "")))),
            title=item.get("title", item.get("Title", "")),
            start=start,
            end=end,
            all_day=item.get("allDay", item.get("AllDay", False)),
            description=item.get("description", item.get("Description", "")),
            location=item.get("location", item.get("Location", "")),
        ))

    return events


def _parse_datetime(
    value: str | int, source_timezone: ZoneInfo = ZoneInfo(DEFAULT_TIMEZONE),
) -> datetime:
    """Parse a datetime from Skoleintra.

    Handles:
        - .NET format: ``/Date(1774821600000)/``
        - ISO 8601: ``2026-03-30T00:00:00``
        - Date only: ``2026-03-30``
    """
    if value is None or value == "" or isinstance(value, bool):
        raise ValueError("Empty datetime")

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, source_timezone)

    value = str(value)

    # .NET /Date(milliseconds)/ format
    net_match = re.fullmatch(r"/Date\((-?\d+)\)/", value)
    if net_match:
        return datetime.fromtimestamp(int(net_match.group(1)) / 1000, source_timezone)

    # ISO formats
    return in_timezone(datetime.fromisoformat(value.replace("Z", "+00:00")), source_timezone)
