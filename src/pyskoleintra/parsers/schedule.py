"""Parser for the schedule/timetable via LessonsEvents calendar source."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime

from ..models import ScheduleDay, ScheduleLesson


def extract_class_name(calendar_html: str) -> str | None:
    """Extract the class name from the calendar page HTML.

    The LessonsEvents URL includes a ``className`` parameter (e.g. ``02Y``).
    We find it by searching for the event source URL in the HTML.
    """
    match = re.search(r"LessonsEvents\?className=([^&\"']+)", calendar_html)
    return match.group(1) if match else None


def parse_lesson_events(json_text: str) -> list[ScheduleDay]:
    """Parse the JSON response from the LessonsEvents calendar endpoint.

    Groups lessons by day and returns a sorted list of ScheduleDay instances.
    """
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    # Group by date
    days: dict[str, list[ScheduleLesson]] = defaultdict(list)
    day_dates: dict[str, datetime] = {}

    for item in data:
        start = _parse_dotnet_date(item.get("startDate", ""))
        end = _parse_dotnet_date(item.get("endDate", ""))
        if not start or not end:
            continue

        day_key = start.strftime("%Y-%m-%d")
        day_dates[day_key] = start.replace(hour=0, minute=0, second=0, microsecond=0)

        time_str = f"{start:%H:%M}-{end:%H:%M}"
        subject = item.get("lessonName", "")
        teacher = item.get("staffName", "")
        room = item.get("lessonClassName", "")

        days[day_key].append(ScheduleLesson(
            time=time_str,
            subject=subject,
            teacher=teacher,
            room=room,
        ))

    # Sort by date, then lessons by time within each day
    result: list[ScheduleDay] = []
    for day_key in sorted(days.keys()):
        lessons = sorted(days[day_key], key=lambda l: l.time)
        result.append(ScheduleDay(date=day_dates[day_key], lessons=lessons))

    return result


def _parse_dotnet_date(value: str) -> datetime | None:
    """Parse a .NET ``/Date(milliseconds)/`` timestamp."""
    if not value:
        return None
    match = re.match(r"/Date\((\d+)\)/", str(value))
    if match:
        return datetime.fromtimestamp(int(match.group(1)) / 1000)
    return None


def parse_schedule(html: str) -> list[ScheduleDay]:
    """Legacy HTML table parser — kept for backwards compatibility.

    Parses a ``<table>`` based schedule, though the primary method now uses
    the LessonsEvents JSON calendar endpoint instead.
    """
    from .common import extract_text, make_soup

    soup = make_soup(html)
    days: list[ScheduleDay] = []

    table = soup.select_one("table.schedule, table.timetable, .sk-schedule table")
    if not table:
        return days

    headers = [extract_text(th) for th in table.select("thead th, tr:first-child th")]
    body_rows = table.select("tbody tr")
    if not body_rows:
        return days

    num_days = max(len(headers) - 1, 0)
    day_lessons: dict[int, list[ScheduleLesson]] = {i: [] for i in range(num_days)}

    for row in body_rows:
        cells = row.find_all("td")
        if not cells:
            continue
        time_text = extract_text(cells[0]) if cells else ""
        for col_idx, cell in enumerate(cells[1:], start=0):
            if col_idx >= num_days:
                break
            subject = extract_text(cell)
            if subject:
                teacher = extract_text(cell.select_one(".teacher"))
                room = extract_text(cell.select_one(".room"))
                day_lessons[col_idx].append(ScheduleLesson(
                    time=time_text, subject=subject, teacher=teacher, room=room,
                ))

    for col_idx in range(num_days):
        if day_lessons[col_idx]:
            days.append(ScheduleDay(
                date=datetime.min,
                lessons=day_lessons[col_idx],
            ))

    return days
