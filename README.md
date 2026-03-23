# pyskoleintra

Python client library for [Skoleintra](https://skoleintra.dk) — the Danish school intranet platform used by many private and free schools (friskoler).

Provides programmatic access to messages, homework, calendar, weekly plans, reading contracts, contact book, SFO schedules, photos, student contacts, documents, and timetables.

## Installation

```bash
pip install -e .
```

Requires Python 3.10+. Dependencies: `requests`, `beautifulsoup4`, `lxml`.

## Quick start

```python
from pyskoleintra import Skoleintra

client = Skoleintra("myschool")
children = client.login("username", "password")

for child in children:
    print(f"--- {child.name} ---")

    for hw in child.homework():
        print(f"  {hw.date:%Y-%m-%d} {hw.subject}: {hw.description}")
```

## Authentication & session persistence

```python
from pyskoleintra import Skoleintra

# Pass cookie_file to persist session across runs
client = Skoleintra("myschool", cookie_file="cookies.txt")

# First run — full login (SAML/SSO flow)
children = client.login("username", "password")

# Subsequent runs — resume from cookies (no re-login)
if client.is_authenticated:
    children = client.resume_session()
else:
    children = client.login("username", "password")
```

The library handles the full SAML/SSO authentication flow automatically. Session cookies are saved in Netscape format and restored on subsequent runs. If a session expires mid-request, the library can automatically re-authenticate using stored credentials.

### Response caching (for development)

When developing parsers or debugging, you can cache all HTTP responses to disk to avoid hitting the server repeatedly:

```python
client = Skoleintra("myschool", cookie_file="cookies.txt", cache_dir="tmp/cache")
```

This saves every GET response as an HTML file. Subsequent runs serve from cache. Auth/SSO URLs are automatically excluded from caching.

---

## Endpoints & data models

Each child provides methods to access every data source on Skoleintra. Below is a comprehensive reference for every endpoint, its return type, and code examples.

### Children

After login, iterate through children on the account:

```python
children = client.login("user", "pass")

for child in children:
    print(child.name)        # "Oliver"
    print(child.parent_id)   # 2345
    print(child.parent_path) # "/parent/2345/Oliver"

# Look up a specific child by name
oliver = client.child("Oliver")
```

---

### Messages

#### Inbox threads

```python
threads = child.inbox()

for t in threads:
    print(t.subject)              # "Matematiklektier"
    print(t.date)                 # "07:16" or "20. mar."
    print(t.sender_name)          # "Mette Hansen (MH)"
    print(t.messages_count)       # 1
    print(t.is_unread)            # False
    print(t.has_attachments)      # False
    print(t.is_replied)           # False
    print(t.is_forwarded)         # False
    print(t.thread_participants)  # "Mette Hansen (MH)"
    print(t.unread_messages_count)# 0
    print(t.thread_id)            # "a1b2c3d4-e5f6-..."
    print(t.latest_message_id)    # 700422
    print(t.profile_image_url)    # "/file/common/Portraetfotos/MH.jpg"
```

**Pagination** — pass the last message ID to load older threads:

```python
threads = child.inbox()
if len(threads) >= 10:
    older = child.inbox(before_message_id=threads[-1].latest_message_id)
```

#### Unread messages

```python
unread = child.unread_messages()

for msg in unread:
    print(msg.id)       # "697137"
    print(msg.subject)  # "Udflugt fredag"
    print(msg.sender)   # "Lars Pedersen (LP)"
    print(msg.date)     # "4. mar."
    print(msg.unread)   # True
```

#### Message detail

```python
# From unread list
detail = child.message(msg.id)

print(detail.subject)       # "Udflugt fredag"
print(detail.sender)        # "Lars Pedersen (LP)"
print(detail.date)          # "Onsdag, 4. mar. 2026 13:17"
print(detail.content)       # Full message body text
print(len(detail.recipients))  # 25
print(len(detail.attachments)) # 0

for att in detail.attachments:
    print(att.name, att.url)

# From conversation thread (returns list of all messages)
messages = child.message(thread_id, source="thread")
for m in messages:
    print(m.sender, m.content[:80])
```

#### Search messages

```python
results = child.search_messages("skovtur")

for t in results:
    print(f"[{t.date}] {t.subject}")
```

**Models:** `MessageThread`, `MessageSummary`, `MessageDetail`, `Attachment`

---

### Homework

Homework entries come from the diary (dagbog) system:

```python
entries = child.homework()

for hw in entries:
    print(hw.date)         # datetime(2026, 3, 27, ...)
    print(hw.subject)      # "DANSK"
    print(hw.description)  # "HUSK laesning og laeseregistrering..."
```

**Model:** `HomeworkEntry` — `date: datetime`, `subject: str`, `description: str`

**Parser notes:** The homework page first loads `/diaries/list` to discover the diary URL (extracted via regex from the HTML), then fetches the actual diary page. Entries are parsed from `#sk-diary-notes-container` with dates in Danish format ("Mandag 26 maj 2025").

---

### Calendar events

```python
from datetime import datetime, timedelta

events = child.calendar_events(
    start=datetime(2026, 3, 1),
    end=datetime(2026, 4, 30),
)

for ev in events:
    print(ev.title)       # "Paaskeferie"
    print(ev.start)       # datetime(2026, 3, 30, ...)
    print(ev.end)         # datetime(2026, 4, 6, ...)
    print(ev.all_day)     # True
    print(ev.description) # ""
    print(ev.location)    # ""
    print(ev.id)          # "12345"
```

Defaults to the next 30 days if no range is given.

**Model:** `CalendarEvent`

**Parser notes:** This endpoint returns pure JSON from `/calendareventsource/SchoolEvents`. The parser handles both lowercase and PascalCase key variants. Dates are ISO 8601.

---

### Weekly plans

```python
# List available plans
plans = child.weekly_plans()

# Fetch a specific week
plan = child.weekly_plan(week=13, year=2026)

print(plan.week)     # 13
print(plan.year)     # 2026
print(plan.content)  # dict — structure varies by school
```

**Model:** `WeeklyPlan` — `week: int`, `year: int`, `content: dict`

**Parser notes:** The plans list page stores data in a `data-clientlogic-settings-WeeklyPlansApp` JSON attribute on the `#root` div. Individual plans may return JSON directly or embed data in HTML attributes.

---

### Reading contracts (laesekontrakt)

Reading contracts track a child's reading progress — books, minutes/pages read, and targets:

```python
contracts = child.reading_contracts()

for rc in contracts:
    print(rc.id)                     # 1845
    print(rc.category)               # "Frilaesning"
    print(rc.date_range)             # "Fra 6. mar. til 27. mar. 2026"
    print(rc.pages_to_read)          # 400 (target)
    print(rc.is_active)              # True
    print(rc.is_page_used_for_count) # False (= minutes, True = pages)

    # The real progress is the sum across books
    unit = "min" if not rc.is_page_used_for_count else "pages"
    total = sum(b.read_pages_count for b in rc.books)
    print(f"Total read: {total} {unit} / {rc.pages_to_read} target")

    for book in rc.books:
        print(book.title)            # "Pippi Langstroempe"
        print(book.author)           # "Astrid Lindgren"
        print(book.read_pages_count) # 120 (minutes or pages read for this book)
```

**Models:** `ReadingContract`, `ReadingContractBook`

**Parser notes:** This page is a Vue.js SPA — the HTML contains no reading data. The parser extracts the API URL from a `data-clientlogic-settings-ReadingContracts` JSON attribute on `#sk-reading-contracts`, then calls the AJAX endpoint (`/readingcontracts/GetStudentReadingContracts`) which returns JSON. Note: BeautifulSoup/lxml lowercases HTML attributes, so the attribute must be queried in lowercase.

The `read_pages_count` on each book is the **total minutes (or pages) read for that book**, verified by summing individual reading records from the `GetRecordsForBook` endpoint. The contract-level `progress` field is a server-reported value that may not match the book totals.

---

### Contact book (kontaktbog)

Notes exchanged between parents and teachers:

```python
notes = child.contact_book()

for note in notes:
    print(note.date)              # "24. nov. 2025"
    print(note.author)            # "Sofie Jensen"
    print(note.content)           # "Godmorgen\nOliver er syg i dag..."
    print(note.note_id)           # "19961"
    print(note.is_reply)          # False
    print(note.seen_by)           # "Anders Nielsen, Bente Larsen..."
    print(note.profile_image_url) # "/file/parent/profile/photo/1037_..."
    print(note.author_initials)   # "SJ" (when no photo)
```

**Model:** `ContactBookNote`

**Parser notes:** Notes are in `div.sk-contactbook-note-container` elements. Replies have the CSS class `answer`. The author is the first `<span>` inside `div.sk-news-item-author`. The note ID is extracted from the reply link URL (`/contactbook/notes/replynote/{id}`). Profile images are either in a `div.sk-profile-image-photo` (background-image style) or as initials in `span.sk-profile-image-icon`.

---

### Photo albums

```python
albums = child.albums()

for album in albums:
    print(album.id)              # 186
    print(album.title)           # "Skovtur i Dyrehaven"
    print(album.description)     # "3.A tog paa skovtur..."
    print(album.author)          # "Mette Hansen"
    print(album.cover_image_url) # "/file/photoalbum/186/IMG_3820.jpeg?t=..."
    print(album.is_unread)       # True
    print(album.url)             # "/parent/2345/Oliver/photos/albums/album/photos/186"

# Fetch photos from an album
photos = child.album_photos(album.id)
for photo in photos:
    print(photo.id)            # 4523
    print(photo.url)           # Full-size image URL
    print(photo.thumbnail_url) # Thumbnail URL
    print(photo.caption)       # ""
```

**Models:** `Album`, `Photo`

**Parser notes:** Albums are listed as `<li>` items containing `a.sk-photoalbums-list-item` links. Album IDs come from the URL pattern `/album/photos/{id}`. Cover images are embedded as CSS `background: url(...)` in the cover div. Photo count is not available from the list page. The page uses "Vis flere..." (Show more) button for pagination via AJAX.

---

### Student contacts

```python
contacts = child.contacts()

for c in contacts:
    print(c.name)       # "Oliver Andersen"
    print(c.class_name) # ""
    print(c.photo_url)  # ""
```

**Model:** `StudentContact`

**Parser notes:** The contact cards are loaded dynamically via AJAX (the `#sk-contact-card-container` div is empty in the HTML). The parser falls back to extracting student names from the `select#sk-toolbar-contact-dropdown` dropdown. Some names may be truncated with "..." in the dropdown. Full details (photo, class, parent info) would require fetching individual student AJAX endpoints (`/contacts/students/{id}`).

---

### Documents

```python
docs = child.documents()

for doc in docs:
    print(doc.name)      # "Foraeldre-haandbog 2025-2026.pdf"
    print(doc.url)       # "/parent/2345/Oliver/documents/school/48"
    print(doc.date)      # "14. aug. 2025"
    print(doc.category)  # "school" or "class"
    print(doc.id)        # 48
    print(doc.is_unread) # True
```

**Model:** `Document`

**Parser notes:** Documents are in `div.sk-documents-content-row.sk-document` elements (folders lack the `sk-document` class). The document title is in `span.sk-documents-document-title`, date in `div.sk-documents-date-column`, and the download link in `a.sk-documents-row-clickable-area`. Document IDs come from `input.sk-documents-checkbox[data-documentid]`. Unread status is indicated by the `sk-documents-unread-item` class.

---

### Schedule (timetable)

```python
days = child.schedule()                       # Current week
days = child.schedule(week_start="2026-03-23") # Specific week

for day in days:
    print(day.date)  # datetime
    for lesson in day.lessons:
        print(lesson.time)     # "08:00-08:45"
        print(lesson.subject)  # "Dansk"
        print(lesson.teacher)  # "MH"
        print(lesson.room)     # "3.A"
```

**Models:** `ScheduleDay`, `ScheduleLesson`

**Parser notes:** The schedule endpoint (`/schedules/schedule/scheme`) returns an HTML table with one column per weekday. The parser extracts lessons column-wise from `tbody` rows.

---

### SFO & Tabulex

```python
sfo = child.sfo()

print(sfo.base_url)           # "https://..."
print(sfo.tabulex_url)        # "/Tabulex/..."
print(sfo.front_page_posting) # "Velkommen til SFO..."

# Fetch the Tabulex SFO schedule
agenda = child.tabulex_agenda()
for item in agenda:
    print(item.date)    # datetime
    print(item.fields)  # {"time": "14:00", "activity": "Frileg"}
```

**Models:** `SfoInfo`, `AgendaItem`

**Parser notes:** The SFO page involves a complex redirect chain (integration -> external site -> SAML form submissions). Tabulex has its own SAML flow with up to 3 form submission rounds. The agenda is an HTML table with `tr.day` (date headers) and `tr.info` (field rows) elements.

---

### Frontpage menu

```python
menu = child.frontpage_menu()

for item in menu:
    print(item.title)       # "Beskeder"
    print(item.url)         # "/parent/2345/Oliver/messages/conversations"
    print(item.badge_count) # 3
```

**Model:** `MenuItem`

---

## Architecture

```
pyskoleintra/
  __init__.py          # Public API exports
  client.py            # Skoleintra — auth, session, child discovery
  child.py             # Child — per-child data access (all endpoints)
  http.py              # HttpSession — requests wrapper, cookies, caching
  auth.py              # SAML/SSO authentication flow
  models.py            # All dataclasses (20+ models)
  exceptions.py        # Exception hierarchy
  parsers/
    common.py          # Shared utilities (BeautifulSoup, Danish dates, etc.)
    messages.py        # Inbox, unread, message detail, search
    homework.py        # Diary / homework entries
    calendar.py        # Calendar events (JSON)
    weekly_plans.py    # Weekly plans (JSON in HTML attributes)
    reading_contract.py# Reading contracts (Vue.js SPA + AJAX)
    contact_book.py    # Contact book notes
    photos.py          # Photo albums and photos
    contacts.py        # Student contacts (dropdown fallback)
    documents.py       # School documents
    schedule.py        # Weekly timetable
    frontpage.py       # Navigation menu, child discovery
    sfo.py             # SFO + Tabulex integration
```

### Request flow

```
User code -> Child.method() -> HttpSession.get(url) -> Skoleintra server
                                    |
                              [cache check]
                                    |
                              Parser.parse(html/json) -> Model dataclass
```

1. `Child` methods build the full URL and call `HttpSession.get()`
2. `HttpSession` handles cookies, redirects, auto-relogin on 302, and optional caching
3. The raw HTML/JSON response is passed to the appropriate parser
4. Parsers return typed dataclass instances

### Parsing strategies

The site uses several different rendering approaches, each requiring a different parsing strategy:

| Strategy | Used by | How it works |
|---|---|---|
| **JSON in HTML attribute** | Messages, Weekly plans | Data embedded in `data-clientlogic-settings-*` attributes, parsed with `json.loads()` |
| **Pure JSON endpoint** | Calendar | Endpoint returns JSON array directly |
| **Vue.js SPA + AJAX** | Reading contracts | HTML is a shell; real data fetched from a separate API endpoint discovered from config JSON |
| **Server-rendered HTML** | Documents, Contact book, Homework | Traditional HTML with CSS class selectors |
| **AJAX-loaded content** | Contacts | HTML container is empty; data available only from dropdown or individual AJAX calls |
| **Complex redirect chains** | SFO/Tabulex | Multiple 302 redirects + SAML form submissions before reaching the actual page |

### BeautifulSoup attribute casing

**Important:** BeautifulSoup with the `lxml` parser lowercases all HTML attribute names. When querying attributes like `data-clientlogic-settings-ReadingContracts`, you must use the lowercase form:

```python
# Wrong — won't match
el.get("data-clientlogic-settings-ReadingContracts")

# Correct
el.get("data-clientlogic-settings-readingcontracts")
```

## Exceptions

```python
from pyskoleintra import (
    SkoleintraError,        # Base exception
    AuthenticationError,    # Login failed
    SessionExpiredError,    # Cookie session expired
    NotAuthorizedError,     # Not logged in
    ParseError,             # HTML/JSON parsing failed
    NetworkError,           # HTTP request failed
    MaintenanceError,       # Skoleintra is in maintenance mode
)
```

## All models — full field reference

### `ChildInfo`

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Child's first name |
| `parent_id` | `int` | Numeric parent/child ID on the platform |
| `parent_path` | `str` | URL path prefix for this child (e.g. `/parent/1234/Oliver`) |

### `MessageThread`

| Field | Type | Description |
|---|---|---|
| `thread_id` | `str` | UUID identifying the conversation thread |
| `subject` | `str` | Message subject line |
| `messages_count` | `int` | Number of messages in the thread |
| `latest_message_id` | `int` | ID of the most recent message (used for pagination) |
| `date` | `str` | Display date (e.g. `"07:16"` for today, `"20. mar."` for older) |
| `is_unread` | `bool` | Whether the thread has unread messages |
| `sender_name` | `str` | Name and initials of the sender (e.g. `"Mette Hansen (MH)"`) |
| `profile_image_url` | `str` | URL path to the sender's profile photo |
| `has_attachments` | `bool` | Whether any message in the thread has attachments |
| `is_forwarded` | `bool` | Whether the thread has been forwarded |
| `is_replied` | `bool` | Whether the thread has been replied to |
| `thread_participants` | `str` | Comma-separated names of all participants |
| `unread_messages_count` | `int` | Number of unread messages in the thread |

### `MessageSummary`

| Field | Type | Description |
|---|---|---|
| `id` | `str \| None` | Message ID (for fetching detail) |
| `subject` | `str` | Subject line |
| `sender` | `str` | Sender name with initials |
| `date` | `str` | Display date string |
| `unread` | `bool` | Whether this message is unread |

### `MessageDetail`

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Message ID |
| `subject` | `str` | Subject line |
| `sender` | `str` | Sender name |
| `date` | `str` | Full date string (e.g. `"Onsdag, 4. mar. 2026 13:17"`) |
| `content` | `str` | Full message body text |
| `recipients` | `list[str]` | List of recipient names |
| `attachments` | `list[Attachment]` | Attached files |
| `auto_delete_date` | `str` | When the message will be auto-deleted (if applicable) |

### `Attachment`

| Field | Type | Description |
|---|---|---|
| `name` | `str` | File name |
| `url` | `str` | Download URL path |

### `HomeworkEntry`

| Field | Type | Description |
|---|---|---|
| `date` | `datetime` | Due date |
| `subject` | `str` | School subject (e.g. `"DANSK"`, `"MATEMATIK"`) |
| `description` | `str` | Homework description text |

### `CalendarEvent`

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Event ID |
| `title` | `str` | Event title |
| `start` | `datetime` | Start date/time |
| `end` | `datetime` | End date/time |
| `all_day` | `bool` | Whether this is an all-day event |
| `description` | `str` | Event description |
| `location` | `str` | Event location |

### `WeeklyPlan`

| Field | Type | Description |
|---|---|---|
| `week` | `int` | ISO week number |
| `year` | `int` | Year |
| `content` | `dict` | Plan content (structure varies by school) |

### `ReadingContract`

| Field | Type | Description |
|---|---|---|
| `id` | `int` | Contract ID |
| `category` | `str` | Reading category (e.g. `"Frilaesning"`) |
| `date_range` | `str` | Human-readable date range (e.g. `"Fra 6. mar. til 27. mar. 2026"`) |
| `pages_to_read` | `int` | Target number of pages or minutes |
| `progress` | `int` | Server-reported progress (may be stale — sum `books[].read_pages_count` for accurate total) |
| `is_active` | `bool` | Whether the contract is currently active |
| `is_page_used_for_count` | `bool` | `True` = pages, `False` = minutes |
| `books` | `list[ReadingContractBook]` | Books registered under this contract |

### `ReadingContractBook`

| Field | Type | Description |
|---|---|---|
| `title` | `str` | Book title |
| `author` | `str` | Author name |
| `read_pages_count` | `int` | Total minutes (or pages) read for this book — sum of all individual reading records |

### `ContactBookNote`

| Field | Type | Description |
|---|---|---|
| `date` | `str` | Note date (e.g. `"24. nov. 2025"`) |
| `author` | `str` | Author name |
| `content` | `str` | Note body text (may contain newlines) |
| `note_id` | `str` | Numeric note ID (from reply URL) |
| `is_reply` | `bool` | Whether this note is a reply to another |
| `seen_by` | `str` | Comma-separated names of people who have seen the note |
| `profile_image_url` | `str` | URL path to the author's profile photo |
| `author_initials` | `str` | Author initials (shown when no photo available) |

### `Album`

| Field | Type | Description |
|---|---|---|
| `id` | `int` | Album ID |
| `title` | `str` | Album title |
| `photo_count` | `int` | Number of photos (0 from list page — not available in listing) |
| `url` | `str` | URL path to the album page |
| `description` | `str` | Album description text |
| `author` | `str` | Who created the album |
| `cover_image_url` | `str` | URL path to the album cover image |
| `is_unread` | `bool` | Whether the album is new/unseen |

### `Photo`

| Field | Type | Description |
|---|---|---|
| `id` | `int` | Photo index (sequential within album) |
| `url` | `str` | Full-size image URL path |
| `thumbnail_url` | `str` | Thumbnail image URL path |
| `caption` | `str` | Photo caption / filename |

### `Document`

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Document filename |
| `url` | `str` | Download URL path |
| `date` | `str` | Last modified date (e.g. `"14. aug. 2025"`) |
| `category` | `str` | `"school"` or `"class"` |
| `id` | `int` | Document ID |
| `is_unread` | `bool` | Whether the document is new/unseen |

### `StudentContact`

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Student name (may be truncated with `...` from dropdown) |
| `class_name` | `str` | Class name (empty from list page) |
| `photo_url` | `str` | Photo URL (empty from list page) |

### `ScheduleDay`

| Field | Type | Description |
|---|---|---|
| `date` | `datetime` | The date of this school day |
| `lessons` | `list[ScheduleLesson]` | Lessons for this day, sorted by time |

### `ScheduleLesson`

| Field | Type | Description |
|---|---|---|
| `time` | `str` | Time range (e.g. `"08:00-08:45"`) |
| `subject` | `str` | Subject code (e.g. `"DAN"`, `"MAT"`, `"ENG"`) |
| `teacher` | `str` | Teacher name |
| `room` | `str` | Class/room name |

### `SfoInfo`

| Field | Type | Description |
|---|---|---|
| `base_url` | `str` | Base URL of the SFO/Infoweb system |
| `tabulex_url` | `str \| None` | URL path to the Tabulex SFO page (if available) |
| `front_page_posting` | `str` | Front page content text |

### `AgendaItem`

| Field | Type | Description |
|---|---|---|
| `date` | `datetime` | Date of the agenda entry |
| `fields` | `dict[str, str]` | Dynamic key-value fields (e.g. `{"time": "14:00", "activity": "Frileg"}`) |

### `MenuItem`

| Field | Type | Description |
|---|---|---|
| `title` | `str` | Menu item label (e.g. `"Beskeder"`, `"Kalender"`) |
| `url` | `str` | URL path for this menu item |
| `badge_count` | `int` | Unread/new item count (0 if none) |
| `icon_class` | `str` | CSS icon class |

## References

This library was built using insights from the following projects and resources:

### Existing Skoleintra projects

- **[svalgaard/fskintra](https://github.com/svalgaard/fskintra)** — The most mature Skoleintra scraper (Python 2, mechanize). Converts ForældreIntra content to emails. Modules for frontpage, homework, dialogue, documents, photos, weekplans, contacts, and signup were used as reference for endpoint discovery.
- **[jona799t/SkoleintraSDK](https://github.com/jona799t/SkoleintraSDK)** ([PyPI: `skoleintra`](https://pypi.org/project/skoleintra/)) — Python 3 SDK using `requests` + `BeautifulSoup`. Provided the clearest reference for the SAML/SSO login flow with cookie handling details.
- **[CavaleriDK/skoleintra](https://github.com/CavaleriDK/skoleintra)** — TypeScript/Node.js client. Best documentation of internal endpoints including the calendar JSON endpoint (`/calendareventsource/SchoolEvents`), schedule (`/schedules/schedule/scheme`), and weekly plans data embedded in HTML attributes.

### Aula projects (related platform, used for architectural reference)

- **[scaarup/aula](https://github.com/scaarup/aula)** — Home Assistant integration for Aula (the newer Danish school platform). Comprehensive OAuth 2.0/OIDC + SAML + MitID authentication flow and extensive API documentation.
- **[helmstedt.dk](https://helmstedt.dk/2020/05/et-lille-kig-paa-aulas-api/)** — Morten Helmstedt's blog posts exploring the Aula API, referenced for understanding Danish school platform conventions.

### Original codebase

An earlier PHP proof-of-concept by this project's author served as the primary reference for the SAML login flow, HTML parsing selectors, and endpoint URLs.

## License

MIT
