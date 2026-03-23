"""Parser for weekly plans (ugeplaner) page."""

from __future__ import annotations

import json
import re

from ..models import WeeklyPlan
from .common import extract_json_attr, extract_text, make_soup


def parse_weekly_plans_list(html: str) -> list[dict]:
    """Parse the weekly plans list page to discover available plans.

    The page may contain plans either in a ``data-clientlogic-settings-WeeklyPlansApp``
    JSON attribute, or as a server-rendered ``ul.sk-weekly-plans-list-container``.
    """
    soup = make_soup(html)

    # Try JSON attribute first (some schools)
    data = extract_json_attr(
        soup,
        "#root[data-clientlogic-settings-weeklyplansapp]",
        "data-clientlogic-settings-weeklyplansapp",
    )
    if isinstance(data, dict):
        return data.get("Plans", data.get("WeeklyPlans", [data]))
    if isinstance(data, list):
        return data

    # Server-rendered list: ul.sk-weekly-plans-list-container > li > a
    plans: list[dict] = []
    for li in soup.select("ul.sk-weekly-plans-list-container li"):
        link = li.select_one("a")
        if not link:
            continue
        href = link.get("href", "")
        title_span = link.select_one("span")
        title = extract_text(title_span) if title_span else link.get_text(strip=True)
        # Extract week-year from the title attribute or href (e.g. "12-2026")
        week_year = ""
        if title_span:
            week_year = title_span.get("title", "")
        if not week_year:
            wy_match = re.search(r"/(\d{1,2}-\d{4})$", href)
            if wy_match:
                week_year = wy_match.group(1)

        plans.append({
            "title": title,
            "url": href,
            "week_year": week_year,
            "is_unread": "sk-unread-item" in li.get("class", []),
        })

    return plans


def parse_weekly_plan_detail(html_or_json: str, week: int, year: int) -> WeeklyPlan:
    """Parse a single weekly plan page or JSON response.

    Weekly plan content structure varies by school, so we return the raw
    content dict along with week/year metadata.
    """
    # Try JSON first
    try:
        content = json.loads(html_or_json)
    except (json.JSONDecodeError, TypeError):
        # Fall back to HTML parsing
        soup = make_soup(html_or_json)
        data = extract_json_attr(
            soup,
            "[data-clientlogic-settings-weeklyplansapp]",
            "data-clientlogic-settings-weeklyplansapp",
        )
        content = data if data else {"raw_html": html_or_json}

    return WeeklyPlan(week=week, year=year, content=content)
