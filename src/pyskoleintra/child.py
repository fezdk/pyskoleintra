"""Child-scoped data access for Skoleintra.

Each Child instance is bound to a specific child's parent path and provides
methods to fetch all data sources available for that child.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlencode, urljoin, urlsplit

from .exceptions import NotAuthorizedError, ParseError
from .models import (
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
    ReadingContractBook,
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
from .sso import is_sso_form
from .tabulex import Tabulex

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
        self._tabulex = Tabulex(http, self.sfo)

    @property
    def name(self) -> str:
        return self._info.name

    @property
    def parent_id(self) -> int:
        return self._info.parent_id

    @property
    def parent_path(self) -> str:
        return self._info.parent_path

    @property
    def tabulex(self) -> Tabulex:
        """Child-scoped access to the Tabulex/IST SFO guardian application."""
        return self._tabulex

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

    def set_messages_read_status(
        self,
        message_ids: str | int | Iterable[str | int],
        *,
        read: bool,
    ) -> None:
        """Mark one or more messages as read or unread.

        SkoleIntra's message UI uses one generic endpoint for both operations.
        The identifiers are numeric message IDs, not conversation UUIDs.

        Args:
            message_ids: One message ID or an iterable of message IDs.
            read: ``True`` to mark the messages read, ``False`` to mark them unread.
        """
        raw_ids = (
            [message_ids]
            if isinstance(message_ids, (str, int))
            else list(message_ids)
        )
        normalized_ids = [str(message_id).strip() for message_id in raw_ids]
        if not normalized_ids:
            raise ValueError("At least one message ID is required")
        if any(not message_id.isdecimal() for message_id in normalized_ids):
            raise ValueError("Message IDs must be numeric")

        query = urlencode(
            [
                *(("messageIds", message_id) for message_id in normalized_ids),
                ("readFlag", str(bool(read)).lower()),
            ]
        )
        response = self._http.post(
            f"{self._url('messages/changemessagestatus')}?{query}"
        )
        if not 200 <= response.status_code < 300:
            raise ParseError(
                f"Changing message read status returned HTTP {response.status_code}"
            )

    def mark_messages_read(
        self,
        message_ids: str | int | Iterable[str | int],
    ) -> None:
        """Mark one or more messages as read."""
        self.set_messages_read_status(message_ids, read=True)

    def mark_messages_unread(
        self,
        message_ids: str | int | Iterable[str | int],
    ) -> None:
        """Mark one or more messages as unread."""
        self.set_messages_read_status(message_ids, read=False)

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

    def reading_contracts(self, *, refresh: bool = False) -> list[ReadingContract]:
        """Fetch reading contracts via the AJAX API.

        The reading contracts page is a Vue.js SPA that loads data from an API
        endpoint. We first fetch the page to discover the API URL, then call it.
        Set ``refresh=True`` to bypass the optional development response cache.

        Returns:
            List of :class:`ReadingContract` instances with full details
            including books and progress.
        """
        provider = self._reading_contract_provider(refresh=refresh)
        key = self._reading_contract_list_key(provider)
        if key is None:
            return []
        return self._reading_contract_list(provider, key, refresh=refresh)

    def reading_contract_books(self, contract_id: int) -> list[ReadingContractBook]:
        """Fetch the books already registered in one contract, bypassing caches.

        Books have no separate ID: reuse their exact ``title`` and ``author``
        when listing entries or adding another reading of the same book.
        """
        _, contract = self._reading_contract_context(contract_id)
        return contract.books

    def reading_contract_entries(
        self, contract_id: int, *, title: str, author: str,
    ) -> list[ReadingContractEntry]:
        """Fetch one book's individual readings, including IDs, bypassing caches."""
        self._validate_reading_book(title, author)
        provider, contract = self._reading_contract_context(contract_id)
        return self._reading_entries(provider, contract.id, title, author)

    def add_reading_contract_entry(
        self, contract_id: int, *, title: str, author: str, amount: int,
    ) -> ReadingContractEntry:
        """Add exactly one reading for today and return its verified server ID.

        ``amount`` is 1–999 minutes or pages, according to the contract's
        ``is_page_used_for_count`` flag. Reusing title/author adds to that book;
        new title/author values create a book through the same single request.
        The server assigns the date; backdating is not supported by this form.

        A POST is never retried. If verification fails, the write may have
        succeeded: inspect fresh entries before deciding whether to retry.
        """
        self._validate_reading_book(title, author)
        if type(amount) is not int or not 1 <= amount <= 999:
            raise ValueError("Reading amount must be an integer between 1 and 999")
        provider, contract = self._reading_contract_context(contract_id, writable=True)
        save_url = self._reading_contract_endpoint(provider, "SaveRecord")
        before = self._reading_entries(provider, contract.id, title, author)
        before_ids = {entry.id for entry in before}
        self._reading_contract_post(save_url, {
            "contractId": contract.id,
            "studentId": contract.student_id,
            "progressRecordModel[Author]": author,
            "progressRecordModel[Title]": title,
            "progressRecordModel[ReadPagesCount]": amount,
        })
        try:
            after = self._reading_entries(provider, contract.id, title, author)
        except Exception as exc:
            raise ParseError(
                "Reading may have been added, but verification failed; "
                "inspect entries before retrying"
            ) from exc
        added = [entry for entry in after if entry.id not in before_ids]
        if (
            len(added) != 1
            or added[0].read_pages_count != amount
            or not before_ids.issubset({entry.id for entry in after})
        ):
            raise ParseError(
                "Could not uniquely identify the added reading; "
                "the write may have succeeded. Inspect entries before retrying"
            )
        return added[0]

    def update_reading_contract_entry(
        self, entry: ReadingContractEntry, *, amount: int,
    ) -> ReadingContractEntry:
        """Set one reading's minutes/pages to ``amount`` and return the updated entry.

        ``amount`` is the new total for this registration (0–999), not an
        increment. The ID, date, title, and author are preserved. Zero keeps
        the entry; use ``delete_reading_contract_entry`` to remove it.

        The supplied entry must still match fresh server data. An unchanged
        amount returns the fresh entry without posting. Otherwise, exactly one
        POST is made and its result verified, including the other existing
        entries in the book. Keep the returned entry for subsequent edits or
        deletion: the original object is not modified and becomes stale.

        A POST is never retried. If verification fails, the write may have
        succeeded: inspect fresh entries before deciding whether to retry.
        """
        if type(amount) is not int or not 0 <= amount <= 999:
            raise ValueError("Reading amount must be an integer between 0 and 999")
        provider, contract, before, current = self._reading_entry_context(entry)
        if amount == current.read_pages_count:
            return current
        update_url = self._reading_contract_endpoint(
            provider, "UpdatePagesCountOfProgressRecordUrl",
        )
        self._reading_contract_post(update_url, {
            "recordToUpdate[studentId]": contract.student_id,
            "recordToUpdate[contractId]": contract.id,
            "recordToUpdate[recordId]": current.id,
            "pagesCount": amount,
        })
        try:
            after = self._reading_entries(provider, contract.id, current.title, current.author)
        except Exception as exc:
            raise ParseError(
                "Reading may have been updated, but verification failed; "
                "inspect entries before retrying"
            ) from exc
        entries_by_id = {item.id: item for item in after}
        updated = entries_by_id.get(current.id)
        expected = replace(current, pages=str(amount), read_pages_count=amount)
        if updated is None or updated != expected:
            raise ParseError(
                "Reading update could not be verified; inspect fresh entries before retrying"
            )
        if any(entries_by_id.get(item.id) != item for item in before if item.id != current.id):
            raise ParseError("Other reading entries changed during update; inspect fresh entries")
        return updated

    def delete_reading_contract_entry(self, entry: ReadingContractEntry) -> None:
        """Delete exactly the supplied entry after checking it against fresh data.

        Pass an entry returned by ``add_reading_contract_entry``,
        ``update_reading_contract_entry``, or ``reading_contract_entries``.
        The contract, book, ID, date, and amount must still match. No other
        entry is selected if it is absent or stale. A POST is never retried;
        an uncertain result requires a fresh read.
        """
        provider, contract, before, _ = self._reading_entry_context(entry)
        delete_url = self._reading_contract_endpoint(provider, "DeleteReadingProgressRecordUrl")
        self._reading_contract_post(delete_url, {
            "recordToDelete[studentId]": contract.student_id,
            "recordToDelete[contractId]": contract.id,
            "recordToDelete[recordId]": entry.id,
        })
        try:
            after = self._reading_entries(provider, contract.id, entry.title, entry.author)
        except Exception as exc:
            raise ParseError(
                "Reading may have been deleted, but verification failed; "
                "inspect entries before retrying"
            ) from exc
        if any(item.id == entry.id for item in after):
            raise ParseError("Reading entry is still present after deletion")
        remaining = {item.id: item for item in after}
        if any(remaining.get(item.id) != item for item in before if item.id != entry.id):
            raise ParseError("Other reading entries changed during deletion; inspect fresh entries")

    def _reading_entry_context(
        self, entry: ReadingContractEntry,
    ) -> tuple[dict[str, str], ReadingContract, list[ReadingContractEntry], ReadingContractEntry]:
        """Verify a single entry against this child's fresh data before changing it."""
        if not isinstance(entry, ReadingContractEntry):
            raise ValueError("Expected one ReadingContractEntry")
        self._validate_reading_id(entry.id, "Entry ID")
        self._validate_reading_book(entry.title, entry.author)
        provider, contract = self._reading_contract_context(entry.contract_id, writable=True)
        entries = self._reading_entries(provider, contract.id, entry.title, entry.author)
        current = next((item for item in entries if item.id == entry.id), None)
        if current is None:
            raise ValueError("Reading entry is not present in this child's contract and book")
        if current.date != entry.date or current.read_pages_count != entry.read_pages_count:
            raise ValueError("Reading entry has changed; fetch it again before changing it")
        return provider, contract, entries, current

    @staticmethod
    def _validate_reading_id(value: int, label: str) -> None:
        if type(value) is not int or value <= 0:
            raise ValueError(f"{label} must be a positive integer")

    @staticmethod
    def _validate_reading_book(title: str, author: str) -> None:
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Book title must not be empty")
        if not isinstance(author, str) or not author.strip():
            raise ValueError("Book author must not be empty")

    def _reading_contract_provider(self, *, refresh: bool) -> dict[str, str]:
        response = self._http.get(self._url("readingcontracts/Index"), use_cache=not refresh)
        if response.status_code != 200:
            raise ParseError(f"Reading contracts page returned HTTP {response.status_code}")
        return rc_parser.extract_data_provider_settings(response.text)

    @staticmethod
    def _reading_contract_list_key(provider: dict[str, str]) -> str | None:
        return next((key for key in (
            "GetStudentClassReadingContracts", "GetStudentGroupReadingContracts",
        ) if key in provider), None)

    def _reading_contract_endpoint(self, provider: dict[str, str], key: str) -> str:
        if key not in provider:
            raise ParseError(f"Reading contracts endpoint {key} is unavailable")
        url = urljoin(self._url("readingcontracts/Index"), provider[key])
        parts = urlsplit(url)
        base = urlsplit(self._base_url)
        if (parts.scheme, parts.netloc) != (base.scheme, base.netloc):
            raise ParseError("Reading contracts endpoint must stay on the school's origin")
        if not parts.path.startswith(f"{self.parent_path}/readingcontracts/"):
            raise ParseError("Reading contracts endpoint belongs to another child or feature")
        return url

    def _reading_contract_list(
        self, provider: dict[str, str], key: str, *, refresh: bool,
    ) -> list[ReadingContract]:
        url = self._reading_contract_endpoint(provider, key)
        text = self._reading_contract_get(url, refresh=refresh)
        return rc_parser.parse_reading_contracts_json(text)

    def _reading_contract_context(
        self, contract_id: int, *, writable: bool = False,
    ) -> tuple[dict[str, str], ReadingContract]:
        self._validate_reading_id(contract_id, "Contract ID")
        provider = self._reading_contract_provider(refresh=True)
        key = self._reading_contract_list_key(provider)
        if key is None:
            raise ParseError("Reading contracts list endpoint is unavailable")
        contracts = self._reading_contract_list(provider, key, refresh=True)
        matches = [contract for contract in contracts if contract.id == contract_id]
        if len(matches) != 1:
            raise ValueError("Contract ID must identify one of this child's reading contracts")
        contract = matches[0]
        if writable:
            self._validate_reading_id(contract.student_id, "Student ID")
            if contract.is_read_only:
                raise NotAuthorizedError("Reading contract is read-only")
        return provider, contract

    def _reading_entries(
        self, provider: dict[str, str], contract_id: int, title: str, author: str,
    ) -> list[ReadingContractEntry]:
        url = self._reading_contract_endpoint(provider, "GetRecordsForBook")
        query = urlencode({"contractId": contract_id, "author": author, "title": title})
        text = self._reading_contract_get(url + ("&" if "?" in url else "?") + query, refresh=True)
        return rc_parser.parse_reading_contract_entries_json(
            text, contract_id=contract_id, title=title, author=author,
        )

    def _reading_contract_get(self, url: str, *, refresh: bool) -> str:
        response = self._http.get(url, use_cache=not refresh)
        if response.status_code != 200:
            raise ParseError(f"Reading contracts request returned HTTP {response.status_code}")
        try:
            data = json.loads(response.text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ParseError("Invalid reading contracts JSON response") from exc
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ParseError("Expected a list of reading contracts or entries")
        return response.text

    def _reading_contract_post(self, url: str, data: dict) -> None:
        # No redirects, automatic login callback, or retries for a mutation.
        response = self._http.post(url, data=data)
        if response.status_code not in (200, 204):
            raise ParseError(
                f"Reading mutation returned HTTP {response.status_code}; "
                "inspect fresh entries before retrying"
            )

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
                if not is_sso_form(form):
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
