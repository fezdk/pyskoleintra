"""Parsers for the Tabulex/IST SFO guardian application."""

from __future__ import annotations

import re
from datetime import date, datetime, time
from urllib.parse import urlsplit

from ..models import (
    TabulexAgendaItem,
    TabulexAppointment,
    TabulexAppointmentType,
    TabulexCapabilities,
    TabulexDashboard,
    TabulexHolidayDay,
    TabulexHolidayPeriod,
    TabulexIdentityPreferences,
    TabulexNavigationItem,
    TabulexNewsItem,
    TabulexOverview,
    TabulexRecurrence,
    TabulexWeekday,
)
from .common import MONTH_LONG, make_soup


_DATE_PATTERN = re.compile(r"\b(\d{1,2}/\d{1,2}-\d{2,4})\b")
_TIME_PATTERN = re.compile(r"(?:kl\.\s*)?(\d{1,2})[:.](\d{2})")


def parse_tabulex_overview(html: str) -> TabulexOverview:
    """Parse all POC metadata available from one guardian landing-page GET."""
    return TabulexOverview(
        dashboard=parse_tabulex_dashboard(html),
        navigation=tuple(parse_tabulex_navigation(html)),
        appointment_types=tuple(parse_tabulex_appointment_types(html)),
        capabilities=parse_tabulex_capabilities(html),
    )


def parse_tabulex_dashboard(html: str) -> TabulexDashboard:
    """Parse the current server-rendered guardian dashboard."""
    soup = make_soup(html)
    dashboard = TabulexDashboard()

    for panel in soup.select(".panel"):
        heading_element = panel.select_one(".panel-title, .panel-heading")
        if heading_element is None:
            continue
        heading = heading_element.get_text(" ", strip=True)
        body = panel.select_one(".panel-body")
        body_text = body.get_text(" ", strip=True) if body else ""
        lowered = heading.casefold()

        if lowered == "status" and body_text and not dashboard.status:
            dashboard.status = body_text
        elif re.match(r"uge\s+\d+", lowered):
            dashboard.week_label = heading
        elif "fødselsdag" in lowered or "fodselsdag" in lowered:
            dashboard.birthday_message = body_text
            if body and "ingen fødselsdage" not in body_text.casefold():
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
            panel_classes = set(panel.get("class") or [])
            if (
                header_id.startswith("news_")
                or "panel__news" in panel_classes
                or "ferie" in lowered
            ):
                dashboard.news.append(TabulexNewsItem(title=heading, content=body_text))

    dashboard.appointments = parse_tabulex_agenda(html)
    return dashboard


def parse_tabulex_navigation(html: str) -> list[TabulexNavigationItem]:
    """Parse the child-facing side menu without assuming installation routes."""
    soup = make_soup(html)
    items: list[TabulexNavigationItem] = []
    seen_paths: set[str] = set()
    for link in soup.select(".nav-mainmenu__list a[href]"):
        path = urlsplit(str(link.get("href") or "")).path
        if not path or path in seen_paths:
            continue
        badge = link.select_one(".badge")
        badge_text = badge.get_text(" ", strip=True) if badge else ""
        badge_match = re.search(r"\d+", badge_text)
        badge_count = int(badge_match.group()) if badge_match else 0
        title = link.get_text(" ", strip=True)
        if badge_text and title.endswith(badge_text):
            title = title[: -len(badge_text)].strip()
        if not title:
            continue
        seen_paths.add(path)
        items.append(
            TabulexNavigationItem(
                title=title,
                path=path,
                badge_count=badge_count,
            )
        )
    return items


def parse_tabulex_capabilities(html: str) -> TabulexCapabilities:
    """Detect supported actions without calling any of them."""
    soup = make_soup(html)
    return TabulexCapabilities(
        can_create_appointment=soup.find("form", id="form_editappointment_header")
        is not None,
        can_report_sick=soup.find(id="sickbox_header") is not None,
        can_create_day_off=soup.find("form", id="form_holiday_header") is not None,
        can_update_activity=soup.find("form", id="form_updatechildactivity")
        is not None,
    )


def parse_tabulex_appointment_types(html: str) -> list[TabulexAppointmentType]:
    """Parse appointment-type choices configured in the create modal."""
    soup = make_soup(html)
    selector = soup.find("select", id="create_appointment_what")
    if selector is None:
        return []
    return [
        TabulexAppointmentType(
            id=str(option.get("value") or ""),
            name=option.get_text(" ", strip=True),
        )
        for option in selector.select("option[value]")
        if option.get("value") and option.get_text(" ", strip=True)
    ]


def parse_tabulex_appointments_page(html: str) -> list[TabulexAppointment]:
    """Parse current and future appointments and their editable view state."""
    soup = make_soup(html)
    items: list[TabulexAppointment] = []
    seen: set[tuple[str | None, date]] = set()
    for row in soup.select("tr"):
        row_text = row.get_text(" ", strip=True)
        date_match = _DATE_PATTERN.search(row_text)
        if not date_match:
            continue
        appointment_date = _parse_short_date(date_match.group(1))
        if appointment_date is None:
            continue

        appointment_id = _appointment_id(row)
        identity = (appointment_id, appointment_date)
        if identity in seen:
            continue
        seen.add(identity)

        cells = [cell.get_text(" ", strip=True) for cell in row.select("th, td")]
        details = cells[1] if len(cells) > 1 else ""
        kind, pickup = _split_appointment_details(details)

        start_time = _time_from_edit_controls(soup, "starttime", appointment_id)
        if start_time is None:
            start_time = _parse_time(row_text)

        description = _control_value(soup, "edit_description", appointment_id)
        transport = _control_value(soup, "edit_transport", appointment_id)
        owner_id = _control_value(soup, "homewith_id", appointment_id)
        before_start_time = _time_from_edit_controls(
            soup,
            "beforestarttime",
            appointment_id,
        )
        end_time = _time_from_edit_controls(soup, "endtime", appointment_id)
        end_date = _parse_short_date(
            _control_value(soup, "edit_enddate", appointment_id)
        )

        selected_kind = _selected_option_text(soup, "edit_what", appointment_id)
        if selected_kind:
            kind = selected_kind
        selected_pickup = _control_value(soup, "edit_pickup", appointment_id)
        if selected_pickup:
            pickup = selected_pickup

        recurrence = _parse_recurrence(
            _control_value(soup, "edit_type", appointment_id)
        )
        weekdays = tuple(
            weekday
            for weekday, control_id in (
                (TabulexWeekday.MONDAY, "monday"),
                (TabulexWeekday.TUESDAY, "tuesday"),
                (TabulexWeekday.WEDNESDAY, "wednesday"),
                (TabulexWeekday.THURSDAY, "thursday"),
                (TabulexWeekday.FRIDAY, "friday"),
            )
            if _control_checked(soup, control_id, appointment_id)
        )
        ignore_wire = _control_value(soup, "edit_ignoreonholiday", appointment_id)

        items.append(
            TabulexAppointment(
                id=appointment_id,
                date=appointment_date,
                kind=kind,
                start_time=start_time,
                pickup=pickup,
                description=description,
                before_start_time=before_start_time,
                end_time=end_time,
                end_date=end_date,
                recurrence=recurrence,
                weekdays=weekdays,
                ignore_on_holiday=ignore_wire != "1",
                transport=transport,
                owner_id=owner_id,
                summary=row_text,
            )
        )
    return items


def parse_tabulex_agenda(
    html: str,
    *,
    today: date | None = None,
) -> list[TabulexAgendaItem]:
    """Parse the dated week overview on the guardian dashboard."""
    soup = make_soup(html)
    agenda_table = soup.select_one("#agenda_plan table.agenda")
    if not agenda_table:
        return []

    reference_date = today or date.today()
    current_date: date | None = None
    items: list[TabulexAgendaItem] = []
    for row in agenda_table.find_all("tr"):
        row_classes = set(row.get("class") or [])
        if "day" in row_classes:
            heading = row.find("th")
            current_date = (
                _parse_long_date(heading.get_text(" ", strip=True), reference_date)
                if heading
                else None
            )
            continue
        if "info" not in row_classes or current_date is None:
            continue

        fields: dict[str, str] = {}
        for element in row.select("[class*=agenda__]"):
            css_class = next(
                (name for name in element.get("class") or [] if name.startswith("agenda__")),
                "",
            )
            value = element.get_text(" ", strip=True)
            if css_class and value:
                fields[css_class.removeprefix("agenda__")] = value
        if not fields:
            continue

        time_text = fields.pop("time", "")
        title = fields.pop("header", "") or fields.pop("activity", "")
        description = " ".join(fields.values())
        if title or description or time_text:
            items.append(
                TabulexAgendaItem(
                    date=current_date,
                    title=title or description,
                    time=_parse_time(time_text),
                    time_text=time_text,
                    description=description if title else "",
                )
            )
    return items


def parse_tabulex_holiday_periods(html: str) -> list[TabulexHolidayPeriod]:
    """Parse holiday attendance choices and the currently selected state."""
    soup = make_soup(html)
    periods: list[TabulexHolidayPeriod] = []
    for table in soup.select("table.reportedHoliday__table"):
        heading = table.find_previous(["h1", "h2", "h3", "h4"])
        title = heading.get_text(" ", strip=True) if heading else ""
        days: list[TabulexHolidayDay] = []
        for row in table.select("tr"):
            date_field = row.find(
                "input",
                attrs={"name": re.compile(r"\[holidays\]\[(\d+)\]\[holidaydate\]")},
            )
            if date_field is None:
                continue
            name = str(date_field.get("name") or "")
            id_match = re.search(r"\[holidays\]\[(\d+)\]", name)
            if not id_match:
                continue
            day_id = id_match.group(1)
            day_date = _parse_short_date(str(date_field.get("value") or ""))
            if day_date is None:
                row_date = _DATE_PATTERN.search(row.get_text(" ", strip=True))
                day_date = _parse_short_date(row_date.group(1)) if row_date else None
            if day_date is None:
                continue

            attendance_field = row.find(
                "input",
                attrs={
                    "name": re.compile(
                        rf"\[holidays\]\[{re.escape(day_id)}\]\[holidayattending\]"
                    ),
                    "checked": True,
                },
            )
            attending = None
            if attendance_field is not None:
                attending = str(attendance_field.get("value") or "") == "1"

            start_field = row.find("input", id=re.compile(rf"holidaytime_start_value_{day_id}"))
            end_field = row.find("input", id=re.compile(rf"holidaytime_end_value_{day_id}"))
            joint_care = row.find(
                "input",
                attrs={"name": re.compile(rf"\[{re.escape(day_id)}\]\[joint_care\]")},
            )
            days.append(
                TabulexHolidayDay(
                    id=day_id,
                    date=day_date,
                    attending=attending,
                    start_time=_parse_time(str(start_field.get("value") or ""))
                    if start_field
                    else None,
                    end_time=_parse_time(str(end_field.get("value") or ""))
                    if end_field
                    else None,
                    joint_care=str(joint_care.get("value") or "") == "1"
                    if joint_care
                    else False,
                )
            )
        if days:
            periods.append(TabulexHolidayPeriod(title=title, days=tuple(days)))
    return periods


def parse_tabulex_identity_preferences(html: str) -> TabulexIdentityPreferences:
    """Parse only non-sensitive visibility preferences from the identity card."""
    soup = make_soup(html)
    return TabulexIdentityPreferences(
        show_on_public_lists=_named_checkbox_checked(soup, "childshowonpubliclists"),
        show_birthday=_named_checkbox_checked(soup, "childshowbirthday"),
        show_picture_on_info_board=_named_checkbox_checked(
            soup,
            "childshowpictureinfoboard",
        ),
    )


def _named_checkbox_checked(soup, field_suffix: str) -> bool:
    field = soup.find(
        "input",
        attrs={"name": re.compile(rf"\[{re.escape(field_suffix)}\]$")},
    )
    return bool(field and field.has_attr("checked"))


def _appointment_id(row) -> str | None:
    candidates = " ".join(
        str(value)
        for element in row.find_all(True)
        for value in (
            element.get("id"),
            element.get("onclick"),
            element.get("data-target"),
        )
        if value
    )
    match = re.search(r"(?:delete|edit)_container(\d+)", candidates)
    return match.group(1) if match else None


def _split_appointment_details(details: str) -> tuple[str, str]:
    match = re.fullmatch(r"(.+?)\s+af\s+(.+)", details, re.IGNORECASE)
    if not match:
        return details, ""
    return match.group(1).strip(), match.group(2).strip()


def _selected_option_text(soup, prefix: str, item_id: str | None) -> str:
    if item_id is None:
        return ""
    field = soup.find("select", id=f"{prefix}{item_id}")
    if field is None:
        return ""
    option = field.find("option", selected=True)
    if option is None and field.get("value"):
        option = field.find("option", value=field.get("value"))
    return option.get_text(" ", strip=True) if option else ""


def _control_value(soup, prefix: str, item_id: str | None) -> str:
    if item_id is None:
        return ""
    field = soup.find(id=f"{prefix}{item_id}")
    if field is None:
        return ""
    if field.name == "select":
        selected = field.find("option", selected=True)
        if selected is not None:
            return str(selected.get("value") or "")
    return str(field.get("value") or "")


def _control_checked(soup, prefix: str, item_id: str | None) -> bool:
    if item_id is None:
        return False
    field = soup.find(id=f"{prefix}{item_id}")
    return bool(field and field.has_attr("checked"))


def _time_from_edit_controls(soup, prefix: str, item_id: str | None) -> time | None:
    hours = _control_value(soup, f"edit_{prefix}_hours", item_id)
    minutes = _control_value(soup, f"edit_{prefix}_minutes", item_id)
    return _parse_time(f"{hours}:{minutes}") if hours and minutes else None


def _parse_recurrence(value: str) -> TabulexRecurrence:
    return {
        "1": TabulexRecurrence.WEEKLY,
        "2": TabulexRecurrence.EVERY_OTHER_WEEK,
    }.get(value, TabulexRecurrence.NONE)


def _parse_time(value: str) -> time | None:
    compact = value.strip()
    if re.fullmatch(r"\d{4}", compact):
        compact = f"{compact[:2]}:{compact[2:]}"
    match = _TIME_PATTERN.search(compact)
    if not match:
        return None
    try:
        return time(int(match.group(1)), int(match.group(2)))
    except ValueError:
        return None


def _parse_short_date(value: str) -> date | None:
    match = _DATE_PATTERN.search(value)
    if not match:
        return None
    for date_format in ("%d/%m-%y", "%d/%m-%Y"):
        try:
            return datetime.strptime(match.group(1), date_format).date()
        except ValueError:
            continue
    return None


def _parse_long_date(value: str, today: date) -> date | None:
    parts = value.strip().split()
    if len(parts) < 3:
        return None
    try:
        day = int(parts[1].rstrip("."))
        month = MONTH_LONG.get(parts[2].casefold().rstrip("."))
        if month is None:
            return None
        result = date(today.year, month, day)
        if (today - result).days > 30:
            result = date(today.year + 1, month, day)
        return result
    except (ValueError, IndexError):
        return None
