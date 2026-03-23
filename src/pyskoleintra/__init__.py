"""pyskoleintra — Python client for Skoleintra (Danish school intranet).

Provides programmatic access to messages, homework, calendar, weekly plans,
reading contracts, SFO schedules, photos, contacts, documents, and more.

Example::

    from pyskoleintra import Skoleintra

    client = Skoleintra("myschool")
    client.login("username", "password")

    for child in client.children:
        print(f"--- {child.name} ---")
        for hw in child.homework():
            print(f"  {hw.date:%Y-%m-%d} {hw.subject}: {hw.description}")
"""

from .child import Child
from .client import Skoleintra
from .exceptions import (
    AuthenticationError,
    MaintenanceError,
    NetworkError,
    NotAuthorizedError,
    ParseError,
    SessionExpiredError,
    SkoleintraError,
)
from .models import (
    AgendaItem,
    Album,
    Attachment,
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
    ScheduleLesson,
    SfoInfo,
    StudentContact,
    WeeklyPlan,
)

__version__ = "0.1.0"

__all__ = [
    # Main classes
    "Skoleintra",
    "Child",
    # Models
    "AgendaItem",
    "Album",
    "Attachment",
    "CalendarEvent",
    "ChildInfo",
    "ContactBookNote",
    "Document",
    "HomeworkEntry",
    "MenuItem",
    "MessageDetail",
    "MessageSummary",
    "MessageThread",
    "Photo",
    "ReadingContract",
    "ReadingContractBook",
    "ReadingContractEntry",
    "ScheduleDay",
    "ScheduleLesson",
    "SfoInfo",
    "StudentContact",
    "WeeklyPlan",
    # Exceptions
    "AuthenticationError",
    "MaintenanceError",
    "NetworkError",
    "NotAuthorizedError",
    "ParseError",
    "SessionExpiredError",
    "SkoleintraError",
]
