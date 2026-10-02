# pyskoleintra

Python client library for [Skoleintra](https://skoleintra.dk) — the Danish school intranet platform used by many private and free schools (friskoler).

Provides programmatic access to messages, homework, calendar, weekly plans, reading contracts, contact book, SFO/Tabulex dashboards, appointments, holiday attendance and visibility preferences, photos, student contacts, documents, and timetables. Message support includes archiving and deleting individual received messages. Reading-contract support includes listing books and individual readings, plus adding, editing, and deleting one reading at a time.

## Installation

```bash
pip install -e .
```

Requires Python 3.10+. Dependencies: `requests`, `beautifulsoup4`, `lxml`,
`python-dotenv`, and `tzdata` (IANA timezone data, including on Windows).

## Setup

### Finding your school subdomain

The school subdomain is the first part of the URL you use to log in to Skoleintra. When you visit your school's Skoleintra, the address bar will show something like:

```
https://myschool.m.skoleintra.dk/...
```

The subdomain is `myschool` — the part before `.m.skoleintra.dk`. This is the value you pass to `Skoleintra()`.

You can also find it by:
- Checking any Skoleintra link or bookmark from your school
- Asking your school's administration
- Looking in emails from Skoleintra (they often contain links with the subdomain)

### Credentials

Your username and password are the same ones you use to log in to ForaeldreIntra on the web.

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

## Dates and timezone

The timezone is configured once on the client and inherited by all children
and their Tabulex clients. The default is `Europe/Copenhagen`, regardless of
the machine's local timezone:

```python
client = Skoleintra("myschool")  # Europe/Copenhagen, with DST
client = Skoleintra("myschool", source_timezone="Europe/Copenhagen")
client = Skoleintra("myschool", source_timezone="UTC")  # optional override
print(client.source_timezone.key)
```

`source_timezone` accepts an IANA timezone name or a `zoneinfo.ZoneInfo` object.
It defines how local wall times are interpreted and which zone is used when
returning instants. An explicit source offset or Unix timestamp always retains
its actual instant. Invalid timezone names fail when creating the instance.
There are no per-method timezone options.

- Calendar event `start`/`end` are timezone-aware. Calendar and schedule query
  ranges interpret naive `datetime` arguments in the instance timezone; aware
  arguments retain their instant. Ambiguous/nonexistent naive times around a
  DST change raise `ValueError`; supply an aware datetime to disambiguate.
- Schedule lessons are grouped and formatted in that timezone. Default dates
  such as today and the start of the current week also use it.
- `HomeworkEntry.date` and `ScheduleDay.date` retain their legacy `datetime`
  type at local midnight, now timezone-aware. They represent calendar days,
  not evidence of an event occurring at midnight.
- Date-only values (including reading contracts and Tabulex appointments),
  standalone Tabulex wall-clock `time` values, week numbers, and original date
  strings retain their values and types. A date alone cannot be shifted to
  another timezone. Tabulex's yearless agenda uses the original response date
  in the instance timezone as its reference.

**Migration in 0.3.0:** calendar, schedule and homework datetimes no longer
depend on the host timezone and are now aware. Update naive-datetime comparisons
and serializers accordingly. Existing message `.date` strings remain unchanged;
use the new normalized fields below when a machine-readable value is needed.

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

A small `.html.json` sidecar records the original response time and body hash.
Relative dates use that original time (the HTTP `Date` header when valid,
otherwise the actual network-fetch time), never the cache file's mtime or the
date of a later poll. Legacy caches without valid matching metadata still load,
but relative message dates remain unknown and yearless Tabulex agenda entries
are omitted. Refetch those pages to obtain dated responses. Full dates remain
usable without metadata.

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

#### Normalized dates

`MessageThread`, `MessageSummary`, `MessageDetail`, `ArchivedMessageSummary`,
and `ArchivedMessageDetail` expose these additional fields:

| Field | Type | Meaning |
|---|---|---|
| `date` | `str` | Original display text, unchanged |
| `timestamp` | `datetime \| None` | A known, timezone-aware instant |
| `calendar_date` | `date \| None` | A full known calendar date |
| `date_precision` | `DatePrecision \| None` | `"day"`, `"minute"`, `"second"`, or `"microsecond"`; `None` if unknown |

For example, `"Mandag, 22. jun. 2026 12:26"` becomes a timestamp of
`2026-06-22T12:26:00+02:00` under the default timezone, with minute precision.
Full Danish dates and ISO dates are supported. Valid ISO machine values, where
available (`SentReceivedDate` or HTML `time[datetime]`), take precedence over
display text; unspecified numeric/epoch formats are not guessed.

`"I dag"`/`"I går"` require an original response time. Yearless strings such
as `"22. jun."`, clock-only strings such as `"07:16"`, and invalid dates stay
unknown. Date-only sources never become midnight timestamps. Ambiguous or
nonexistent local message times retain only their known calendar day. There
are no extra detail requests to fill in missing dates during list polling.

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
# From unread list (uses the numeric message ID)
detail = child.message(msg.id)

print(detail.subject)       # "Udflugt fredag"
print(detail.sender)        # "Lars Pedersen (LP)"
print(detail.date)          # "Onsdag, 4. mar. 2026 13:17"
print(detail.content)       # Full message body text
print(len(detail.recipients))  # 25
print(len(detail.attachments)) # 0

for att in detail.attachments:
    print(att.name, att.url)

# From conversation thread (returns list of all messages in the thread)
# Requires both the numeric message ID and the UUID thread ID
messages = child.message(
    t.latest_message_id,
    source="thread",
    thread_id=t.thread_id,
)
for m in messages:
    print(m.sender, m.content[:80])
```

The `message()` method supports three sources:
- `source="unread"` (default) — fetch by numeric message ID from unread list
- `source="outbox"` — fetch from sent messages
- `source="thread"` — fetch all messages in a conversation; requires `thread_id` (UUID from `MessageThread.thread_id`) and `message_id` (numeric from `MessageThread.latest_message_id`)

#### Mark messages read or unread

Message status is changed explicitly with numeric message IDs:

```python
child.mark_messages_read(msg.id)
child.mark_messages_unread([first.id, second.id])

# The generic form is also available:
child.set_messages_read_status(msg.id, read=False)
```

Reading a conversation with `source="thread"` does not change its read status.

`inbox_message(id).is_unread` exposes the exact message's boolean
`ShowUnreadIndication` flag. Missing/non-boolean fields produce `None`, never a
guess from conversation status or absence from a partial unread list. Both true
and false were verified against existing known-state messages with GET-only
reads and unchanged before/after lists; no live status mutation was needed.

Explicit `mark_messages_read` / `mark_messages_unread` calls invalidate the
optional disk cache for this child's message paths on this origin, including
date sidecars and paginated lists. Generation markers persist across sessions
and reject GET responses begun before invalidation. Other children, origins and
calendar data remain cached. Invalidation also happens on an uncertain POST
outcome; the library does not retry the write. Existing cache files are retained
but ignored until refreshed, not globally deleted.

#### Browse the archive

```python
page = child.archived_messages(page=1, query="sommer", refresh=True)
for entry in page.messages:
    print(entry.archive_id, entry.subject, entry.date)

# Explicit pagination, using the same search query:
if page.next_page is not None:
    next_page = child.archived_messages(page=page.next_page, query="sommer")

# Fetch content only when needed:
if page.messages:
    detail = child.archived_message(page.messages[0].archive_id, refresh=True)
    print(detail.content, detail.calendar_date)
```

`archived_messages(*, page=1, query="", refresh=False)` returns an
`ArchivedMessagePage` with `messages`, `page`, and `next_page`. Each call reads
one server page; it does not fetch every page or each message's detail.
`archived_message(archive_id, *, refresh=False)` returns one
`ArchivedMessageDetail`. Both use only GET and support `refresh=True` to bypass
both cache reads and writes. HTTP failures (including 404/500), login pages, or
missing required markup raise `ParseError`; they are not treated as empty data.

Archive IDs belong to a **separate namespace** from inbox IDs. Archive models
expose `archive_id: int` and deliberately have no `.id`. Do not pass archive IDs
to `archive_message`, `delete_message`, or read-status actions. No archive
delete, restore, or unarchive operation is exposed by these read APIs.

Archive dates have **at most day precision**: the inspected archive detail
renders a synthetic `00:00` where the inbox had a real time. A yearless list
date remains unknown; a full date in detail becomes `calendar_date`, with
`timestamp=None` and `date_precision="day"`.

These methods send no read-status action. A live check of two already-read
archive copies used eight GET requests after authentication; archive contents,
visible inbox status, and the unread list were unchanged afterwards. **Whether
opening an unread archive copy implicitly marks it read on the server is not
verified.** Do not present this API as a guarantee that unread archive status is
preserved. Multi-page traversal is covered by synthetic tests; the live archive
used for verification fit on one page.

#### Archive or delete one received message

Use a numeric message ID from an inbox thread or message detail. The fresh
single-message lookup needs no thread UUID and does not mark the message read:

```python
detail = child.inbox_message(reviewed_message_id)
if detail is not None:
    print(detail.subject, detail.sender, detail.date)
    print(detail.is_archived)  # True if a copy has been saved in the archive
```

`inbox_message()` bypasses the development cache. It returns `None` only when
the server explicitly returns JSON `null`; login pages, HTTP errors and
unexpected responses raise `ParseError`. The inspected installation returns
HTTP 500 for a deleted message, so a lookup error alone does not prove absence.

Archive a message after selecting and reviewing its ID. **The default moves
the message out of the inbox and keeps it available in the archive:**

```python
archived = child.archive_message(reviewed_message_id)
assert archived.is_archived is True

# Explicitly keep the original in the inbox instead:
copied = child.archive_message(another_reviewed_message_id, mode="copy")
```

`archive_message(id, *, mode="move")` first saves an archive copy when needed.
It then finds a readable matching copy in the archive, checks its subject,
sender, date, text and attachment links, refreshes the original, and deletes
only that original from the inbox. It checks the archive copy again afterwards.
An existing matching copy is reused, avoiding duplicates. If no matching copy
can be verified (including attachment differences), the original is not deleted.

`mode="copy"` saves a copy and leaves the original in the inbox. It verifies the
fresh archive flag; if that flag was already set, it returns without posting.
Other mode values raise `ValueError` before any request.

**Migration:** commit `9f4adee` introduced `archive_message(id)` as copy-only.
Callers that want that behavior must now pass `mode="copy"`. Callers using the
default should remove the message from their inbox view only after success.

Both modes return a `MessageDetail` snapshot with `is_archived=True` and the
**original inbox message ID**. After a move, it is a snapshot taken before
inbox deletion, not a currently accessible inbox message. The archive copy
has a separate ID in the archive UI; do not pass that ID to `inbox_message()`,
`archive_message()` or `delete_message()`. The inspected archive list and detail
offer deletion of an archive copy, but no unarchive or move-to-inbox action.
The library exposes neither archive-copy deletion nor restoration to the inbox.
Archive detail HTML can display `00:00` instead of the original send time; the
returned snapshot retains the original date string.

Moving uses the separately verified copy and delete operations. The legacy
UI's `movetoarchive` form was observed to move successfully while returning
HTTP 500, and to duplicate a message already copied to the archive. The library
therefore does not use that form.

Deletion is a separate, explicit call:

```python
child.delete_message(reviewed_message_id)
```

This selects exactly one message in the signed-in account's inbox, even when
it belongs to a longer conversation. It requires the message's current UI to
offer the single-message delete action. The request contains one `MessageIds[]`
value and no thread selection. Successful verification requires a fresh lookup
to return `null` unless the server explicitly reports exactly one deleted
message (the observed live response is JSON `1`). A numeric count other than
one raises `ParseError`. The count avoids relying on the server's broken
lookup of deleted IDs. No trash or restore operation was found in the inspected
message UI; an archive copy is not a restore API.

Both mutation methods accept one positive numeric ID (`int` or ASCII decimal
`str`). They currently support received inbox messages only. They do not expose
batch operations, sent-message deletion or archive deletion.
A lookup returning explicit `null` makes mutations raise `ValueError`;
HTTP errors for missing messages still raise `ParseError`. Unsupported
mailbox/delete access raises `NotAuthorizedError`.

The methods bypass the development cache for preflight and verification.
Copy-only and deletion each make at most one POST; a move makes at most one
copy POST and one delete POST. **Moving is not atomic:** failure can leave an
archive copy alongside the original inbox message, or leave deletion uncertain.
They never automatically retry a POST, follow redirects or log in again during
it. A network error or failed verification can mean the operation succeeded:
inspect the web inbox/archive and, when available, `inbox_message(id)` before
retrying. Single-message lookup, deletion, copy-only archiving and default
moving have been checked live. Copying preserved the inbox original and its
read status; repeating the copy call created no duplicate. Moving created one
readable archive copy, removed only the selected inbox message and preserved
the previous archive entries. Failure and partial-result handling are covered
by `tests/test_message_mutations.py`.

#### Search messages

```python
results = child.search_messages("skovtur")

for t in results:
    print(f"[{t.date}] {t.subject}")
```

**Models:** `MessageThread`, `MessageSummary`, `MessageDetail`, `Attachment`,
`ArchivedMessageSummary`, `ArchivedMessageDetail`, `ArchivedMessagePage`.
`DatePrecision` is also exported.

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

#### Minutes and pages

The contract determines the unit: `is_page_used_for_count=False` means minutes;
`True` means pages. Both use the same server fields (`ReadPagesCount`, `Pages`,
and `pagesCount`), so a field name containing "pages" does not itself imply
page counting. For example, `amount=20` records 20 minutes on a minutes-based
contract and 20 pages on a pages-based contract.

There is no unit selector on an individual reading. The library follows the
contract's unit and does not create contracts or change their settings.
Page counting is visible in SkoleIntra's own HTML/JavaScript and contract data
model. Live verification used a minutes-based contract; pages-based behavior
is covered by synthetic tests and has not been verified against a live
pages-based contract.

#### Books and individual readings

Books are identified by their exact **title and author within a contract**; the
API does not expose a separate book ID. Each individual reading has a numeric ID.

| Method on `Child` | Result |
|---|---|
| `reading_contracts(refresh=False)` | Contracts, book totals, units, and read-only status |
| `reading_contract_books(contract_id)` | Books already registered in that contract |
| `reading_contract_entries(contract_id, title=..., author=...)` | Individual readings with IDs, dates, and amounts |
| `add_reading_contract_entry(contract_id, title=..., author=..., amount=...)` | The verified new `ReadingContractEntry` |
| `update_reading_contract_entry(entry, amount=...)` | A new `ReadingContractEntry` with the verified updated amount |
| `delete_reading_contract_entry(entry)` | `None`, after verifying deletion of that entry |

```python
contract = next(rc for rc in child.reading_contracts(refresh=True) if rc.is_active)
books = child.reading_contract_books(contract.id)
book = books[0]

entries = child.reading_contract_entries(
    contract.id, title=book.title, author=book.author,
)
for entry in entries:
    print(entry.id, entry.date, entry.read_pages_count)
```

#### Add, edit, or delete one reading

```python
# One registration for today: minutes if is_page_used_for_count is False,
# otherwise pages. Reuse the exact title/author to keep using the same book.
entry = child.add_reading_contract_entry(
    contract.id, title=book.title, author=book.author, amount=20,
)
print(entry.id)  # The newly created reading's server ID

# Set this registration to 25 minutes/pages, preserving its ID and date.
# Keep the returned object: the earlier entry still contains the old amount.
entry = child.update_reading_contract_entry(entry, amount=25)

# Explicitly delete only this registration, for example to clean up a test.
child.delete_reading_contract_entry(entry)
```

To start a new book, pass its title and author to `add_reading_contract_entry`.
One call creates one reading; there is no batch mutation. `amount` must be an
integer from 1 to 999 when creating a reading. The server assigns today's date,
as in the web form. Backdating and changing a reading's book are not supported.

`update_reading_contract_entry(entry, amount=...)` sets the new amount for that
single registration, rather than adding to its previous amount. Editing accepts
0–999; zero retains the registration. To edit an older reading, pass its object
from `reading_contract_entries`. The method returns a new verified entry and
leaves your input object unchanged. An identical amount is checked against fresh
data and returned without a POST.

Creation compares fresh before/after entry IDs and returns the new entry only
when the result is unambiguous. Editing and deletion take a `ReadingContractEntry`
from creation, editing, or a fresh entry listing and check its contract, book, ID,
date, and amount before posting. Editing verifies the new amount and preserved
identity; deletion verifies that the entry is gone. Both verify that the other
existing entries in that book are unchanged. Read-only contracts are rejected
locally. These checks can detect stale data but are not a server-side lock: avoid
simultaneous edits of the same reading from multiple clients.

Mutations are **never automatically retried**. If a network or verification
error occurs, the server may already have applied the change; fetch fresh
entries before deciding whether to retry. The book/entry methods and mutation
checks bypass the optional development response cache. Use
`reading_contracts(refresh=True)` to refresh the contract overview too.

**Models:** `ReadingContract`, `ReadingContractBook`, `ReadingContractEntry`

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
from dataclasses import replace
from datetime import date, time

from pyskoleintra import (
    TabulexAppointmentInput,
    TabulexRecurrence,
    TabulexWeekday,
)

sfo = child.sfo()

print(sfo.base_url)           # "https://..."
print(sfo.tabulex_url)        # "/Tabulex/..."
print(sfo.front_page_posting) # "Velkommen til SFO..."
print(sfo.notice_board)
print(sfo.weekly_plan)
print(sfo.news)
print(sfo.shortcuts)

# Tabulex owns its own SSO navigation, routes, parsers, and models.
tabulex = child.tabulex
overview = tabulex.overview()  # One landing-page request
dashboard = overview.dashboard
print(dashboard.status)
print(dashboard.news)
print(dashboard.birthdays)
for item in dashboard.appointments:
    print(item.date, item.time, item.title)

for section in overview.navigation:
    print(section.title, section.path, section.badge_count)

print(overview.capabilities.can_create_appointment)
print(overview.capabilities.can_report_sick)
print(overview.capabilities.can_create_day_off)

# Appointment types are discovered from the current installation.
for appointment_type in overview.appointment_types:
    print(appointment_type.id, appointment_type.name)

# Read current holiday-attendance registrations.
for period in tabulex.holiday_periods():
    print(period.title)
    for day in period.days:
        print(day.date, day.attending, day.start_time, day.end_time)

# Read only the non-sensitive visibility settings needed by integrations.
preferences = tabulex.identity_preferences()
print(preferences.show_birthday)

appointments = tabulex.appointments()
appointment_id = appointments[0].id

appointment = TabulexAppointmentInput(
    date=date(2026, 8, 20),
    start_time=time(14, 30),
    kind="Hentes",
    pickup="Forælder",
)
tabulex.create_appointment(appointment)

# Start from the parsed edit view so omitted fields are preserved.
current = tabulex.appointment(appointment_id)
updated = replace(current.as_input(), start_time=time(15, 0))
tabulex.update_appointment(
    appointment_id,
    updated,
)

# Applications can fetch a typed preview before applying their own policy.
print(tabulex.appointment(appointment_id))
tabulex.delete_appointment(appointment_id)

# The complete appointment modal is represented when recurrence is needed.
recurring = TabulexAppointmentInput(
    date=date(2026, 8, 20),
    start_time=time(15, 0),
    kind="Hentes",
    end_date=date(2026, 9, 30),
    recurrence=TabulexRecurrence.WEEKLY,
    weekdays=(TabulexWeekday.THURSDAY,),
    ignore_on_holiday=True,
)
```

**POC read model:** `TabulexOverview` combines the dashboard, dynamically discovered navigation, appointment types, and supported-action flags from one response. Separate read methods expose holiday periods and the three non-sensitive identity-card visibility preferences.

**Write semantics:** The library performs a requested write without interactive confirmation. Applications are responsible for preview and confirmation policy. Appointment IDs are validated as numeric, and update payloads cannot override the ID passed to `update_appointment()`.

Only appointment create/update/delete writes are implemented. Holiday attendance, day off, sickness, activity, and identity-card operations remain read-only until their POST contracts have been separately approved and tested. Capability flags describe what the upstream UI exposes; they do not perform an action.

**Parser notes:** The SFO page involves a complex redirect chain (integration -> external site -> SAML form submissions). Only forms containing known identity-provider fields are auto-submitted; ordinary SFOweb forms are never treated as redirects. The Tabulex URL, including its installation-specific query parameters, is discovered dynamically rather than hardcoded.

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
  child.py             # Child — per-child Skoleintra data access
  tabulex.py           # Tabulex — child-scoped IST SFO client
  sso.py               # Shared SAML/WS-Federation form handling
  http.py              # HttpSession — requests wrapper, cookies, caching
  auth.py              # SAML/SSO authentication flow
  models.py            # Shared and Tabulex domain models
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
    sfo.py             # Legacy Infoweb SFO front page
    tabulex.py         # Tabulex dashboard, appointments, holidays, preferences
```

### Request flow

```
User code -> Child/Tabulex method -> HttpSession.get(url) -> upstream server
                                    |
                              [cache check]
                                    |
                              Parser.parse(html/json) -> Model dataclass
```

1. `Child` and `Tabulex` methods build the full URL and call `HttpSession.get()`
2. `HttpSession` handles cookies, redirects, auto-relogin on 302, and optional caching
3. The raw HTML/JSON response is passed to the appropriate parser
4. Parsers return typed dataclass instances

Reading mutations discover their POST endpoints from the child's reading-contract
page. Each mutation uses fresh reads before and after its single POST to validate
the target and verify the result. `HttpSession.get(..., use_cache=False)` bypasses
both reading and writing the optional response cache for these checks.

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

## Tests

Install the development dependencies and run the offline test suite:

```bash
pip install -e '.[dev]'
python -m pytest tests -q
```

The tests in `tests/` use synthetic data and do not require credentials or contact
SkoleIntra. Reading-contract tests cover request payloads, entry IDs, minutes and
pages, amount validation, stale entries, read-only contracts, cache bypass,
uncertain writes, and preservation of other existing readings.

Message tests cover normalized dates, DST gaps/folds, original cache context,
archive pagination and ID isolation, error handling, and GET-only archive reads.
Timezone tests cover inheritance, host independence, calendar/schedule output,
query ranges across DST, and Tabulex's reference date. Pytest discovery is
restricted to `tests/` so private manual live scripts are never collected.

`test_live.py` is a separate manual script that logs in using `.env`; it is not
part of the offline suite. It does not automatically run reading mutations.
Manual live validation of the new reading methods covered adding to an existing
book and a new book, editing the same entry from 1 to 2 to 0 minutes, and deleting
only the created test entries. Original readings and contract summaries were
compared before and after cleanup. Future live mutation tests should create one
temporary entry at a time, retain its returned ID, and only clean up that entry.

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
| `is_archived` | `bool \| None` | Whether a copy is in the archive; `None` when the source does not expose the flag |
| `is_outbox` | `bool \| None` | Whether this is a sent message; `None` when the source does not expose the flag |

The three message models above also include `timestamp`, `calendar_date`, and
`date_precision` as described in [Normalized dates](#normalized-dates).

### `ArchivedMessageSummary`, `ArchivedMessageDetail`, `ArchivedMessagePage`

| Model | Fields |
|---|---|
| `ArchivedMessageSummary` | `archive_id: int`, `subject: str`, `sender: str`, `date: str`, and the three normalized date fields |
| `ArchivedMessageDetail` | All summary fields, plus `content: str`, `recipients: list[str]`, `attachments: list[Attachment]` |
| `ArchivedMessagePage` | `messages: list[ArchivedMessageSummary]`, `page: int`, `next_page: int \| None` |

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
| `student_id` | `int` | Student ID used by the reading mutation endpoints |
| `is_read_only` | `bool` | Whether the server marks readings in this contract read-only |

### `ReadingContractBook`

| Field | Type | Description |
|---|---|---|
| `title` | `str` | Book title |
| `author` | `str` | Author name |
| `read_pages_count` | `int` | Total minutes (or pages) read for this book — sum of all individual reading records |

### `ReadingContractEntry`

| Field | Type | Description |
|---|---|---|
| `id` | `int` | Server ID of this individual reading |
| `contract_id` | `int` | Contract containing this reading |
| `title` | `str` | Exact book title |
| `author` | `str` | Exact book author |
| `date` | `str` | Server-formatted reading date |
| `read_pages_count` | `int` | Minutes or pages read in this registration |
| `pages` | `str` | Legacy text representation of the same amount |
| `comment` | `str` | Legacy field; not provided by the reading API |

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
| `notice_board` | `str` | Text from the Infoweb notice board |
| `weekly_plan` | `str` | Text from the Infoweb weekly-plan section |
| `news` | `str` | Text from the Infoweb SFO news section |
| `shortcuts` | `dict[str, str]` | Named links discovered on the SFO front page |

### `TabulexOverview`

| Field | Type | Description |
|---|---|---|
| `dashboard` | `TabulexDashboard` | Status, notices, week overview, birthdays, and galleries |
| `navigation` | `tuple[TabulexNavigationItem, ...]` | Guardian sections discovered from the live side menu |
| `appointment_types` | `tuple[TabulexAppointmentType, ...]` | Installation-specific appointment choices |
| `capabilities` | `TabulexCapabilities` | Whether the live UI exposes appointment, sick, day-off, and activity actions |

### `TabulexDashboard`

| Field | Type | Description |
|---|---|---|
| `status` | `str` | Current child status shown by Tabulex |
| `news` | `list[TabulexNewsItem]` | News and notice panels |
| `week_label` | `str` | Label for the displayed week |
| `appointments` | `list[TabulexAgendaItem]` | Typed entries shown in the week overview |
| `birthdays` | `list[str]` | Birthday entries, when present |
| `birthday_message` | `str` | Full text from the birthday panel |
| `galleries` | `list[str]` | Gallery labels shown on the dashboard |

### `TabulexNewsItem`

| Field | Type | Description |
|---|---|---|
| `title` | `str` | Panel title |
| `content` | `str` | Panel text |

### `TabulexAgendaItem`

| Field | Type | Description |
|---|---|---|
| `date` | `date` | Agenda date, with year inferred from the displayed week |
| `title` | `str` | Main appointment/activity text |
| `time` | `time \| None` | Parsed clock time when the page contains one |
| `time_text` | `str` | Original time label, including non-clock labels |
| `description` | `str` | Additional agenda fields flattened as text |

### `TabulexAppointment`

| Field | Type | Description |
|---|---|---|
| `date` | `date` | Appointment date |
| `summary` | `str` | Complete human-readable row text |
| `id` | `str \| None` | Numeric Tabulex appointment ID when exposed by the page |
| `kind` | `str` | Appointment type, such as `"Hentes"` or `"Gå hjem"` |
| `start_time` | `time \| None` | Appointment time when present |
| `pickup` | `str` | Person or description following `"af"` |
| `description` | `str` | Free-text description from the edit view |
| `before_start_time` | `time \| None` | Before-start time from the edit view |
| `end_time` | `time \| None` | Optional end time |
| `end_date` | `date \| None` | Recurrence end date |
| `recurrence` | `TabulexRecurrence` | None, weekly, or every other week |
| `weekdays` | `tuple[TabulexWeekday, ...]` | Selected recurrence weekdays |
| `ignore_on_holiday` | `bool` | Whether recurring entries are skipped during holidays |
| `transport` | `str` | Optional transport value |
| `owner_id` | `str` | Optional playdate/owner actor ID |

`as_input()` copies every parsed edit value into a `TabulexAppointmentInput`. This is the safe basis for a UI preview and partial edit with `dataclasses.replace()`.

### `TabulexAppointmentInput`

| Field | Type | Description |
|---|---|---|
| `date` | `date` | Appointment date |
| `start_time` | `time` | Appointment time in a 15-minute increment |
| `kind` | `str` | Name of a type returned by `appointment_types()` |
| `pickup` | `str` | Optional pickup person or description |
| `description` | `str` | Optional free-text description |
| `before_start_time` | `time` | Tabulex before-start time, defaulting to 14:00 |
| `end_time` | `time \| None` | Optional end time in a 15-minute increment |
| `end_date` | `date \| None` | Required end date for recurring appointments |
| `recurrence` | `TabulexRecurrence` | Recurrence mode; defaults to none |
| `weekdays` | `tuple[TabulexWeekday, ...]` | Required weekdays for recurrence |
| `ignore_on_holiday` | `bool` | Skip recurrence during holidays; defaults to true |
| `transport` | `str` | Optional transport value |
| `owner_id` | `str` | Optional selected playdate/owner actor ID |

### `TabulexAppointmentType`

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Installation-specific Tabulex type ID |
| `name` | `str` | Display name used when constructing an appointment |

### `TabulexHolidayPeriod` / `TabulexHolidayDay`

| Field | Type | Description |
|---|---|---|
| `title` | `str` | Holiday registration title, such as a named school holiday |
| `days` | `tuple[TabulexHolidayDay, ...]` | Individual registration dates in the period |
| `TabulexHolidayDay.id` | `str` | Upstream day ID needed by a future approved write contract |
| `TabulexHolidayDay.date` | `date` | Registration date |
| `TabulexHolidayDay.attending` | `bool \| None` | True for attending, false for free, none when unanswered |
| `TabulexHolidayDay.start_time` | `time \| None` | Registered/available arrival time |
| `TabulexHolidayDay.end_time` | `time \| None` | Registered/available departure time |
| `TabulexHolidayDay.joint_care` | `bool` | Whether the date uses joint care |

### `TabulexIdentityPreferences`

| Field | Type | Description |
|---|---|---|
| `show_on_public_lists` | `bool` | Child visibility on public lists |
| `show_birthday` | `bool` | Birthday visibility on the dashboard |
| `show_picture_on_info_board` | `bool` | Picture visibility on the information board |

No phone numbers, email addresses, contact data, free-text permissions, or image fields are returned by `identity_preferences()`.

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
