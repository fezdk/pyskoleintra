"""Parser for the student contacts (kontakt) page."""

from __future__ import annotations

import re

from ..models import StudentContact
from .common import make_soup


def parse_student_contacts(html: str) -> list[StudentContact]:
    """Parse student contacts from ``/contacts/students/cards``.

    The actual contact cards are loaded via AJAX, but the page contains a
    ``<select>`` dropdown with all student names and their URLs.
    """
    soup = make_soup(html)
    contacts: list[StudentContact] = []

    dropdown = soup.select_one("select#sk-toolbar-contact-dropdown")
    if not dropdown:
        return contacts

    for option in dropdown.select("option"):
        name = option.get_text(strip=True)
        value = option.get("value", "")
        if not name or not value:
            continue

        # Extract class name from the page heading if available
        class_name = ""
        heading = soup.select_one("h2.h-ta-c")
        if heading:
            class_name = heading.get_text(strip=True)

        contacts.append(StudentContact(
            name=name, class_name=class_name, photo_url="",
        ))

    return contacts
