"""Data models for Skoleintra entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


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


@dataclass
class MessageSummary:
    """A single message entry from the unread or inbox list."""

    id: str | None
    subject: str
    sender: str
    date: str
    unread: bool = False


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
class AgendaItem:
    """A single agenda item from Tabulex SFO schedule."""

    date: datetime
    fields: dict[str, str]  # Dynamic keys like 'time', 'activity', 'note'


@dataclass
class TabulexNewsItem:
    """A news/notice panel shown on the Tabulex guardian dashboard."""

    title: str
    content: str


@dataclass
class TabulexDashboard:
    """Read-only snapshot of the Tabulex guardian dashboard."""

    status: str = ""
    news: list[TabulexNewsItem] = field(default_factory=list)
    week_label: str = ""
    appointments: list[AgendaItem] = field(default_factory=list)
    birthdays: list[str] = field(default_factory=list)
    birthday_message: str = ""
    galleries: list[str] = field(default_factory=list)


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


@dataclass
class ReadingContractBook:
    """A book within a reading contract."""

    title: str
    author: str
    read_pages_count: int = 0


@dataclass
class ReadingContractEntry:
    """Legacy flat entry — kept for backwards compatibility."""

    date: str
    title: str
    pages: str = ""
    comment: str = ""


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
