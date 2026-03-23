#!/usr/bin/env python3
"""Live integration test for pyskoleintra.

Logs in using credentials from .env and exercises every available endpoint,
printing results as it goes. Useful for verifying the library works against
the real Skoleintra site.

Usage:
    1. Copy .env.example to .env and fill in your credentials
    2. Run: python test_live.py
    3. Run with --cached to re-test parsers from cached responses (no login)
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

# Load .env from same directory as this script
load_dotenv(Path(__file__).parent / ".env")

from pyskoleintra import Skoleintra

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCHOOL = os.environ.get("SKOLEINTRA_SCHOOL", "")
USERNAME = os.environ.get("SKOLEINTRA_USERNAME", "")
PASSWORD = os.environ.get("SKOLEINTRA_PASSWORD", "")
COOKIE_FILE = str(Path(__file__).parent / "cookies.txt")
CACHE_DIR = str(Path(__file__).parent / "tmp" / "response_cache")

# Set to True for verbose HTTP logging
DEBUG = os.environ.get("SKOLEINTRA_DEBUG", "").lower() in ("1", "true", "yes")
CACHED_MODE = "--cached" in sys.argv

if not CACHED_MODE and not all([SCHOOL, USERNAME, PASSWORD]):
    print("Error: Set SKOLEINTRA_SCHOOL, SKOLEINTRA_USERNAME, and SKOLEINTRA_PASSWORD in .env")
    sys.exit(1)


def separator(title: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


def safe_run(label: str, fn, *args, **kwargs):
    """Run a function and catch errors, so one failing endpoint doesn't stop the rest."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        print(f"  [ERROR] {label}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if DEBUG:
        logging.basicConfig(level=logging.DEBUG)

    # -- Login ---------------------------------------------------------------

    separator("Login")
    client = Skoleintra(SCHOOL, cookie_file=COOKIE_FILE, cache_dir=CACHE_DIR)

    if CACHED_MODE:
        # In cached mode, construct a Child directly without logging in
        print("Running in CACHED mode — skipping login, using cached responses")
        from pyskoleintra.child import Child
        from pyskoleintra.models import ChildInfo
        info = ChildInfo(name="Child", parent_id=0, parent_path="/parent/0/Child")
        children = [Child(info, client.base_url, client._http)]
    else:
        # Try resuming from cookies first
        if client.is_authenticated:
            print("Resuming session from cookies...")
            try:
                children = client.resume_session()
                print(f"Session resumed — {len(children)} child(ren)")
            except Exception:
                print("Cookie session expired, logging in fresh...")
                children = client.login(USERNAME, PASSWORD)
        else:
            print(f"Logging in to {SCHOOL}.m.skoleintra.dk ...")
            children = client.login(USERNAME, PASSWORD)

    print(f"Found {len(children)} child(ren):")
    for child in children:
        print(f"  - {child.name} (parent_id={child.parent_id}, path={child.parent_path})")

    # -- Iterate through each child ------------------------------------------

    for child in children:
        separator(f"Child: {child.name}")
        now = datetime.now()

        # 1. Frontpage menu
        print("--- Frontpage menu ---")
        menu = safe_run("frontpage_menu", child.frontpage_menu)
        if menu:
            for item in menu:
                badge = f" [{item.badge_count}]" if item.badge_count else ""
                print(f"  {item.title}{badge}  ->  {item.url}")

        # 2. Inbox messages (all)
        print("\n--- Inbox ---")
        threads = safe_run("inbox", child.inbox)
        if threads:
            for t in threads:
                unread = " *UNREAD*" if t.is_unread else ""
                attach = " +attach" if t.has_attachments else ""
                print(f"  [{t.date}] {t.subject} ({t.messages_count} msg, from: {t.sender_name}){unread}{attach}")

            # Pagination: load all remaining threads
            all_threads = list(threads)
            while len(threads) >= 10:
                last_id = threads[-1].latest_message_id
                threads = safe_run("inbox page", child.inbox, before_message_id=last_id)
                if not threads:
                    break
                all_threads.extend(threads)
                for t in threads:
                    unread = " *UNREAD*" if t.is_unread else ""
                    print(f"  [{t.date}] {t.subject} ({t.messages_count} msg){unread}")
            print(f"  Total: {len(all_threads)} threads")

        # 3. Unread messages
        print("\n--- Unread messages ---")
        unread = safe_run("unread_messages", child.unread_messages)
        if unread:
            for m in unread:
                print(f"  [{m.date}] {m.sender}: {m.subject} (id={m.id})")

            # Fetch full detail of each unread message
            for m in unread:
                if not m.id:
                    continue
                print(f"\n  --- Message: {m.subject} ---")
                detail = safe_run("message detail", child.message, m.id)
                if detail:
                    print(f"    From: {detail.sender}")
                    print(f"    Date: {detail.date}")
                    print(f"    To: {len(detail.recipients)} recipients")
                    if detail.attachments:
                        print(f"    Attachments:")
                        for att in detail.attachments:
                            print(f"      - {att.name}: {att.url}")
                    content = detail.content.replace("\n", "\n    | ")
                    print(f"    Content:\n    | {content}")
        else:
            print("  No unread messages")

        # 4. Message search
        print("\n--- Message search (query: 'moede') ---")
        search_results = safe_run("search_messages", child.search_messages, "moede")
        if search_results:
            for t in search_results:
                print(f"  [{t.date}] {t.subject} (from: {t.sender_name})")
        else:
            print("  No results")

        # 5. Homework (all entries)
        print("\n--- Homework ---")
        homework = safe_run("homework", child.homework)
        if homework:
            for hw in homework:
                desc = hw.description.replace("\n", " ").strip()
                print(f"  {hw.date:%Y-%m-%d} [{hw.subject}] {desc}")
        else:
            print("  No homework entries")

        # 6. Calendar events
        print("\n--- Calendar events (next 90 days) ---")
        events = safe_run("calendar_events", child.calendar_events,
                          start=now, end=now + timedelta(days=90))
        if events:
            for ev in events:
                all_day = " (all day)" if ev.all_day else ""
                loc = f" @ {ev.location}" if ev.location else ""
                desc = f" — {ev.description}" if ev.description else ""
                print(f"  {ev.start:%Y-%m-%d %H:%M} {ev.title}{all_day}{loc}{desc}")
        else:
            print("  No events")

        # 7. Weekly plans (all)
        print("\n--- Weekly plans ---")
        plans = safe_run("weekly_plans", child.weekly_plans)
        if plans:
            for p in plans:
                if isinstance(p, dict):
                    title = p.get("title", str(p)[:80])
                    url = p.get("url", "")
                    wy = p.get("week_year", "")
                    unread = " *NEW*" if p.get("is_unread") else ""
                    print(f"  {title} [{wy}]{unread}  ->  {url}")
                else:
                    print(f"  {p}")
        else:
            print("  No weekly plans")

        # 8. Specific weekly plan (most recent if available)
        if plans and isinstance(plans[0], dict) and plans[0].get("week_year"):
            wy = plans[0]["week_year"]
            parts = wy.split("-")
            if len(parts) == 2:
                week, year = int(parts[0]), int(parts[1])
                print(f"\n--- Weekly plan detail: {wy} ---")
                plan = safe_run("weekly_plan", child.weekly_plan, week, year)
                if plan:
                    print(f"  Week {plan.week}/{plan.year}")
                    if isinstance(plan.content, dict) and "raw_html" not in plan.content:
                        for k, v in plan.content.items():
                            print(f"    {k}: {str(v)[:120]}")
                    elif isinstance(plan.content, dict) and "raw_html" in plan.content:
                        print(f"  (raw HTML, {len(plan.content['raw_html'])} chars)")
                else:
                    print("  No plan")

        # 9. Reading contracts
        print("\n--- Reading contracts ---")
        contracts = safe_run("reading_contracts", child.reading_contracts)
        if contracts:
            for rc in contracts:
                unit = "pages" if rc.is_page_used_for_count else "min"
                active = "ACTIVE" if rc.is_active else "expired"
                pct = (rc.progress / rc.pages_to_read * 100) if rc.pages_to_read else 0
                print(f"  Contract #{rc.id}: {rc.category} [{active}]")
                print(f"    Period: {rc.date_range}")
                print(f"    Progress: {rc.progress}/{rc.pages_to_read} {unit} ({pct:.0f}%)")
                print(f"    Books ({len(rc.books)}):")
                for book in rc.books:
                    print(f"      - {book.title} by {book.author} ({book.read_pages_count} {unit})")
        else:
            print("  No reading contracts")

        # 10. Contact book (all notes)
        print("\n--- Contact book ---")
        notes = safe_run("contact_book", child.contact_book)
        if notes:
            for note in notes:
                reply_tag = " [reply]" if note.is_reply else ""
                content = note.content.replace("\n", "\n    | ")
                print(f"  [{note.date}] {note.author}{reply_tag} (id={note.note_id})")
                print(f"    | {content}")
                if note.seen_by:
                    print(f"    Seen by: {note.seen_by[:80]}")
                print()
        else:
            print("  No contact book notes")

        # 11. SFO / Tabulex — commented out (Tabulex SSO is a separate external system)
        # sfo = safe_run("sfo", child.sfo)
        # if sfo:
        #     print(f"  Base URL: {sfo.base_url}")
        #     print(f"  Tabulex URL: {sfo.tabulex_url}")

        # 12. Photo albums (all, with photos from each)
        print("--- Photo albums ---")
        albums = safe_run("albums", child.albums)
        if albums:
            for album in albums:
                unread = " *NEW*" if album.is_unread else ""
                print(f"  Album #{album.id}: {album.title}{unread}")
                if album.description:
                    print(f"    Description: {album.description[:100]}")
                if album.author:
                    print(f"    Author: {album.author}")
                if album.cover_image_url:
                    print(f"    Cover: {album.cover_image_url}")

                # Fetch photos for this album
                photos = safe_run(f"album_photos({album.id})", child.album_photos, album.id)
                if photos:
                    print(f"    Photos ({len(photos)}):")
                    for photo in photos[:5]:
                        print(f"      - {photo.caption}: {photo.url}")
                    if len(photos) > 5:
                        print(f"      ... and {len(photos) - 5} more")
                print()
        else:
            print("  No albums")

        # 13. Student contacts (all)
        print("--- Student contacts ---")
        contacts = safe_run("contacts", child.contacts)
        if contacts:
            for c in contacts:
                cls = f" ({c.class_name})" if c.class_name else ""
                print(f"  {c.name}{cls}")
            print(f"  Total: {len(contacts)} students")
        else:
            print("  No contacts")

        # 14. Documents (all)
        print("\n--- Documents ---")
        docs = safe_run("documents", child.documents)
        if docs:
            for doc in docs:
                unread = " *NEW*" if doc.is_unread else ""
                print(f"  [{doc.date}] {doc.name} (id={doc.id}, {doc.category}){unread}")
                print(f"    URL: {doc.url}")
        else:
            print("  No documents")

        # 15. Schedule (current week + next week)
        for week_offset in range(3):
            test_monday = now - timedelta(days=now.weekday()) + timedelta(weeks=week_offset)
            test_monday = test_monday.replace(hour=0, minute=0, second=0, microsecond=0)
            schedule = safe_run("schedule", child.schedule, start=test_monday)
            if schedule:
                print(f"\n--- Schedule (week of {test_monday:%Y-%m-%d}) ---")
                for day in schedule:
                    date_str = day.date.strftime("%A %d. %b %Y") if day.date.year > 1 else "Day"
                    print(f"  {date_str}:")
                    for lesson in day.lessons:
                        teacher = f" ({lesson.teacher})" if lesson.teacher else ""
                        room = f" [{lesson.room}]" if lesson.room else ""
                        print(f"    {lesson.time}  {lesson.subject}{teacher}{room}")

    # -- Summary -------------------------------------------------------------

    separator("Done")
    print("All endpoints tested. Cookie file saved to:", COOKIE_FILE)


if __name__ == "__main__":
    main()
