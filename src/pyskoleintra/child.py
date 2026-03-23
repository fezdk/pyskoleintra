"""Child-scoped data access for Skoleintra.

Each Child instance is bound to a specific child's parent path and provides
methods to fetch all data sources available for that child.
"""

from __future__ import annotations

import logging
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

    def message(self, message_id: str | int, *, source: str = "unread") -> MessageDetail | list[MessageDetail]:
        """Fetch a single message's full detail.

        Args:
            message_id: The message or thread ID.
            source: Where to fetch from — ``"unread"``, ``"outbox"``, or ``"thread"``.
                For ``"thread"``, returns a list of all messages in the thread.
        """
        if source == "thread":
            # Thread-based loading returns JSON
            url = (
                f"{self._base_url}{self.parent_path}/messages/conversations"
                f"/loadmessagesforselectedconversation"
                f"?threadId={message_id}&takeFromRootMessageId={message_id}"
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
                resp = self._http.post(form["form"]["action"], data=form["inputs"])
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

        resp = self._http.get(tabulex_url)
        resp = self._http.follow_redirects(resp)

        # Handle potential SAML forms (Tabulex has its own SAML flow)
        for _ in range(5):
            if resp.status_code != 200:
                break
            form = parse_first_form(resp.text)
            if not form:
                break
            resp = self._http.post(form["form"]["action"], data=form["inputs"])
            resp = self._http.follow_redirects(resp)

        return sfo_parser.parse_tabulex_agenda(resp.text)

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
