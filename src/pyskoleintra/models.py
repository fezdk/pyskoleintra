"""Data models for Skoleintra entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import Enum, IntEnum
from typing import Literal


DatePrecision = Literal["day", "minute", "second", "microsecond"]


# ---------------------------------------------------------------------------
# Child / profile
# ---------------------------------------------------------------------------

@dataclass
class ChildInfo:
    """A child enrolled at the school, as seen by a parent."""

    name: str
    parent_id: int
    parent_path: str  # e.g. /parent/1234/Oliver


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

@dataclass
class MessageThread:
    """A conversation thread from the inbox."""

    thread_id: str
    subject: str
    messages_count: int
    latest_message_id: int
    date: str
    is_unread: bool
    sender_name: str = ""
    profile_image_url: str = ""
    has_attachments: bool = False
    is_forwarded: bool = False
    is_replied: bool = False
    thread_participants: str = ""
    unread_messages_count: int = 0
    timestamp: datetime | None = None
    calendar_date: date | None = None
    date_precision: DatePrecision | None = None


@dataclass
class MessageSummary:
    """A single message entry from the unread or inbox list."""

    id: str | None
    subject: str
    sender: str
    date: str
    unread: bool = False
    timestamp: datetime | None = None
    calendar_date: date | None = None
    date_precision: DatePrecision | None = None


@dataclass
class MessageDetail:
    """Full detail of a single message."""

    id: str
    subject: str
    sender: str
    date: str
    content: str
    recipients: list[str] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    auto_delete_date: str = ""
    is_archived: bool | None = None
    is_outbox: bool | None = None
    timestamp: datetime | None = None
    calendar_date: date | None = None
    date_precision: DatePrecision | None = None


@dataclass
class ArchivedMessageSummary:
    """One archive entry. Its ID is separate from inbox message IDs."""

    archive_id: int
    subject: str
    sender: str
    date: str
    timestamp: datetime | None = None
    calendar_date: date | None = None
    date_precision: DatePrecision | None = None


@dataclass
class ArchivedMessageDetail:
    """Archive content; no inbox ID or synthetic midnight timestamp is exposed."""

    archive_id: int
    subject: str
    sender: str
    date: str
    content: str
    recipients: list[str] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    timestamp: datetime | None = None
    calendar_date: date | None = None
    date_precision: DatePrecision | None = None


@dataclass
class ArchivedMessagePage:
    """One server page of archive entries, without automatic detail fetching."""

    messages: list[ArchivedMessageSummary]
    page: int
    next_page: int | None = None


@dataclass
class Attachment:
    """A file attached to a message."""

    name: str
    url: str


# ---------------------------------------------------------------------------
# Homework / diaries
# ---------------------------------------------------------------------------

@dataclass
class HomeworkEntry:
    """A single homework entry for a subject on a given date."""

    date: datetime
    subject: str
    description: str


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

@dataclass
class CalendarEvent:
    """A calendar event (from JSON endpoint)."""

    id: str
    title: str
    start: datetime
    end: datetime
    all_day: bool = False
    description: str = ""
    location: str = ""


# ---------------------------------------------------------------------------
# Weekly plans
# ---------------------------------------------------------------------------

@dataclass
class WeeklyPlan:
    """A weekly plan entry."""

    week: int
    year: int
    content: dict  # Structured content varies by school


# ---------------------------------------------------------------------------
# SFO / Tabulex
# ---------------------------------------------------------------------------

@dataclass
class SfoInfo:
    """SFO front-page information."""

    base_url: str
    tabulex_url: str | None = None
    front_page_posting: str = ""
    notice_board: str = ""
    weekly_plan: str = ""
    news: str = ""
    shortcuts: dict[str, str] = field(default_factory=dict)


@dataclass
class TabulexNewsItem:
    """A news/notice panel shown on the Tabulex guardian dashboard."""

    title: str
    content: str


@dataclass(frozen=True)
class TabulexAgendaItem:
    """One dated item in the week overview on the guardian dashboard."""

    date: date
    title: str
    time: time | None = None
    time_text: str = ""
    description: str = ""


@dataclass(frozen=True)
class TabulexNavigationItem:
    """One child-scoped section discovered in the guardian side menu."""

    title: str
    path: str
    badge_count: int = 0


@dataclass(frozen=True)
class TabulexCapabilities:
    """Actions exposed by the current guardian installation."""

    can_create_appointment: bool = False
    can_report_sick: bool = False
    can_create_day_off: bool = False
    can_update_activity: bool = False


@dataclass
class TabulexDashboard:
    """Read-only snapshot of the Tabulex guardian dashboard."""

    status: str = ""
    news: list[TabulexNewsItem] = field(default_factory=list)
    week_label: str = ""
    appointments: list[TabulexAgendaItem] = field(default_factory=list)
    birthdays: list[str] = field(default_factory=list)
    birthday_message: str = ""
    galleries: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TabulexAppointmentType:
    """An appointment type offered by the current Tabulex installation."""

    id: str
    name: str


class TabulexRecurrence(str, Enum):
    """Supported appointment recurrence modes."""

    NONE = "none"
    WEEKLY = "weekly"
    EVERY_OTHER_WEEK = "every_other_week"


class TabulexWeekday(IntEnum):
    """Bit values used by the Tabulex appointment form."""

    MONDAY = 1
    TUESDAY = 2
    WEDNESDAY = 4
    THURSDAY = 8
    FRIDAY = 16


@dataclass(frozen=True)
class TabulexAppointmentInput:
    """Values used when creating or updating a Tabulex appointment."""

    date: date
    start_time: time
    kind: str
    pickup: str = ""
    description: str = ""
    before_start_time: time = time(14, 0)
    end_time: time | None = None
    end_date: date | None = None
    recurrence: TabulexRecurrence = TabulexRecurrence.NONE
    weekdays: tuple[TabulexWeekday, ...] = ()
    ignore_on_holiday: bool = True
    transport: str = ""
    owner_id: str = ""

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("kind must not be empty")
        for name, value in (
            ("start_time", self.start_time),
            ("before_start_time", self.before_start_time),
            ("end_time", self.end_time),
        ):
            if value is None:
                continue
            if value.minute not in (0, 15, 30, 45) or value.second or value.microsecond:
                raise ValueError(f"{name} must use 15-minute increments")
        if self.recurrence is TabulexRecurrence.NONE:
            if self.end_date is not None or self.weekdays:
                raise ValueError("Non-recurring appointments cannot have an end date or weekdays")
        elif self.end_date is None or not self.weekdays:
            raise ValueError("Recurring appointments require an end date and weekdays")
        if len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("weekdays must not contain duplicates")


@dataclass(frozen=True)
class TabulexAppointment:
    """A typed appointment from the Tabulex guardian appointment list."""

    date: date
    summary: str
    id: str | None = None
    kind: str = ""
    start_time: time | None = None
    pickup: str = ""
    description: str = ""
    before_start_time: time | None = None
    end_time: time | None = None
    end_date: date | None = None
    recurrence: TabulexRecurrence = TabulexRecurrence.NONE
    weekdays: tuple[TabulexWeekday, ...] = ()
    ignore_on_holiday: bool = True
    transport: str = ""
    owner_id: str = ""

    def as_input(self) -> TabulexAppointmentInput:
        """Copy the complete editable view into an update input."""
        if self.start_time is None:
            raise ValueError("Appointment has no parsed start time")
        return TabulexAppointmentInput(
            date=self.date,
            start_time=self.start_time,
            kind=self.kind,
            pickup=self.pickup,
            description=self.description,
            before_start_time=self.before_start_time or time(14, 0),
            end_time=self.end_time,
            end_date=self.end_date,
            recurrence=self.recurrence,
            weekdays=self.weekdays,
            ignore_on_holiday=self.ignore_on_holiday,
            transport=self.transport,
            owner_id=self.owner_id,
        )


@dataclass(frozen=True)
class TabulexHolidayDay:
    """One date in a holiday-attendance registration period."""

    id: str
    date: date
    attending: bool | None = None
    start_time: time | None = None
    end_time: time | None = None
    joint_care: bool = False


@dataclass(frozen=True)
class TabulexHolidayPeriod:
    """A holiday registration window and its individual dates."""

    title: str
    days: tuple[TabulexHolidayDay, ...] = ()


@dataclass(frozen=True)
class TabulexIdentityPreferences:
    """Non-sensitive visibility preferences from the child's identity card."""

    show_on_public_lists: bool = False
    show_birthday: bool = False
    show_picture_on_info_board: bool = False


@dataclass(frozen=True)
class TabulexOverview:
    """One-request snapshot used as the initial Tabulex POC contract."""

    dashboard: TabulexDashboard
    navigation: tuple[TabulexNavigationItem, ...] = ()
    appointment_types: tuple[TabulexAppointmentType, ...] = ()
    capabilities: TabulexCapabilities = field(default_factory=TabulexCapabilities)


# ---------------------------------------------------------------------------
# Contact book
# ---------------------------------------------------------------------------

@dataclass
class ContactBookNote:
    """A note from the contact book (kontaktbog)."""

    date: str
    author: str
    content: str
    note_id: str = ""
    is_reply: bool = False
    seen_by: str = ""
    profile_image_url: str = ""
    author_initials: str = ""


# ---------------------------------------------------------------------------
# Photos / albums
# ---------------------------------------------------------------------------

@dataclass
class Album:
    """A photo album."""

    id: int
    title: str
    photo_count: int = 0
    url: str = ""
    description: str = ""
    author: str = ""
    cover_image_url: str = ""
    is_unread: bool = False


@dataclass
class Photo:
    """A photo within an album."""

    id: int
    url: str
    thumbnail_url: str = ""
    caption: str = ""


# ---------------------------------------------------------------------------
# Reading contract (læsekontrakt)
# ---------------------------------------------------------------------------

@dataclass
class ReadingContract:
    """A reading contract with a target and progress tracking."""

    id: int
    category: str
    date_range: str
    pages_to_read: int
    progress: int
    is_active: bool
    is_page_used_for_count: bool  # False = minutes, True = pages
    books: list[ReadingContractBook] = field(default_factory=list)
    student_id: int = 0
    is_read_only: bool = False


@dataclass
class ReadingContractBook:
    """A book identified within its contract by its exact title and author."""

    title: str
    author: str
    read_pages_count: int = 0


@dataclass
class ReadingContractEntry:
    """One reading, with its server ID and minutes/pages read.

    The first four fields retain the legacy positional constructor. ``pages``
    contains the amount as text; ``read_pages_count`` is the typed equivalent
    and counts minutes when the contract's ``is_page_used_for_count`` is false.
    """

    date: str
    title: str
    pages: str = ""
    comment: str = ""
    id: int = 0
    contract_id: int = 0
    author: str = ""
    read_pages_count: int = 0


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@dataclass
class Document:
    """A document available for download."""

    name: str
    url: str
    date: str = ""
    category: str = ""
    id: int = 0
    is_unread: bool = False


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

@dataclass
class StudentContact:
    """A student contact card."""

    name: str
    class_name: str = ""
    photo_url: str = ""


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

@dataclass
class ScheduleDay:
    """A day in the weekly schedule."""

    date: datetime
    lessons: list[ScheduleLesson] = field(default_factory=list)


@dataclass
class ScheduleLesson:
    """A single lesson in the schedule."""

    time: str
    subject: str
    teacher: str = ""
    room: str = ""


# ---------------------------------------------------------------------------
# Navigation / menu items
# ---------------------------------------------------------------------------

@dataclass
class MenuItem:
    """A navigation menu item discovered from the frontpage."""

    title: str
    url: str
    badge_count: int = 0
    icon_class: str = ""
