"""Parsers for SFO front page and Tabulex agenda schedule."""

from __future__ import annotations

import logging
import re
from datetime import datetime

from ..models import AgendaItem, SfoInfo, TabulexDashboard, TabulexNewsItem
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
        notice_board=_find_infoweb_section(soup, r"opslagstavle"),
        weekly_plan=_find_infoweb_section(soup, r"ugeplan"),
        news=_find_infoweb_section(soup, r"nyt\s+fra\s+sfo"),
        shortcuts=links,
    )


def _find_infoweb_section(soup, heading_pattern: str) -> str:
    """Extract a legacy Infoweb box without depending on a single table layout."""
    heading = soup.find(string=re.compile(heading_pattern, re.IGNORECASE))
    if heading is None:
        return ""
    for container_name in ("td", "tr", "table", "div"):
        container = heading.find_parent(container_name)
        if container is None:
            continue
        text = container.get_text(" ", strip=True)
        text = re.sub(heading_pattern, "", text, count=1, flags=re.IGNORECASE).strip()
        if text and len(text) <= 4000:
            return text
    return ""


def parse_tabulex_dashboard(html: str) -> TabulexDashboard:
    """Parse the current server-rendered IST SFO guardian dashboard."""
    soup = make_soup(html)
    dashboard = TabulexDashboard()

    for panel in soup.select(".panel"):
        heading_element = panel.select_one(".panel-title, .panel-heading")
        if heading_element is None:
            continue
        heading = heading_element.get_text(" ", strip=True)
        body = panel.select_one(".panel-body")
        body_text = body.get_text(" ", strip=True) if body else ""
        lowered = heading.lower()

        if lowered == "status" and body_text and not dashboard.status:
            dashboard.status = body_text
        elif re.match(r"uge\s+\d+", lowered):
            dashboard.week_label = heading
        elif "fødselsdag" in lowered or "fodselsdag" in lowered:
            dashboard.birthday_message = body_text
            if body and "ingen fødselsdage" not in body_text.lower():
                dashboard.birthdays = [
                    text
                    for text in (
                        item.get_text(" ", strip=True)
                        for item in body.select("li, .birthday")
                    )
                    if text
                ]
                if not dashboard.birthdays and body_text:
                    dashboard.birthdays = [body_text]
        elif "billeder" in lowered:
            dashboard.galleries = [
                link.get_text(" ", strip=True)
                for link in (body.select("a") if body else [])
                if link.get_text(" ", strip=True)
            ]
            if not dashboard.galleries and body_text:
                dashboard.galleries = [body_text]
        elif body_text:
            header_id = str(heading_element.get("id") or "")
            if header_id.startswith("news_") or "ferie" in lowered or "ny" in lowered:
                dashboard.news.append(TabulexNewsItem(title=heading, content=body_text))

    dashboard.appointments = parse_tabulex_agenda(html)
    return dashboard


def parse_tabulex_appointments_page(html: str) -> list[AgendaItem]:
    """Parse current and future appointments from ``/guardian/appointments``."""
    soup = make_soup(html)
    items: list[AgendaItem] = []
    date_pattern = re.compile(r"\b(\d{1,2}/\d{1,2}-\d{2,4})\b")
    for row in soup.select("tr"):
        row_text = row.get_text(" ", strip=True)
        match = date_pattern.search(row_text)
        if not match:
            continue
        date_value = None
        for date_format in ("%d/%m-%y", "%d/%m-%Y"):
            try:
                date_value = datetime.strptime(match.group(1), date_format)
                break
            except ValueError:
                continue
        if date_value is None:
            continue
        fields: dict[str, str] = {"summary": row_text}
        delete_control = row.find(attrs={"onclick": re.compile(r"delete_container\d+")})
        if delete_control is not None:
            id_match = re.search(r"delete_container(\d+)", str(delete_control.get("onclick") or ""))
            if id_match:
                fields["eventplannedid"] = id_match.group(1)
        for index, cell in enumerate(row.select("th, td")):
            value = cell.get_text(" ", strip=True)
            if not value:
                continue
            classes = cell.get("class", [])
            key = str(classes[0]) if classes else f"column_{index}"
            fields[key] = value
        items.append(AgendaItem(date=date_value, fields=fields))
    return items


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
