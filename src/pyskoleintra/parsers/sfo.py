"""Parser for the legacy SkoleIntra SFO front page."""

from __future__ import annotations

import re

from ..models import SfoInfo
from .common import make_soup


def parse_sfo_page(html: str, base_url: str) -> SfoInfo:
    """Parse ``SFOforside.asp`` and discover its dynamic Tabulex link."""
    soup = make_soup(html)
    tabulex_link = soup.select_one('a[href*="Tabulex"], a[href*="tabulex"]')
    tabulex_url = str(tabulex_link["href"]) if tabulex_link else None

    links: dict[str, str] = {}
    for link in soup.select("a[href]"):
        href = str(link.get("href") or "")
        text = link.get_text(" ", strip=True)
        if href and text and not href.startswith(("#", "javascript")):
            links[text] = href

    front_page = ""
    for cell in soup.select("td"):
        text = cell.get_text(" ", strip=True)
        if 50 < len(text) < 2000 and "aktiviteter" not in text.casefold()[:30]:
            front_page = text
            break

    return SfoInfo(
        base_url=base_url,
        tabulex_url=tabulex_url,
        front_page_posting=front_page,
        notice_board=_find_infoweb_section(soup, r"opslagstavle"),
        weekly_plan=_find_infoweb_section(soup, r"ugeplan"),
        news=_find_infoweb_section(soup, r"nyt\s+fra\s+sfo"),
        shortcuts=links,
    )


def _find_infoweb_section(soup, heading_pattern: str) -> str:
    """Extract a legacy Infoweb box without assuming one table layout."""
    heading = soup.find(string=re.compile(heading_pattern, re.IGNORECASE))
    if heading is None:
        return ""
    for container_name in ("td", "tr", "table", "div"):
        container = heading.find_parent(container_name)
        if container is None:
            continue
        text = container.get_text(" ", strip=True)
        text = re.sub(heading_pattern, "", text, count=1, flags=re.IGNORECASE).strip()
        if text and len(text) <= 4000:
            return text
    return ""
