"""Parser for the contact book (kontaktbog) notes page."""

from __future__ import annotations

import re

from ..models import ContactBookNote
from .common import extract_text, make_soup


def parse_contact_book_notes(html: str) -> list[ContactBookNote]:
    """Parse contact book notes from ``/contactbook/notes``.

    Notes are in ``div.sk-contactbook-note-container`` elements, each with
    a header (author, date) and a body (``div.sk-contactbook-note``).
    """
    soup = make_soup(html)
    notes: list[ContactBookNote] = []

    for item in soup.select("div.sk-contactbook-note-container"):
        # Author is the first <span> inside the author div
        author_div = item.select_one("div.sk-news-item-author")
        author = ""
        if author_div:
            first_span = author_div.select_one("span")
            if first_span:
                author = first_span.get_text(strip=True)

        # Date
        date_el = item.select_one("div.sk-contactbook-note-date")
        date = extract_text(date_el) if date_el else ""

        # Note content (HTML body)
        content_el = item.select_one("div.sk-contactbook-note")
        content = content_el.get_text(separator="\n", strip=True) if content_el else ""

        if not content:
            continue

        # Note ID from reply link URL
        note_id = ""
        reply_link = item.select_one("div.sk-contactbook-note-actions a")
        if reply_link:
            href = reply_link.get("href", "")
            id_match = re.search(r"/replynote/(\d+)", href)
            if id_match:
                note_id = id_match.group(1)

        # Is this a reply?
        is_reply = "answer" in item.get("class", [])

        # Seen by
        seen_by = ""
        small = item.select_one("small")
        if small:
            seen_by = small.get_text(strip=True)
            # Strip "Set af: " prefix
            seen_by = re.sub(r"^Set af:\s*", "", seen_by)

        # Profile image (photo URL or initials)
        profile_image_url = ""
        author_initials = ""
        photo_div = item.select_one("div.sk-profile-image-photo")
        if photo_div:
            style = photo_div.get("style", "")
            url_match = re.search(r"url\(['\"]?([^'\")\s]+)['\"]?\)", style)
            if url_match:
                profile_image_url = url_match.group(1)
        initials_el = item.select_one("span.sk-profile-image-icon")
        if initials_el:
            author_initials = initials_el.get_text(strip=True)

        notes.append(ContactBookNote(
            date=date, author=author, content=content,
            note_id=note_id, is_reply=is_reply, seen_by=seen_by,
            profile_image_url=profile_image_url,
            author_initials=author_initials,
        ))

    return notes
