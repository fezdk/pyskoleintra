"""Authenticated binary downloads for school message attachments."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import unquote, urljoin, urlsplit

from .exceptions import NetworkError, NotAuthorizedError, ParseError
from .models import Attachment
from .parsers.common import make_soup

if TYPE_CHECKING:
    from .http import HttpSession


def _is_login_url(url: str) -> bool:
    try:
        path = unquote(urlsplit(url).path).casefold()
    except ValueError as exc:
        raise ParseError("Attachment response contains an invalid URL") from exc
    return any(part in ("login", "signin", "sign-in", "logon", "idplogin")
               for part in path.split("/"))


def _file_url(base_url: str, reference: str) -> str:
    if not isinstance(reference, str):
        raise TypeError("Attachment URL must be text")
    if not reference or any(ord(c) < 32 or ord(c) == 127 for c in reference):
        raise ValueError("Attachment URL is empty or contains control characters")
    target = urljoin(base_url.rstrip("/") + "/", reference)
    parts, base = urlsplit(target), urlsplit(base_url)
    if (parts.scheme != "https" or base.scheme != "https"
            or not parts.hostname or parts.hostname != base.hostname
            or (parts.port or 443) != (base.port or 443)
            or parts.username is not None or parts.password is not None
            or parts.fragment):
        raise ValueError("Attachment URL must use the school's HTTPS origin without credentials or fragment")
    # These are file references, not arbitrary authenticated application actions.
    # Reject encoded traversal as well as the normal /../ spelling.
    path = parts.path
    for _ in range(4):
        if (not path.startswith("/file/") or "\\" in path
                or any(part in (".", "..") for part in path.split("/"))
                or any(ord(c) < 32 or ord(c) == 127 for c in path)):
            raise ValueError("Attachment URL must reference a school /file/ resource")
        decoded = unquote(path)
        if decoded == path:
            return target
        path = decoded
    raise ValueError("Attachment URL has excessive nested escaping")


def _check_file_response(response) -> None:
    if response.status_code in (401, 403):
        raise NotAuthorizedError("Attachment requires an authenticated session and file access")
    if response.status_code != 200:
        raise NetworkError(f"Attachment download returned HTTP {response.status_code}")
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    # Sniff only the beginning, never decode the binary file as response.text.
    # This also detects a login page incorrectly labelled application/octet-stream.
    prefix = response.content[:8192].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    html = content_type in ("text/html", "application/xhtml+xml") or re.match(
        br"(?:<!doctype\s+html\b|<html\b|<head\b|<body\b|<form\b)", prefix,
    )
    if not html:
        return
    soup = make_soup(response.content)
    if soup.select_one('input[type="password" i]') is not None or any(
        _is_login_url(form.get("action", "")) for form in soup.select("form[action]")
    ):
        raise NotAuthorizedError("Attachment request returned a login form")
    disposition = response.headers.get("Content-Disposition", "").split(";", 1)[0].strip().lower()
    if disposition != "attachment":
        raise ParseError("Attachment request returned an HTML page instead of a file")


def download_attachment(http: HttpSession, base_url: str, attachment: Attachment | str) -> bytes:
    """Fetch one same-school file, following at most five validated redirects."""
    if not isinstance(attachment, (Attachment, str)):
        raise TypeError("attachment must be an Attachment or its URL")
    target = _file_url(base_url, attachment.url if isinstance(attachment, Attachment) else attachment)
    seen = set()
    for hop in range(6):
        if target in seen:
            raise ParseError("Attachment redirect loop")
        seen.add(target)
        # The development cache stores decoded HTML and would corrupt file bytes.
        response = http.get(target, use_cache=False, allow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            if not location:
                raise ParseError("Attachment redirect is missing its Location header")
            if _is_login_url(location):
                raise NotAuthorizedError("Attachment request redirected to login")
            if hop == 5:
                raise ParseError("Attachment exceeded five redirects")
            try:
                target = _file_url(base_url, urljoin(target, location))
            except ValueError as exc:
                raise ParseError("Attachment redirect leaves the supported school file routes") from exc
            continue
        _check_file_response(response)
        return response.content
    raise ParseError("Attachment download could not be completed")  # Defensive, loop raises at hop 5.
