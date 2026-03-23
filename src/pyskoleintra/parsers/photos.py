"""Parser for photo album pages."""

from __future__ import annotations

import json
import re

from ..models import Album, Photo
from .common import extract_text, make_soup


def parse_albums_list(html: str, parent_path: str) -> list[Album]:
    """Parse the albums list page at ``/photos/albums``.

    Albums are listed as ``<li>`` items inside ``ul.sk-list``, each containing
    an ``<a class="sk-photoalbums-list-item">`` link.
    """
    soup = make_soup(html)
    albums: list[Album] = []

    for link in soup.select("a.sk-photoalbums-list-item"):
        href = link.get("href", "")
        # Album ID from URL pattern /albums/album/photos/{id}
        album_id_match = re.search(r"/(?:album/photos|Album)/(\d+)", href, re.IGNORECASE)
        if not album_id_match:
            continue

        album_id = int(album_id_match.group(1))

        title_el = link.select_one("div.sk-photoalbum-list-item-title")
        title = extract_text(title_el) if title_el else f"Album {album_id}"

        desc_el = link.select_one("div.sk-photoalbum-list-item-description")
        description = extract_text(desc_el) if desc_el else ""

        author_el = link.select_one("div.sk-photoalbum-list-item-author")
        author = ""
        if author_el:
            raw = author_el.get_text(strip=True)
            # Strip "Oprettet af: " prefix
            author = re.sub(r"^Oprettet af:\s*", "", raw)

        # Cover image URL from inline background style
        cover_image_url = ""
        cover_div = link.select_one("div.sk-photoalbum-list-item-cover-image div")
        if cover_div:
            style = cover_div.get("style", "")
            url_match = re.search(r"url\(\s*['\"]?([^'\")\s]+)['\"]?\s*\)", style)
            if url_match:
                cover_image_url = url_match.group(1)

        # Unread status from parent <li>
        li = link.find_parent("li")
        is_unread = "sk-unread-photoalbum" in (li.get("class", []) if li else [])

        albums.append(Album(
            id=album_id, title=title, photo_count=0, url=href,
            description=description, author=author,
            cover_image_url=cover_image_url, is_unread=is_unread,
        ))

    return albums


def parse_album_photos(html: str) -> list[Photo]:
    """Parse individual photos from an album page.

    Photos are available in a ``data-clientlogic-settings-photoalbum`` JSON
    attribute containing a ``GalleryModel.Items`` array, and also as ``<img>``
    tags with ``/file/photoalbum/`` URLs.
    """
    soup = make_soup(html)
    photos: list[Photo] = []

    # Try JSON config first (most reliable)
    el = soup.select_one("[data-clientlogic-settings-photoalbum]")
    if el:
        raw = el.get("data-clientlogic-settings-photoalbum", "")
        try:
            data = json.loads(raw)
            items = data.get("GalleryModel", {}).get("Items", [])
            for i, item in enumerate(items):
                src = item.get("Source", "")
                caption = item.get("Description", "")
                photos.append(Photo(
                    id=i,
                    url=src,
                    thumbnail_url=src,
                    caption=caption,
                ))
            if photos:
                return photos
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallback: parse <img> tags with photoalbum URLs
    for img in soup.select("img[src*='/file/photoalbum/']"):
        src = img.get("src", "")
        if not src:
            continue

        # Try to get a unique ID from the filename
        parent_link = img.find_parent("a")
        full_url = parent_link["href"] if parent_link and parent_link.get("href") else src

        photos.append(Photo(
            id=len(photos),
            url=full_url,
            thumbnail_url=src,
            caption=img.get("alt", ""),
        ))

    return photos
