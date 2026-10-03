"""High-level client for the Tabulex/IST SFO guardian application."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from requests import Response

from .exceptions import NetworkError, NotAuthorizedError, ParseError
from .dates import DEFAULT_TIMEZONE, resolve_timezone
from .http import response_fetched_at
from .models import (
    SfoInfo,
    StaffContact,
    TabulexContact,
    TabulexAgendaItem,
    TabulexAppointment,
    TabulexAppointmentInput,
    TabulexAppointmentType,
    TabulexCapabilities,
    TabulexDashboard,
    TabulexHolidayPeriod,
    TabulexIdentityPreferences,
    TabulexNavigationItem,
    TabulexNewsItem,
    TabulexOverview,
    TabulexRecurrence,
)
from .parsers import tabulex as tabulex_parser
from .parsers import tabulex_contacts as contacts_parser
from .parsers.common import make_soup, resolve_url
from .sso import follow_sso

if TYPE_CHECKING:
    from .http import HttpSession


class Tabulex:
    """Child-scoped access to the Tabulex guardian application.

    SFOweb routes and form payloads are implementation details of this class.
    Applications are responsible for any preview or confirmation policy before
    invoking a mutating method.
    """

    _APPOINTMENTS_PATH = "/guardian/appointments"
    _HOLIDAYS_PATH = "/guardian/holidays"
    _IDENTITY_CARD_PATH = "/guardian/identitycard"

    def __init__(
        self, http: HttpSession, sfo_loader: Callable[[], SfoInfo], *,
        source_timezone: str | ZoneInfo = DEFAULT_TIMEZONE,
    ):
        self._http = http
        self._sfo_loader = sfo_loader
        self._source_timezone = resolve_timezone(source_timezone)

    @property
    def source_timezone(self) -> ZoneInfo:
        """Timezone inherited from the owning Skoleintra instance."""
        return self._source_timezone

    def _date_context(self, response: Response) -> dict:
        fetched_at = response_fetched_at(response)
        return {
            "today": fetched_at.astimezone(self.source_timezone).date() if fetched_at else None,
            "infer_dates": fetched_at is not None,
        }

    def _page(self, path: str | None = None) -> Response:
        """Open the discovered landing page or a same-origin guardian page."""
        sfo_info = self._sfo_loader()
        if not sfo_info.tabulex_url:
            raise ParseError("SFO front page does not contain a Tabulex link")
        discovered_url = resolve_url(sfo_info.base_url, sfo_info.tabulex_url)
        landing = follow_sso(
            self._http,
            self._http.get(discovered_url),
            max_steps=5,
        )
        if path is None:
            return landing
        target = resolve_url(str(landing.url), path)
        return self._http.get(target)

    def dashboard(self) -> TabulexDashboard:
        """Fetch status, notices, this week's agenda, birthdays, and galleries."""
        page = self._page()
        return tabulex_parser.parse_tabulex_dashboard(page.text, **self._date_context(page))

    def overview(self) -> TabulexOverview:
        """Fetch the complete one-request POC snapshot from the landing page."""
        page = self._page()
        return tabulex_parser.parse_tabulex_overview(page.text, **self._date_context(page))

    def status(self) -> str:
        """Fetch the child's current status text."""
        return self.dashboard().status

    def news(self) -> list[TabulexNewsItem]:
        """Fetch news and notice panels shown on the dashboard."""
        return self.dashboard().news

    def birthdays(self) -> list[str]:
        """Fetch birthday entries currently shown on the dashboard."""
        return self.dashboard().birthdays

    def agenda(self) -> list[TabulexAgendaItem]:
        """Fetch the agenda currently shown on the guardian dashboard."""
        page = self._page()
        return tabulex_parser.parse_tabulex_agenda(page.text, **self._date_context(page))

    def navigation(self) -> list[TabulexNavigationItem]:
        """Fetch child-facing sections discovered from the guardian side menu."""
        return tabulex_parser.parse_tabulex_navigation(self._page().text)

    def capabilities(self) -> TabulexCapabilities:
        """Detect available guardian actions without invoking them."""
        return tabulex_parser.parse_tabulex_capabilities(self._page().text)

    def appointments(self) -> list[TabulexAppointment]:
        """Fetch current and future appointments from the appointment list."""
        page = self._page(self._APPOINTMENTS_PATH)
        return tabulex_parser.parse_tabulex_appointments_page(page.text)

    def appointment(self, appointment_id: str) -> TabulexAppointment:
        """Return one currently listed appointment by its numeric ID."""
        appointment_id = self._validate_appointment_id(appointment_id)
        matches = [item for item in self.appointments() if item.id == appointment_id]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one current appointment with ID {appointment_id}, "
                f"found {len(matches)}"
            )
        return matches[0]

    def appointment_types(self) -> list[TabulexAppointmentType]:
        """Fetch the appointment types configured by this SFO installation."""
        return tabulex_parser.parse_tabulex_appointment_types(self._page().text)

    def holiday_periods(self) -> list[TabulexHolidayPeriod]:
        """Fetch holiday periods and the current attendance selections."""
        page = self._page(self._HOLIDAYS_PATH)
        return tabulex_parser.parse_tabulex_holiday_periods(page.text)

    def identity_preferences(self) -> TabulexIdentityPreferences:
        """Fetch non-sensitive visibility preferences from the identity card."""
        page = self._page(self._IDENTITY_CARD_PATH)
        return tabulex_parser.parse_tabulex_identity_preferences(page.text)

    def _contact_page(self, path: str) -> Response:
        page = self._page(path)
        if page.status_code in (401, 403) or 300 <= page.status_code < 400:
            raise NotAuthorizedError("SFO contact page requires authentication")
        if page.status_code != 200:
            raise NetworkError(f"SFO contact request returned HTTP {page.status_code}")
        return page

    def contacts(self) -> list[TabulexContact]:
        """Fetch the child's SFO contacts, including pickup and access flags.

        These are separate from school parents and class representatives.
        This reads the page only; it never submits contact editing forms.
        """
        return contacts_parser.parse_tabulex_contacts(
            self._contact_page("/guardian/contacts").text,
        )

    def staff_contacts(self) -> list[StaffContact]:
        """Fetch this SFO's staff and photo URLs, independently of school staff."""
        page = self._contact_page("/guardian/staff")
        return contacts_parser.parse_tabulex_staff(page.text, str(page.url))

    def create_appointment(self, appointment: TabulexAppointmentInput) -> Response:
        """Create an appointment and return the underlying HTTP response."""
        landing = self._page()
        payload = self._form_payload(landing.text, "form_editappointment_header")
        payload.update(self._appointment_payload(landing.text, appointment))
        payload["eID"] = "ajax"
        target = resolve_url(str(landing.url), self._APPOINTMENTS_PATH)
        return self._http.post(target, data=payload)

    def update_appointment(
        self,
        appointment_id: str,
        appointment: TabulexAppointmentInput,
    ) -> Response:
        """Update an appointment and return the underlying HTTP response."""
        appointment_id = self._validate_appointment_id(appointment_id)
        page = self._page(self._APPOINTMENTS_PATH)
        payload = self._form_payload(page.text, "form_editappointment")
        payload.update(self._appointment_payload(page.text, appointment))
        prefix = "tx_tmsfo_pi1[formdata]"
        payload[f"{prefix}[eventplannedid]"] = appointment_id
        payload["eID"] = "ajax"
        target = resolve_url(str(page.url), self._APPOINTMENTS_PATH)
        return self._http.post(target, data=payload)

    def delete_appointment(self, appointment_id: str) -> Response:
        """Delete an appointment and return the underlying HTTP response."""
        appointment_id = self._validate_appointment_id(appointment_id)
        page = self._page(self._APPOINTMENTS_PATH)
        payload = self._form_payload(page.text, "form_editappointment")
        prefix = "tx_tmsfo_pi1[formdata]"
        payload[f"{prefix}[eventplannedid]"] = appointment_id
        payload[f"{prefix}[eventowner]"] = ""
        payload["eID"] = "ajax"
        target = resolve_url(str(page.url), self._APPOINTMENTS_PATH)
        return self._http.post(target, data=payload)

    @staticmethod
    def _validate_appointment_id(appointment_id: str) -> str:
        value = str(appointment_id)
        if not value.isdigit():
            raise ValueError("appointment_id must be numeric")
        return value

    @staticmethod
    def _form_payload(html: str, form_id: str) -> dict[str, str]:
        soup = make_soup(html)
        form = soup.find("form", id=form_id)
        if form is None:
            raise ParseError(f"Tabulex form not found: {form_id}")
        payload: dict[str, str] = {}
        for field in form.select("input[name]"):
            name = field.get("name")
            if name:
                payload[str(name)] = str(field.get("value") or "")
        return payload

    @staticmethod
    def _appointment_payload(
        html: str,
        appointment: TabulexAppointmentInput,
    ) -> dict[str, str]:
        appointment_types = {
            item.name.casefold(): item.id
            for item in tabulex_parser.parse_tabulex_appointment_types(html)
        }
        appointment_type_id = appointment_types.get(appointment.kind.strip().casefold())
        if not appointment_type_id:
            raise ValueError(f"Unknown Tabulex appointment type: {appointment.kind}")

        prefix = "tx_tmsfo_pi1[formdata]"
        return {
            f"{prefix}[eventplannedid]": "",
            f"{prefix}[eventtransport]": appointment.transport,
            f"{prefix}[eventpickup]": appointment.pickup,
            f"{prefix}[eventtypeid]": appointment_type_id,
            f"{prefix}[eventdescription]": appointment.description,
            f"{prefix}[eventbeforestarttime]": appointment.before_start_time.strftime("%H%M"),
            f"{prefix}[eventstarttime]": appointment.start_time.strftime("%H%M"),
            f"{prefix}[eventstartdate]": appointment.date.strftime("%d/%m-%y"),
            f"{prefix}[eventendtime]": (
                appointment.end_time.strftime("%H%M") if appointment.end_time else ""
            ),
            f"{prefix}[eventenddate]": (
                appointment.end_date.strftime("%d/%m-%y") if appointment.end_date else ""
            ),
            f"{prefix}[eventtype]": {
                TabulexRecurrence.NONE: "0",
                TabulexRecurrence.WEEKLY: "1",
                TabulexRecurrence.EVERY_OTHER_WEEK: "2",
            }[appointment.recurrence],
            f"{prefix}[eventrulebyday]": " ".join(
                str(int(day)) for day in appointment.weekdays
            ),
            f"{prefix}[eventignoreonholiday]": (
                "0" if appointment.ignore_on_holiday else "1"
            ),
            f"{prefix}[eventowner]": appointment.owner_id,
        }
