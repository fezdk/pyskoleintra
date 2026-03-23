"""Parser for homework/diary pages."""

from __future__ import annotations

import re
from datetime import datetime

from ..models import HomeworkEntry
from .common import MONTH_SHORT, make_soup


def find_diary_url(html: str, parent_path: str) -> str | None:
    """Extract the first diary notes URL from the diaries list page.

    Looks for links matching ``{parent_path}/diaries/list/notes/{id}``.
    """
    pattern = re.escape(parent_path) + r"/diaries/list/notes/\d+"
    match = re.search(pattern, html)
    return match.group(0) if match else None


def parse_homework(html: str) -> list[HomeworkEntry]:
    """Parse homework entries from a diary notes page.

    Structure: ``#sk-diary-notes-container`` contains ``<ul class="sk-list">``
    with ``<li>`` per date, each holding a table of subject/description rows.
    """
    soup = make_soup(html)
    container = soup.select_one("#sk-diary-notes-container")
    if not container:
        return []

    entries: list[HomeworkEntry] = []

    for li in container.select("ul.sk-list > li"):
        # Date is in a bold tag inside a white-box div
        date_el = li.select_one("div.sk-white-box b")
        if not date_el:
            continue

        date_text = date_el.get_text(strip=True)
        date = _parse_danish_date(date_text)
        if not date:
            continue

        # Table rows: first is header, rest are entries
        for row in li.select("table tbody tr")[1:]:  # skip header
            cols = row.find_all("td")
            if len(cols) < 2:
                continue

            subject = cols[0].get_text(strip=True)
            description = cols[1].get_text(strip=True)

            if len(description) > 2:
                entries.append(HomeworkEntry(
                    date=date,
                    subject=subject,
                    description=description,
                ))

    return entries


def _parse_danish_date(text: str) -> datetime | None:
    """Parse a Danish date string like 'Mandag 26 maj 2025' into a datetime."""
    # Remove non-alphanumeric chars except spaces
    cleaned = re.sub(r"[^a-zA-ZæøåÆØÅ0-9 ]", "", text)
    parts = cleaned.split()

    # Expected: [weekday, day, month, year]
    if len(parts) < 4:
        return None

    try:
        day = int(parts[1])
        month_str = parts[2][:3].lower()
        year = int(parts[3])
        month = MONTH_SHORT.get(month_str)
        if not month:
            return None
        return datetime(year, month, day)
    except (ValueError, IndexError):
        return None
