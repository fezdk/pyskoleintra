"""Parser for the documents page."""

from __future__ import annotations

from ..models import Document
from .common import extract_text, make_soup


def parse_documents(html: str) -> list[Document]:
    """Parse the documents list page at ``/documents/school``.

    Documents are listed in ``sk-documents-grid-row`` divs. Actual files have
    the ``sk-document`` class; folders do not.
    """
    soup = make_soup(html)
    docs: list[Document] = []

    for row in soup.select("div.sk-documents-content-row.sk-document"):
        link = row.select_one("a.sk-documents-row-clickable-area")
        if not link:
            continue

        href = link.get("href", "")
        title_el = row.select_one("span.sk-documents-document-title")
        name = extract_text(title_el) if title_el else ""
        if not name:
            continue

        date_el = row.select_one("div.sk-documents-date-column")
        date = extract_text(date_el) if date_el else ""

        # Category from the URL (school vs class)
        category = ""
        if "/documents/school" in href:
            category = "school"
        elif "/documents/class" in href:
            category = "class"

        # Document ID from the checkbox input
        doc_id = 0
        id_input = row.select_one("input.sk-documents-checkbox[data-documentid]")
        if id_input:
            try:
                doc_id = int(id_input.get("data-documentid", 0))
            except (ValueError, TypeError):
                pass

        # Unread status
        is_unread = "sk-documents-unread-item" in row.get("class", [])

        docs.append(Document(
            name=name, url=href, date=date, category=category,
            id=doc_id, is_unread=is_unread,
        ))

    return docs
