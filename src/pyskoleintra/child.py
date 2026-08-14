"""Child-scoped data access for Skoleintra.

Each Child instance is bound to a specific child's parent path and provides
methods to fetch all data sources available for that child.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .exceptions import NotAuthorizedError, ParseError
from .models import (
    AgendaItem,
    Album,
    CalendarEvent,
    ChildInfo,
    ContactBookNote,
    Document,
    HomeworkEntry,
    MenuItem,
    MessageDetail,
    MessageSummary,
    MessageThread,
    Photo,
    ReadingContract,
    ReadingContractEntry,
    ScheduleDay,
    SfoInfo,
    StudentContact,
    TabulexDashboard,
    WeeklyPlan,
)
from .parsers import (
    calendar as cal_parser,
    contact_book as cb_parser,
    contacts as contacts_parser,
    documents as docs_parser,
    frontpage as fp_parser,
    homework as hw_parser,
    messages as msg_parser,
    photos as photos_parser,
    reading_contract as rc_parser,
    schedule as sched_parser,
    sfo as sfo_parser,
    weekly_plans as wp_parser,
)
from .parsers.common import base_site_url, parse_first_form, parse_js_redirect, resolve_url

if TYPE_CHECKING:
    from .http import HttpSession

logger = logging.getLogger(__name__)

_SSO_FIELD_NAMES = {
    "samlrequest",
    "samlresponse",
    "relaystate",
    "wa",
    "wctx",
    "wresult",
}


class Child:
    """Provides access to all data sources for a single child on Skoleintra.

    Not instantiated directly — use :attr:`Skoleintra.children` instead.
    """

    def __init__(self, info: ChildInfo, base_url: str, http: HttpSession):
        self._info = info
        self._base_url = base_url
        self._http = http

    @property
    def name(self) -> str:
        return self._info.name

    @property
    def parent_id(self) -> int:
        return self._info.parent_id

    @property
    def parent_path(self) -> str:
        return self._info.parent_path

    def __repr__(self) -> str:
        return f"Child(name={self.name!r}, parent_id={self.parent_id})"

    # -- helpers --

    def _url(self, path: str) -> str:
        """Build a full URL from a child-relative path."""
        return f"{self._base_url}{self.parent_path}/{path}"

    def _get(self, path: str, *, relogin_callback=None) -> str:
        """GET a child-scoped path and return the response text."""
        resp = self._http.get(self._url(path), relogin_callback=relogin_callback)
        return resp.text

    @staticmethod
    def _is_sso_form(form: dict) -> bool:
        names = {str(name).lower() for name in form.get("inputs", {})}
        return bool(names & _SSO_FIELD_NAMES)

    def _follow_sso(self, resp, *, max_steps: int = 10):
        """Follow redirects and identity-provider forms, never application forms."""
        resp = self._http.follow_redirects(resp)
        for _ in range(max_steps):
            if resp.status_code != 200:
                break
            form = parse_first_form(resp.text)
            if not form or not self._is_sso_form(form):
                break
            base = str(resp.url or "")
            action = resolve_url(base, form["form"]["action"])
            resp = self._http.post(action, data=form["inputs"])
            resp = self._http.follow_redirects(resp)
        return resp

    # -----------------------------------------------------------------------
    # Frontpage
    # -----------------------------------------------------------------------

    def frontpage_menu(self) -> list[MenuItem]:
        """Fetch the frontpage and return the navigation menu items."""
        html = self._get("Index")
        return fp_parser.parse_menu_items(html, self.parent_path)

    # -----------------------------------------------------------------------
    # Messages
    # -----------------------------------------------------------------------

    def inbox(self, *, before_message_id: int | None = None) -> list[MessageThread]:
        """Fetch inbox conversations.

        Args:
            before_message_id: For pagination, pass the last message ID from
                the previous page to load older conversations.
        """
        if before_message_id is not None:
            url = (
                f"{self._base_url}{self.parent_path}/messages/conversations"
                f"/getconversationsbypageindex"
                f"?takeFromRootMessageId={before_message_id}&searchRequest=&filter="
            )
            resp = self._http.get(url)
            return msg_parser.parse_inbox_page_json(resp.text)

        html = self._get("messages/conversations")
        return msg_parser.parse_inbox_conversations(html)

    def unread_messages(self) -> list[MessageSummary]:
        """Fetch the list of unread messages."""
        html = self._get("messages/unread")
        return msg_parser.parse_unread_messages_list(html)

    def message(
        self,
        message_id: str | int,
        *,
        source: str = "unread",
        thread_id: str | None = None,
    ) -> MessageDetail | list[MessageDetail]:
        """Fetch a single message's full detail.

        Args:
            message_id: The numeric message ID (e.g. from ``MessageThread.latest_message_id``).
            source: Where to fetch from — ``"unread"``, ``"outbox"``, or ``"thread"``.
                For ``"thread"``, returns a list of all messages in the thread.
            thread_id: The UUID thread ID (required for ``source="thread"``).
                If not provided, ``message_id`` is used as both threadId and
                takeFromRootMessageId — which only works if message_id is numeric.
        """
        if source == "thread":
            # The API requires threadId (UUID) and takeFromRootMessageId (numeric).
            tid = thread_id or message_id
            root_id = message_id
            url = (
                f"{self._base_url}{self.parent_path}/messages/conversations"
                f"/loadmessagesforselectedconversation"
                f"?threadId={tid}&takeFromRootMessageId={root_id}"
                f"&takeToMessageId=0&searchRequest=&_={int(time.time())}"
            )
            resp = self._http.get(url)
            return msg_parser.parse_message_detail_json(resp.text)
        elif source == "outbox":
            url = f"{self._base_url}{self.parent_path}/messages/outbox/message/{message_id}?pageIndex=1"
        else:
            url = f"{self._base_url}{self.parent_path}/messages/unread/message/{message_id}?pageIndex=1"

        resp = self._http.get(url)
        return msg_parser.parse_message_detail_html(resp.text, str(message_id))

    def search_messages(self, query: str) -> list[MessageThread]:
        """Search inbox messages by keyword."""
        url = (
            f"{self._base_url}{self.parent_path}/messages/conversations/search"
            f"?searchRequest={query}&filter=&_={int(time.time())}"
        )
        resp = self._http.get(url)
        return msg_parser.parse_inbox_page_json(resp.text)

    # -----------------------------------------------------------------------
    # Homework / diaries
    # -----------------------------------------------------------------------

    def homework(self) -> list[HomeworkEntry]:
        """Fetch homework entries from the diary."""
        html = self._get("diaries/list")
        diary_url = hw_parser.find_diary_url(html, self.parent_path)
        if not diary_url:
            return []

        full_url = f"{self._base_url}{diary_url}"
        resp = self._http.get(full_url)
        return hw_parser.parse_homework(resp.text)

    # -----------------------------------------------------------------------
    # Calendar
    # -----------------------------------------------------------------------

    def calendar_events(
        self, start: datetime | None = None, end: datetime | None = None
    ) -> list[CalendarEvent]:
        """Fetch calendar events (school events) as JSON.

        Args:
            start: Start of the date range (defaults to today).
            end: End of the date range (defaults to 30 days from start).
        """
        if start is None:
            start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if end is None:
            end = start + timedelta(days=30)

        start_ts = int(start.timestamp())
        end_ts = int(end.timestamp())
        # Event sources are child-scoped
        url = (
            f"{self._base_url}{self.parent_path}/calendareventsource/SchoolEvents"
            f"?departmentIds=[0]&start={start_ts}&end={end_ts}"
        )
        resp = self._http.get(url)
        return cal_parser.parse_calendar_events(resp.text)

    # -----------------------------------------------------------------------
    # Weekly plans
    # -----------------------------------------------------------------------

    def _wp_url(self, suffix: str) -> str:
        """Build a weekly plans URL.

        Skoleintra uses ``/parent/{id}/{name}item/weeklyplansandhomework/...``
        (no slash before ``item``) — this is how the server generates the link.
        """
        # parent_path is like /parent/1234/Oliver — append without trailing slash
        return f"{self._base_url}{self.parent_path}item/weeklyplansandhomework/{suffix}"

    def weekly_plans(self) -> list[dict]:
        """Fetch the list of available weekly plans."""
        url = self._wp_url("list")
        resp = self._http.get(url)
        return wp_parser.parse_weekly_plans_list(resp.text)

    def weekly_plan(self, week: int, year: int) -> WeeklyPlan:
        """Fetch a specific weekly plan by week number and year."""
        url = self._wp_url(f"item/sfo/{week:02d}-{year}")
        resp = self._http.get(url)
        return wp_parser.parse_weekly_plan_detail(resp.text, week, year)

    # -----------------------------------------------------------------------
    # Reading contract (læsekontrakt)
    # -----------------------------------------------------------------------

    def reading_contracts(self) -> list[ReadingContract]:
        """Fetch reading contracts via the AJAX API.

        The reading contracts page is a Vue.js SPA that loads data from an API
        endpoint. We first fetch the page to discover the API URL, then call it.

        Returns:
            List of :class:`ReadingContract` instances with full details
            including books and progress.
        """
        html = self._get("readingcontracts/Index")
        api_url = rc_parser.extract_contracts_api_url(html)
        if not api_url:
            return []

        full_url = f"{self._base_url}{api_url}"
        resp = self._http.get(full_url)
        return rc_parser.parse_reading_contracts_json(resp.text)

    # -----------------------------------------------------------------------
    # Contact book (kontaktbog)
    # -----------------------------------------------------------------------

    def contact_book(self) -> list[ContactBookNote]:
        """Fetch contact book notes."""
        html = self._get("contactbook/notes")
        return cb_parser.parse_contact_book_notes(html)

    # -----------------------------------------------------------------------
    # SFO / Tabulex
    # -----------------------------------------------------------------------

    def _sfo_authenticate(self) -> str:
        """Run the SFO/Infoweb SAML authentication flow.

        Returns the base URL of the Infoweb SFO site after authentication.

        The flow is:
        1. GET integration/redirect/Csi → 302 chain to Infoweb
        2. Infoweb redirects to CSI proxy for SAML
        3. SAML assertion posted back
        4. NsiLogin establishes the Infoweb session
        5. OpdaterLogfil.asp with JS redirect (may crash on Rammeside.asp — that's OK)
        """
        url = self._url(
            "integration/redirect/Csi?redirectTo=%2FInfoweb%2FFI2%2FSFOforside.asp"
        )
        resp = self._http.get(url)
        resp = self._http.follow_redirects(resp)

        # Handle SAML forms and JS redirects (up to 10 steps)
        for _ in range(10):
            if resp.status_code != 200:
                break
            form = parse_first_form(resp.text)
            if form:
                if not self._is_sso_form(form):
                    break
                base = str(resp.url or url)
                action = resolve_url(base, form["form"]["action"])
                resp = self._http.post(action, data=form["inputs"])
                resp = self._http.follow_redirects(resp)
                continue
            js_url = parse_js_redirect(resp.text)
            if js_url:
                base = str(resp.url) if resp.url else url
                js_url = resolve_url(base, js_url)
                resp = self._http.get(js_url)
                resp = self._http.follow_redirects(resp)
                continue
            break

        # Extract the Infoweb base URL from any response in the chain
        # It will be like https://school.skoleintra.dk/Infoweb/Fi2/...
        last_url = str(self._http.last_response.url) if self._http.last_response else url
        base_match = base_site_url(last_url)
        return base_match

    def sfo(self) -> SfoInfo:
        """Fetch SFO information including the Tabulex link.

        Navigates the SAML auth chain to the Infoweb SFO system, then fetches
        ``SFOforside.asp`` directly (the framing page may crash, but the
        session is still valid for direct page access).
        """
        sfo_base = self._sfo_authenticate()

        # Fetch SFOforside.asp directly (bypasses Rammeside.asp framing)
        sfo_url = f"{sfo_base}/Infoweb/Fi2/SFOforside.asp"
        resp = self._http.get(sfo_url)
        if resp.status_code != 200:
            raise ParseError(f"SFO frontpage returned {resp.status_code}")

        return sfo_parser.parse_sfo_page(resp.text, sfo_base)

    def tabulex_agenda(self, tabulex_url: str | None = None) -> list[AgendaItem]:
        """Fetch the Tabulex SFO schedule.

        Note: Tabulex is a fully separate system joined via SSO. The SAML
        handshake to Tabulex may fail if their SSO endpoint is down.

        Args:
            tabulex_url: Full URL to the Tabulex page. If None, fetches SFO info first.
        """
        if not tabulex_url:
            sfo_info = self.sfo()
            if not sfo_info.tabulex_url:
                return []
            tabulex_url = resolve_url(sfo_info.base_url, sfo_info.tabulex_url)

        resp = self._follow_sso(self._http.get(tabulex_url), max_steps=5)
        return sfo_parser.parse_tabulex_agenda(resp.text)

    def _tabulex_page(self, path: str | None = None):
        """Open the discovered Tabulex landing page or a read-only guardian path."""
        sfo_info = self.sfo()
        if not sfo_info.tabulex_url:
            raise ParseError("SFO front page does not contain a Tabulex link")
        discovered_url = resolve_url(sfo_info.base_url, sfo_info.tabulex_url)
        landing = self._follow_sso(self._http.get(discovered_url), max_steps=5)
        if path is None:
            return landing
        target = resolve_url(str(landing.url), path)
        return self._http.get(target)

    def tabulex_dashboard(self) -> TabulexDashboard:
        """Fetch status, notices, appointments, birthdays, and galleries."""
        return sfo_parser.parse_tabulex_dashboard(self._tabulex_page().text)

    def tabulex_guardian_page(self, path: str) -> str:
        """GET an allow-listed Tabulex guardian page and return its HTML."""
        allowed = {
            "/guardian/appointments",
            "/guardian/messages",
            "/guardian/holidays",
            "/guardian/gallery",
            "/guardian/activities",
        }
        if path not in allowed:
            raise ValueError(f"Unsupported Tabulex guardian path: {path}")
        return self._tabulex_page(path).text

    def tabulex_appointments(self) -> list[AgendaItem]:
        """Fetch current and future appointments across dashboard weeks."""
        html = self.tabulex_guardian_page("/guardian/appointments")
        return sfo_parser.parse_tabulex_appointments_page(html)

    def tabulex_prepare_delete_appointment(self, eventplannedid: str) -> AgendaItem:
        """Resolve an appointment ID to the exact currently displayed appointment."""
        if not str(eventplannedid).isdigit():
            raise ValueError("eventplannedid must be numeric")
        matches = [
            item
            for item in self.tabulex_appointments()
            if item.fields.get("eventplannedid") == str(eventplannedid)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one current appointment with ID {eventplannedid}, found {len(matches)}"
            )
        return matches[0]

    def tabulex_delete_appointment(
        self,
        eventplannedid: str,
    ):
        """Delete an appointment by its numeric Tabulex ID."""
        if not str(eventplannedid).isdigit():
            raise ValueError("eventplannedid must be numeric")

        page = self._tabulex_page("/guardian/appointments")
        soup = sfo_parser.make_soup(page.text)
        form = soup.find("form", id="form_editappointment")
        if form is None:
            raise ParseError("Tabulex edit/delete form not found")
        payload: dict[str, str] = {}
        for field in form.select("input[name]"):
            name = field.get("name")
            if name:
                payload[str(name)] = str(field.get("value") or "")
        prefix = "tx_tmsfo_pi1[formdata]"
        payload[f"{prefix}[eventplannedid]"] = str(eventplannedid)
        payload[f"{prefix}[eventowner]"] = ""
        payload["eID"] = "ajax"
        target = resolve_url(str(page.url), "/guardian/appointments")
        return self._http.post(target, data=payload)

    def tabulex_prepare_edit_appointment(
        self,
        eventplannedid: str,
        *,
        date: str,
        time: str,
        kind: str,
        pickup: str = "",
    ) -> tuple[AgendaItem, dict[str, str]]:
        """Validate a current appointment and build an edit payload from its modal."""
        if not str(eventplannedid).isdigit():
            raise ValueError("eventplannedid must be numeric")
        if not re.fullmatch(r"\d{2}/\d{2}-\d{2}(?:\d{2})?", date):
            raise ValueError("date must use DD/MM-YY or DD/MM-YYYY")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):(?:00|15|30|45)", time):
            raise ValueError("time must use HH:MM in 15-minute increments")

        page = self._tabulex_page("/guardian/appointments")
        appointments = sfo_parser.parse_tabulex_appointments_page(page.text)
        matches = [
            item
            for item in appointments
            if item.fields.get("eventplannedid") == str(eventplannedid)
        ]
        if len(matches) != 1:
            raise ValueError(f"Appointment {eventplannedid} is not uniquely present")
        soup = sfo_parser.make_soup(page.text)
        selector = soup.find("select", id="create_appointment_what")
        if selector is None:
            raise ParseError("Tabulex appointment type selector not found")
        options = {
            option.get_text(" ", strip=True).casefold(): str(option.get("value") or "")
            for option in selector.select("option[value]")
        }
        event_type_id = options.get(kind.strip().casefold())
        if not event_type_id:
            raise ValueError(f"Unknown Tabulex appointment type: {kind}")

        prefix = "tx_tmsfo_pi1[formdata]"
        return matches[0], {
            f"{prefix}[eventplannedid]": str(eventplannedid),
            f"{prefix}[eventtransport]": "",
            f"{prefix}[eventpickup]": pickup,
            f"{prefix}[eventtypeid]": event_type_id,
            f"{prefix}[eventdescription]": "",
            f"{prefix}[eventbeforestarttime]": "1400",
            f"{prefix}[eventstarttime]": time.replace(":", ""),
            f"{prefix}[eventstartdate]": date,
            f"{prefix}[eventendtime]": "",
            f"{prefix}[eventenddate]": "",
            f"{prefix}[eventtype]": "0",
            f"{prefix}[eventrulebyday]": "",
            f"{prefix}[eventignoreonholiday]": "0",
            f"{prefix}[eventowner]": "",
            f"{prefix}[eventruleinterval]": "",
        }

    def tabulex_edit_appointment(
        self,
        eventplannedid: str,
        values: dict[str, str],
    ):
        """Edit an appointment by its numeric Tabulex ID."""
        if not str(eventplannedid).isdigit():
            raise ValueError("eventplannedid must be numeric")
        page = self._tabulex_page("/guardian/appointments")
        soup = sfo_parser.make_soup(page.text)
        form = soup.find("form", id="form_editappointment")
        if form is None:
            raise ParseError("Tabulex edit form not found")
        payload: dict[str, str] = {}
        for field in form.select("input[name]"):
            name = field.get("name")
            if name:
                payload[str(name)] = str(field.get("value") or "")
        payload.update({str(key): str(value) for key, value in values.items()})
        prefix = "tx_tmsfo_pi1[formdata]"
        payload[f"{prefix}[eventplannedid]"] = str(eventplannedid)
        payload["eID"] = "ajax"
        target = resolve_url(str(page.url), "/guardian/appointments")
        return self._http.post(target, data=payload)

    def _tabulex_submit_modal(self, form_id: str, values: dict[str, str]):
        """Submit one known Tabulex modal form."""
        allowed = {
            "form_editappointment_header": "/guardian/appointments",
            "form_holiday_header": "/guardian/holidays",
            "form_reportsick": "/",
        }
        if form_id not in allowed:
            raise ValueError(f"Unsupported Tabulex write form: {form_id}")

        landing = self._tabulex_page()
        soup = sfo_parser.make_soup(landing.text)
        form = soup.find("form", id=form_id)
        if form is None:
            raise ParseError(f"Tabulex form not found: {form_id}")
        payload: dict[str, str] = {}
        for field in form.select("input[name], select[name], textarea[name]"):
            name = field.get("name")
            if not name or field.has_attr("disabled"):
                continue
            if field.name == "input" and field.get("type") in ("checkbox", "radio"):
                if not field.has_attr("checked"):
                    continue
            payload[str(name)] = str(field.get("value") or "")
        payload.update({str(key): str(value) for key, value in values.items()})
        payload["eID"] = "ajax"
        action = resolve_url(str(landing.url), allowed[form_id])
        return self._http.post(action, data=payload)

    def tabulex_submit_appointment(self, values: dict[str, str]):
        """Submit the dashboard's appointment modal (not a delete operation)."""
        return self._tabulex_submit_modal("form_editappointment_header", values)

    def tabulex_prepare_appointment(
        self,
        *,
        date: str,
        time: str,
        kind: str,
        pickup: str = "",
        event_type_id: str | None = None,
        allow_type_override: bool = False,
    ) -> dict[str, str]:
        """Validate an appointment against live modal choices and build its payload."""
        if not re.fullmatch(r"\d{2}/\d{2}-\d{2}", date):
            raise ValueError("date must use DD/MM-YY")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):(?:00|15|30|45)", time):
            raise ValueError("time must use HH:MM in 15-minute increments")
        landing = self._tabulex_page()
        soup = sfo_parser.make_soup(landing.text)
        select = soup.find("select", id="create_appointment_what")
        if select is None:
            raise ParseError("Tabulex appointment type selector not found")
        options = {
            option.get_text(" ", strip=True).casefold(): str(option.get("value") or "")
            for option in select.select("option[value]")
        }
        discovered_type_id = options.get(kind.strip().casefold())
        if not discovered_type_id:
            raise ValueError(f"Unknown Tabulex appointment type: {kind}")
        if event_type_id is not None:
            if not allow_type_override:
                raise ValueError("event_type_id override requires allow_type_override=True")
            discovered_type_id = event_type_id
        field = "tx_tmsfo_pi1[formdata]"
        return {
            f"{field}[eventplannedid]": "",
            f"{field}[eventtransport]": "",
            f"{field}[eventpickup]": pickup,
            f"{field}[eventtypeid]": discovered_type_id,
            f"{field}[eventdescription]": "",
            f"{field}[eventbeforestarttime]": "1400",
            f"{field}[eventstarttime]": time.replace(":", ""),
            f"{field}[eventstartdate]": date,
            f"{field}[eventendtime]": "",
            f"{field}[eventenddate]": "",
            f"{field}[eventtype]": "0",
            f"{field}[eventrulebyday]": "",
            f"{field}[eventignoreonholiday]": "0",
            f"{field}[eventowner]": "",
        }

    def tabulex_submit_holiday(self, values: dict[str, str]):
        """Submit the dashboard's holiday/day-off modal."""
        return self._tabulex_submit_modal("form_holiday_header", values)

    def tabulex_report_sick(self, values: dict[str, str]):
        """Submit the dashboard's sick-report modal."""
        return self._tabulex_submit_modal("form_reportsick", values)

    # -----------------------------------------------------------------------
    # Photos / albums
    # -----------------------------------------------------------------------

    def albums(self) -> list[Album]:
        """Fetch the list of photo albums."""
        html = self._get("photos/albums")
        return photos_parser.parse_albums_list(html, self.parent_path)

    def album_photos(self, album_id: int) -> list[Photo]:
        """Fetch photos from a specific album."""
        url = self._url(f"photos/albums/album/photos/{album_id}")
        resp = self._http.get(url)
        return photos_parser.parse_album_photos(resp.text)

    # -----------------------------------------------------------------------
    # Contacts
    # -----------------------------------------------------------------------

    def contacts(self) -> list[StudentContact]:
        """Fetch student contact cards."""
        html = self._get("contacts/students/cards")
        return contacts_parser.parse_student_contacts(html)

    # -----------------------------------------------------------------------
    # Documents
    # -----------------------------------------------------------------------

    def documents(self) -> list[Document]:
        """Fetch the school documents list."""
        html = self._get("documents/school")
        return docs_parser.parse_documents(html)

    # -----------------------------------------------------------------------
    # Schedule
    # -----------------------------------------------------------------------

    def schedule(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        week_start: str | None = None,
    ) -> list[ScheduleDay]:
        """Fetch the weekly schedule via the calendar lessons event source.

        Args:
            start: Start of the date range (defaults to Monday of current week).
            end: End of the date range (defaults to Friday of the same week).
            week_start: Deprecated — use ``start`` instead. If given, overrides ``start``.
        """
        if week_start is not None:
            start = datetime.strptime(week_start, "%Y-%m-%d")
        if start is None:
            today = datetime.now()
            start = today - timedelta(days=today.weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        if end is None:
            end = start + timedelta(days=5)

        start_ts = int(start.timestamp())
        end_ts = int(end.timestamp())

        # Discover the class name from the calendar page
        cal_url = self._url("calendar/myCalendar/today")
        resp = self._http.get(cal_url)
        resp = self._http.follow_redirects(resp)
        class_name = sched_parser.extract_class_name(resp.text)

        if not class_name:
            return []

        url = (
            f"{self._base_url}{self.parent_path}/calendareventsource/LessonsEvents"
            f"?className={class_name}&start={start_ts}&end={end_ts}"
        )
        resp = self._http.get(url)
        return sched_parser.parse_lesson_events(resp.text)
