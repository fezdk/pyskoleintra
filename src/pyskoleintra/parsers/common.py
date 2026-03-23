"""Shared HTML parsing utilities used across multiple parsers."""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

# Danish month name mappings (short and long forms)
MONTH_SHORT = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
}
MONTH_LONG = {
    "januar": 1, "februar": 2, "marts": 3, "april": 4, "maj": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
}


def make_soup(html: str) -> BeautifulSoup:
    """Create a BeautifulSoup instance from raw HTML."""
    return BeautifulSoup(html, "lxml")


def parse_first_form(html: str) -> dict | None:
    """Parse the first <form> in the HTML, returning its action, method, and input values.

    Returns:
        A dict with ``form`` (action, method) and ``inputs`` keys, or ``None`` if no form found.
    """
    soup = make_soup(html)
    form = soup.find("form")
    if not form or not isinstance(form, Tag):
        return None

    inputs: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if name:
            inputs[name] = inp.get("value", "")

    return {
        "form": {
            "action": form.get("action", ""),
            "method": form.get("method", "post"),
        },
        "inputs": inputs,
    }


def extract_json_attr(soup: BeautifulSoup, selector: str, attr: str) -> dict | list | None:
    """Find an element by CSS selector and parse a JSON-encoded attribute."""
    el = soup.select_one(selector)
    if not el:
        return None
    raw = el.get(attr, "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse JSON from attr %s on %s", attr, selector)
        return None


def extract_text(element: Tag | None) -> str:
    """Safely extract and strip text from a BS4 element."""
    if element is None:
        return ""
    return element.get_text(strip=True)


def resolve_url(base: str, url: str) -> str:
    """Resolve a potentially relative URL against a base."""
    if url.startswith("http"):
        return url
    return urljoin(base, url)


def base_site_url(url: str) -> str:
    """Extract scheme + host from a full URL (e.g. 'https://foo.example.dk/path' -> 'https://foo.example.dk')."""
    match = re.match(r"(https?://[^/]+)", url)
    return match.group(1) if match else url


def parse_js_redirect(html: str) -> str | None:
    """Extract a URL from a JavaScript ``parent.location.href = '...'`` redirect."""
    match = re.search(r"""parent\.location\.href\s*=\s*['"]([^'"]+)['"]""", html)
    return match.group(1) if match else None
