"""Parser for reading contracts (læsekontrakt) page."""

from __future__ import annotations

import json

from ..exceptions import ParseError
from ..models import ReadingContract, ReadingContractBook, ReadingContractEntry
from .common import make_soup


def extract_data_provider_settings(html: str) -> dict[str, str]:
    """Extract the reading-contract AJAX endpoints from the page HTML.

    The page is a Vue.js SPA that loads data from an API. The URL is embedded
    in a ``data-clientlogic-settings-ReadingContracts`` JSON attribute on the
    ``#sk-reading-contracts`` element.

    Note: BeautifulSoup/lxml lowercases HTML attributes, so we use the
    lowercase form when querying.
    """
    soup = make_soup(html)
    el = soup.select_one("#sk-reading-contracts")
    if not el:
        return {}

    raw = el.get("data-clientlogic-settings-readingcontracts", "")
    if not raw:
        return {}

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}

    if not isinstance(data, dict):
        return {}

    provider = data.get("DataProviderSettings", {})
    if not isinstance(provider, dict):
        return {}
    return {key: value for key, value in provider.items() if isinstance(value, str) and value}


def extract_contracts_api_url(html: str) -> str | None:
    """Extract the class/group contracts endpoint, retaining the original API."""
    provider = extract_data_provider_settings(html)
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
            student_id=item.get("StudentId", 0),
            is_read_only=item.get("IsReadOnly", False),
        ))

    return contracts


def parse_reading_contract_entries_json(
    json_text: str, *, contract_id: int, title: str, author: str,
) -> list[ReadingContractEntry]:
    """Parse GetRecordsForBook strictly: missing IDs must never look like an empty list."""
    try:
        data = json.loads(json_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ParseError("Invalid reading entries JSON") from exc
    if not isinstance(data, list):
        raise ParseError("Expected a list of reading entries")

    entries = []
    seen_ids = set()
    for item in data:
        if (
            not isinstance(item, dict)
            or type(item.get("Id")) is not int
            or item["Id"] <= 0
            or item["Id"] in seen_ids
            or type(item.get("Pages")) is not int
            or item["Pages"] < 0
            or not isinstance(item.get("Date"), str)
        ):
            raise ParseError("Invalid or duplicate reading entry")
        seen_ids.add(item["Id"])
        entries.append(ReadingContractEntry(
            date=item["Date"], title=title, pages=str(item["Pages"]),
            id=item["Id"], contract_id=contract_id, author=author,
            read_pages_count=item["Pages"],
        ))
    return entries
