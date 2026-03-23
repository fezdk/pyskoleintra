"""Parsers for SFO front page and Tabulex agenda schedule."""

from __future__ import annotations

import logging
import re
from datetime import datetime

from ..models import AgendaItem, SfoInfo
from .common import MONTH_LONG, extract_text, make_soup

logger = logging.getLogger(__name__)


def parse_sfo_page(html: str, base_url: str) -> SfoInfo:
    """Parse the SFO front page (``SFOforside.asp``).

    Extracts the Tabulex link, upcoming activities, and news postings.
    """
    soup = make_soup(html)

    # Tabulex link
    tabulex_link = soup.select_one(
        'a[href*="Tabulex"], a[href*="tabulex"]'
    )
    tabulex_url = tabulex_link["href"] if tabulex_link else None

    # Shortcuts / links section
    links: dict[str, str] = {}
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if href and text and not href.startswith("#") and not href.startswith("javascript"):
            links[text] = href

    # Front page posting / content
    # The SFO page uses table-based layout with various sections
    front_page = ""
    # Try to find news content
    for td in soup.select("td"):
        text = td.get_text(strip=True)
        # Skip very short cells (navigation) and very long ones (full page)
        if 50 < len(text) < 2000 and "aktiviteter" not in text.lower()[:30]:
            front_page = text
            break

    return SfoInfo(
        base_url=base_url,
        tabulex_url=tabulex_url,
        front_page_posting=front_page,
    )


def parse_tabulex_agenda(html: str) -> list[AgendaItem]:
    """Parse the Tabulex SFO schedule page, extracting agenda items grouped by day.

    Structure: ``div#agenda_plan > table.agenda`` with ``tr.day`` (date header)
    followed by ``tr.info`` rows containing agenda item divs.
    """
    soup = make_soup(html)
    agenda_table = soup.select_one("#agenda_plan table.agenda")
    if not agenda_table:
        return []

    rows = agenda_table.find_all("tr")
    items: list[AgendaItem] = []

    current_date: datetime | None = None

    for row in rows:
        row_class = row.get("class", [])

        if "day" in row_class:
            th = row.find("th")
            if th:
                current_date = _parse_tabulex_date(th.get_text(strip=True))

        elif "info" in row_class and current_date is not None:
            fields: dict[str, str] = {}
            for div in row.find_all("div"):
                css_class = div.get("class", [""])
                if isinstance(css_class, list):
                    css_class = css_class[0] if css_class else ""
                if css_class:
                    key = css_class.replace("agenda__", "")
                    value = div.get_text(strip=True)
                    if value:
                        fields[key] = value

            if fields:
                items.append(AgendaItem(date=current_date, fields=fields))

    return items


def _parse_tabulex_date(text: str) -> datetime | None:
    """Parse a Tabulex date header like 'Mandag 26 juni'.

    Year is inferred: if the date is more than 30 days in the past, use next year.
    """
    parts = text.strip().split()
    if len(parts) < 3:
        return None

    try:
        day = int(parts[1].rstrip("."))
        month_str = parts[2].lower().rstrip(".")
        month = MONTH_LONG.get(month_str)
        if not month:
            return None

        year = datetime.now().year
        dt = datetime(year, month, day)

        # If more than 30 days in the past, assume next year
        if (datetime.now() - dt).days > 30:
            dt = datetime(year + 1, month, day)

        return dt
    except (ValueError, IndexError):
        return None
