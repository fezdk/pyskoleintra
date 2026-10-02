"""Conservative normalization of message dates without inventing missing values."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..dates import localize

MONTHS = {
    "jan": 1, "januar": 1, "feb": 2, "februar": 2, "mar": 3, "marts": 3,
    "apr": 4, "april": 4, "maj": 5, "jun": 6, "juni": 6, "jul": 7, "juli": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "okt": 10, "oktober": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
WEEKDAYS = {
    "mandag": 0, "man": 0, "tirsdag": 1, "tirs": 1, "onsdag": 2, "ons": 2,
    "torsdag": 3, "tors": 3, "fredag": 4, "fre": 4, "lørdag": 5, "lør": 5,
    "søndag": 6, "søn": 6,
}
ISO = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})(?:[T ](?P<clock>\d{2}:\d{2}"
    r"(?::(?P<seconds>\d{2})(?:[.,](?P<fraction>\d{1,6}))?)?)"
    r"(?P<offset>Z|[+-]\d{2}:\d{2})?)?", re.I,
)
DANISH = re.compile(
    r"(?:(?P<weekday>[a-zæøå]+)\.?[,]?\s+)?"
    r"(?P<day>\d{1,2})\.?\s+(?P<month>[a-zæøå]+)\.?\s+(?P<year>\d{4})"
    r"(?:\s+(?:kl\.?\s*)?(?P<clock>\d{1,2}:\d{2}(?::\d{2})?))?", re.I,
)
RELATIVE = re.compile(
    r"(?P<day>i dag|i går)(?:[,]?\s+(?:kl\.?\s*)?(?P<clock>\d{1,2}:\d{2}(?::\d{2})?))?",
    re.I,
)


def date_fields(
    raw: str, *, source_timezone: ZoneInfo | None = None,
    fetched_at: datetime | None = None, day_only: bool = False,
    machine_value: str | None = None,
) -> dict:
    """Return normalized fields, retaining only precision actually known.

    ISO machine values take precedence only when valid. A source timezone is
    needed for wall times; without it the full calendar date can still be
    retained. Archive sources always discard clock time, including synthetic
    00:00. Yearless dates and unqualified times never borrow today's date.
    """
    unknown = {"timestamp": None, "calendar_date": None, "date_precision": None}
    if isinstance(machine_value, str) and ISO.fullmatch(machine_value.strip()):
        parsed = date_fields(
            machine_value, source_timezone=source_timezone, fetched_at=fetched_at,
            day_only=day_only,
        )
        if parsed["calendar_date"] is not None:
            return parsed
    if not isinstance(raw, str):
        return unknown
    text = " ".join(raw.split())
    clock = None
    precision = "minute"
    instant = None
    try:
        match = ISO.fullmatch(text)
        if match:
            day = date.fromisoformat(match["date"])
            clock = match["clock"]
            if clock:
                precision = "microsecond" if match["fraction"] else "second" if match["seconds"] else "minute"
                # Validate the clock even for day-only archive data.
                clock = clock.replace(",", ".")
                time.fromisoformat(clock)
                if match["offset"]:
                    offset = match["offset"].upper().replace("Z", "+00:00")
                    if int(offset[1:3]) > 23 or int(offset[4:6]) > 59:
                        return unknown
                    instant = datetime.fromisoformat(f"{match['date']}T{clock}{offset}")
        else:
            match = DANISH.fullmatch(text)
            if match:
                month = MONTHS.get(match["month"].lower())
                if month is None:
                    return unknown
                day = date(int(match["year"]), month, int(match["day"]))
                if match["weekday"] and WEEKDAYS.get(match["weekday"].lower()) != day.weekday():
                    return unknown
                clock = match["clock"]
            else:
                match = RELATIVE.fullmatch(text)
                if not match or source_timezone is None or fetched_at is None or fetched_at.utcoffset() is None:
                    return unknown
                day = fetched_at.astimezone(source_timezone).date()
                if match["day"].lower() == "i går":
                    day -= timedelta(days=1)
                clock = match["clock"]
            if clock:
                clock = clock.zfill(5) if len(clock) == 4 else clock
                parts = clock.split(":")
                clock = ":".join([parts[0].zfill(2), *parts[1:]])
                time.fromisoformat(clock)
                precision = "second" if len(parts) == 3 else "minute"
        if day_only or not clock:
            return {"timestamp": None, "calendar_date": day, "date_precision": "day"}
        if instant is not None:
            if source_timezone is not None:
                instant = instant.astimezone(source_timezone)
        elif source_timezone is not None:
            instant = localize(datetime.combine(day, time.fromisoformat(clock)), source_timezone)
        if instant is None:
            return {"timestamp": None, "calendar_date": day, "date_precision": "day"}
        return {"timestamp": instant, "calendar_date": instant.date(), "date_precision": precision}
    except (ValueError, OverflowError):
        return unknown
