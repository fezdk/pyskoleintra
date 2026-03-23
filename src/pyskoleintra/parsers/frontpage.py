"""Parser for the Skoleintra frontpage — extracts navigation menu and child info."""

from __future__ import annotations

import re

from ..models import ChildInfo, MenuItem
from .common import make_soup


def parse_menu_items(html: str, parent_path: str) -> list[MenuItem]:
    """Extract navigation menu items from the frontpage HTML.

    The menu is in a ``<nav>`` with ``<a>`` links whose hrefs contain the parent path.
    """
    soup = make_soup(html)
    items: list[MenuItem] = []
    seen_urls: set[str] = set()

    for link in soup.select("nav a[href]"):
        href = link.get("href", "")
        if parent_path not in href or href in seen_urls:
            continue
        seen_urls.add(href)

        title_span = link.select_one(".sk-l-menu-item-title")
        title = title_span.get_text(strip=True) if title_span else link.get_text(strip=True)

        badge = link.select_one(".sk-l-menu-item-badge")
        badge_count = 0
        if badge and badge.get_text(strip=True).isdigit():
            badge_count = int(badge.get_text(strip=True))

        icon_span = link.select_one("[class*='sk-font-icon']")
        icon_class = ""
        if icon_span:
            for cls in icon_span.get("class", []):
                if cls.startswith("sk-l-menu-item-"):
                    icon_class = cls
                    break

        items.append(MenuItem(title=title, url=href, badge_count=badge_count, icon_class=icon_class))

    return items


def parse_children_from_page(html: str) -> list[ChildInfo]:
    """Discover children by finding all unique /parent/{id}/{name} paths in the page.

    On a parent account with multiple children, the page contains links for switching
    between children — each with a different parent path.
    """
    soup = make_soup(html)
    children: dict[str, ChildInfo] = {}

    # Count occurrences of each parent path pattern to distinguish real children
    # from URL join bugs (e.g. /parent/1234/Oliveritem/ from missing slash)
    path_counts: dict[str, int] = {}
    path_infos: dict[str, ChildInfo] = {}

    for link in soup.find_all("a", href=True):
        href = link["href"]
        match = re.match(r"/parent/(\d+)/(\w+)/", href)
        if match:
            parent_id = int(match.group(1))
            name = match.group(2)
            key = f"{parent_id}/{name}"
            path_counts[key] = path_counts.get(key, 0) + 1
            if key not in path_infos:
                path_infos[key] = ChildInfo(
                    name=name,
                    parent_id=parent_id,
                    parent_path=f"/parent/{parent_id}/{name}",
                )

    # Filter out false positives: if name X is a prefix of name Xfoo for the
    # same parent_id, and X appears more often, Xfoo is likely a URL join bug
    to_remove: set[str] = set()
    keys = list(path_infos.keys())
    for key in keys:
        info = path_infos[key]
        for other_key in keys:
            if other_key == key or other_key in to_remove:
                continue
            other_info = path_infos[other_key]
            if (
                other_info.parent_id == info.parent_id
                and other_info.name.startswith(info.name)
                and len(other_info.name) > len(info.name)
                and path_counts.get(key, 0) > path_counts.get(other_key, 0)
            ):
                to_remove.add(other_key)

    return [info for key, info in path_infos.items() if key not in to_remove]


def parse_unread_badge_count(html: str) -> int:
    """Extract the unread messages count from the header badge."""
    soup = make_soup(html)
    badge = soup.select_one('a[data-menu-item-type="Messages"] .badge')
    if badge:
        text = badge.get_text(strip=True)
        if text.isdigit():
            return int(text)
    return 0
