"""Parser for reading contracts (læsekontrakt) page."""

from __future__ import annotations

import json

from ..models import ReadingContract, ReadingContractBook
from .common import make_soup


def extract_contracts_api_url(html: str) -> str | None:
    """Extract the AJAX API URL for reading contracts from the page HTML.

    The page is a Vue.js SPA that loads data from an API. The URL is embedded
    in a ``data-clientlogic-settings-ReadingContracts`` JSON attribute on the
    ``#sk-reading-contracts`` element.

    Note: BeautifulSoup/lxml lowercases HTML attributes, so we use the
    lowercase form when querying.
    """
    soup = make_soup(html)
    el = soup.select_one("#sk-reading-contracts")
    if not el:
        return None

    raw = el.get("data-clientlogic-settings-readingcontracts", "")
    if not raw:
        return None

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    provider = data.get("DataProviderSettings", {})
    return (
        provider.get("GetStudentClassReadingContracts")
        or provider.get("GetStudentGroupReadingContracts")
        or None
    )


def parse_reading_contracts_json(json_text: str) -> list[ReadingContract]:
    """Parse the JSON response from the reading contracts API endpoint."""
    try:
        data = json.loads(json_text)
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(data, list):
        data = [data]

    contracts: list[ReadingContract] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        books: list[ReadingContractBook] = []
        for book_data in item.get("Books", []):
            if not isinstance(book_data, dict):
                continue
            books.append(ReadingContractBook(
                title=book_data.get("Title", ""),
                author=book_data.get("Author", ""),
                read_pages_count=book_data.get("ReadPagesCount", 0),
            ))

        contracts.append(ReadingContract(
            id=item.get("Id", 0),
            category=item.get("Category", ""),
            date_range=item.get("DateRange", ""),
            pages_to_read=item.get("PagesToRead", 0),
            progress=item.get("Progress", 0),
            is_active=item.get("IsActive", False),
            is_page_used_for_count=item.get("IsPageUsedForCount", True),
            books=books,
        ))

    return contracts
