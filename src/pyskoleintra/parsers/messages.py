"""Parsers for Skoleintra message pages (inbox, unread, individual messages)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from ..exceptions import ParseError
from ..models import (
    ArchivedMessageDetail, ArchivedMessagePage, ArchivedMessageSummary,
    Attachment, MessageDetail, MessageSummary, MessageThread,
)
from .common import extract_json_attr, extract_text, make_soup
from .message_dates import date_fields


def extract_data_provider_settings(html: str) -> dict[str, str]:
    """Read the inbox's AJAX endpoints; a missing inbox must not look empty."""
    attr = "data-clientlogic-settings-messageconversationswitharchivefunctionality"
    data = extract_json_attr(make_soup(html), f"div[{attr}]", attr)
    if not isinstance(data, dict) or not isinstance(data.get("DataProviderSettings"), dict):
        raise ParseError("Message inbox settings are unavailable")
    return {
        key: value for key, value in data["DataProviderSettings"].items()
        if isinstance(value, str) and value
    }


def archive_message_links(html: str, *, subject: str, sender: str) -> list[str]:
    """Find candidate archive copies on one search page, without treating login as empty."""
    soup = make_soup(html)
    container = soup.select_one("div.sk-messages-list[data-clientlogic-settings-messages]")
    if container is None:
        raise ParseError("Archive message list is unavailable")
    links = []
    for row in container.select("li.sk-message-list-item"):
        if (
            extract_text(row.select_one(".sk-message-title")) != subject
            or extract_text(row.select_one(".sk-message-senderrecipient-name")) != sender
        ):
            continue
        link = row.select_one('a[href*="/archive/message/"]')
        if link is not None:
            links.append(link["href"])
    return links


def archive_message_matches(html: str, original: MessageDetail) -> bool:
    """Compare preserved content; archive HTML can discard the original time of day."""
    soup = make_soup(html)
    if not original.subject or not original.sender or not original.date:
        return False
    if soup.select_one(".sk-message-subject-text") is None or soup.select_one(".sk-message-text") is None:
        return False
    archived = parse_message_detail_html(html, "")

    def normalize(text: str) -> str:
        return " ".join(text.split())

    def without_clock(text: str) -> str:
        return re.sub(r"\s+\d{1,2}:\d{2}(?::\d{2})?$", "", normalize(text))

    original_text = make_soup(original.content).get_text(" ", strip=True)
    # Attachment URLs may change when copied. Never accept a match without
    # verifying each original attachment URL; refuse unsupported copies safely.
    return (
        archived.subject == original.subject
        and archived.sender == original.sender
        and without_clock(archived.date) == without_clock(original.date)
        and normalize(archived.content) == normalize(original_text)
        and archived.attachments == original.attachments
    )


def archive_page_links(html: str) -> list[str]:
    """Return archive pagination links for the caller to validate against its child."""
    return [
        link["href"] for link in make_soup(html).select("a[href]")
        if re.search(r"/messages/archive/[1-9][0-9]*(?:\?|$)", link["href"])
    ]


def parse_inbox_conversations(
    html: str, *, source_timezone: ZoneInfo | None = None,
    fetched_at: datetime | None = None,
) -> list[MessageThread]:
    """Parse the inbox conversations page.

    The conversation list is embedded as JSON in a data attribute on a
    ``div[data-clientlogic-settings-messageconversationswitharchivefunctionality]``.
    """
    soup = make_soup(html)
    data = extract_json_attr(
        soup,
        "div[data-clientlogic-settings-messageconversationswitharchivefunctionality]",
        "data-clientlogic-settings-messageconversationswitharchivefunctionality",
    )

    if not data or not isinstance(data, dict):
        return []

    conversations = data.get("Conversations", [])
    threads: list[MessageThread] = []

    for conv in conversations:
        profile_vm = conv.get("ProfileImageViewModel", {})
        threads.append(_thread_from_json(
            conv, profile_vm, source_timezone=source_timezone, fetched_at=fetched_at,
        ))

    return threads


def parse_inbox_page_json(
    json_text: str, *, source_timezone: ZoneInfo | None = None,
    fetched_at: datetime | None = None,
) -> list[MessageThread]:
    """Parse a JSON response from the paginated conversations endpoint."""
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return []

    if isinstance(data, dict):
        conversations = data.get("Conversations", data.get("Messages", []))
    elif isinstance(data, list):
        conversations = data
    else:
        return []

    threads: list[MessageThread] = []
    for conv in conversations:
        profile_vm = conv.get("ProfileImageViewModel", {})
        threads.append(_thread_from_json(
            conv, profile_vm, source_timezone=source_timezone, fetched_at=fetched_at,
        ))

    return threads


def _thread_from_json(
    conv: dict, profile_vm: dict | None = None, *, source_timezone: ZoneInfo | None = None,
    fetched_at: datetime | None = None,
) -> MessageThread:
    """Build a MessageThread from a JSON conversation dict."""
    if profile_vm is None:
        profile_vm = {}
    return MessageThread(
        thread_id=conv.get("ThreadId", ""),
        subject=conv.get("Subject", ""),
        messages_count=conv.get("MessagesCount", 0),
        latest_message_id=conv.get("LatestMessageId", 0),
        date=conv.get("Date", ""),
        is_unread=conv.get("IsUnread", False),
        sender_name=profile_vm.get("Name", conv.get("SenderName", "")),
        profile_image_url=profile_vm.get("Photo", ""),
        has_attachments=conv.get("HasAttachments", False),
        is_forwarded=conv.get("IsForwarded", False),
        is_replied=conv.get("IsReplied", False),
        thread_participants=conv.get("ThreadParticipantsString", ""),
        unread_messages_count=conv.get("UnreadMessagesCount", 0),
        **date_fields(conv.get("Date", ""), source_timezone=source_timezone, fetched_at=fetched_at),
    )


def parse_unread_messages_list(
    html: str, *, source_timezone: ZoneInfo | None = None,
    fetched_at: datetime | None = None,
) -> list[MessageSummary]:
    """Parse the unread messages page, which uses HTML list items."""
    soup = make_soup(html)
    container = soup.select_one("div.sk-messages-list")
    if not container:
        return []

    # Read the unread CSS class from settings
    settings_raw = container.get("data-clientlogic-settings-messages", "")
    unread_css = "sk-unread-message"
    if settings_raw:
        try:
            settings = json.loads(settings_raw)
            unread_css = settings.get("UnreadListItemCssClass", unread_css)
        except json.JSONDecodeError:
            pass

    messages: list[MessageSummary] = []
    for item in container.select("li.sk-message-list-item"):
        is_unread = unread_css in item.get("class", [])
        subject = extract_text(item.select_one("div.sk-message-title"))
        sender = extract_text(item.select_one("li.sk-message-senderrecipient-name"))
        date = extract_text(item.select_one("li.sk-message-send-date"))

        message_id = None
        msg_link = item.select_one("a[href*='/message/']")
        if msg_link:
            match = re.search(r"/message/(\d+)", msg_link["href"])
            if match:
                message_id = match.group(1)

        messages.append(MessageSummary(
            id=message_id,
            subject=subject,
            sender=sender,
            date=date,
            unread=is_unread,
            **date_fields(
                date, source_timezone=source_timezone, fetched_at=fetched_at,
                machine_value=_machine_date(item.select_one(".sk-message-send-date")),
            ),
        ))

    return messages


def _machine_date(element) -> str | None:
    if element is None:
        return None
    time_element = element if element.name == "time" else element.select_one("time[datetime]")
    return time_element.get("datetime") if time_element is not None else None


def parse_message_detail_html(
    html: str, message_id: str, *, source_timezone: ZoneInfo | None = None,
    fetched_at: datetime | None = None, day_only: bool = False,
) -> MessageDetail:
    """Parse a single message detail page (HTML format, used by unread/outbox)."""
    soup = make_soup(html)

    subject = extract_text(soup.select_one("div.sk-message-subject-text"))
    sender_el = soup.select_one("div.sk-message-senderrecipient-name span")
    sender = extract_text(sender_el)

    date_el = soup.select_one("div.sk-message-send-date span")
    date = extract_text(date_el)

    content_el = soup.select_one("div.sk-message-text")
    content = content_el.get_text(separator="\n", strip=True) if content_el else ""

    auto_delete = extract_text(soup.select_one("span.sk-message-deleted-automatically-label"))

    # Recipients
    recipients: list[str] = []
    for span in soup.select("div.sk-message-senderrecipient-name span:not(.semibold)"):
        text = span.get_text(strip=True).strip(", ")
        if text:
            recipients.extend(r.strip() for r in text.split(",") if r.strip())

    # Attachments
    attachments: list[Attachment] = []
    for a_tag in soup.select("div.sk-attachments-list a"):
        attachments.append(Attachment(
            name=a_tag.get_text(strip=True),
            url=a_tag.get("href", ""),
        ))

    return MessageDetail(
        id=message_id,
        subject=subject,
        sender=sender,
        date=date,
        content=content,
        recipients=recipients,
        attachments=attachments,
        auto_delete_date=auto_delete,
        **date_fields(
            date, source_timezone=source_timezone, fetched_at=fetched_at, day_only=day_only,
            machine_value=_machine_date(soup.select_one(".sk-message-send-date")),
        ),
    )


def parse_message_detail_json(
    json_text: str, *, source_timezone: ZoneInfo | None = None,
    fetched_at: datetime | None = None,
) -> list[MessageDetail]:
    """Parse message detail from JSON (used by conversation thread endpoint)."""
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return []

    if isinstance(data, dict):
        data = [data]

    details: list[MessageDetail] = []
    for msg in data:
        attachments = []
        for link in (msg.get("AttachmentsLinks") or []):
            if isinstance(link, dict):
                attachments.append(Attachment(
                    name=link.get("Name", ""),
                    url=link.get("Url", ""),
                ))

        details.append(MessageDetail(
            id=str(msg.get("Id", "")),
            subject=msg.get("Subject", ""),
            sender=msg.get("SenderName", ""),
            date=msg.get("SentReceivedDateText", ""),
            content=msg.get("BaseText", ""),
            recipients=msg.get("Recipients", []),
            attachments=attachments,
            auto_delete_date=msg.get("AutoDeletionDateText", ""),
            is_archived=msg.get("IsCopiedToArchive"),
            is_outbox=msg.get("IsOutbox"),
            # Exact per-message UI flag, not the whole conversation's IsUnread.
            is_unread=msg.get("ShowUnreadIndication") if type(msg.get("ShowUnreadIndication")) is bool else None,
            **date_fields(
                msg.get("SentReceivedDateText", ""),
                source_timezone=source_timezone, fetched_at=fetched_at,
                machine_value=msg.get("SentReceivedDate"),
            ),
        ))

    return details


def _archive_link_id(href: str, base_url: str, parent_path: str, route: str) -> int | None:
    """Accept only numeric archive routes on this child's origin."""
    base = urlsplit(base_url)
    target = urlsplit(urljoin(base_url, href))
    if (target.scheme, target.netloc) != (base.scheme, base.netloc):
        return None
    match = re.fullmatch(re.escape(parent_path) + route + r"([1-9][0-9]*)", target.path)
    return int(match[1]) if match else None


def parse_archived_messages(
    html: str, *, base_url: str, parent_path: str, page: int,
    source_timezone: ZoneInfo | None = None, fetched_at: datetime | None = None,
) -> ArchivedMessagePage:
    """Parse exactly one archive page. Missing markup is not an empty archive."""
    soup = make_soup(html)
    container = soup.select_one("div.sk-messages-list[data-clientlogic-settings-messages]")
    if container is None:
        raise ParseError("Archive message list is unavailable")
    messages = []
    seen = set()
    for row in container.select("li.sk-message-list-item"):
        link = row.select_one('a[href*="/archive/message/"]')
        archive_id = _archive_link_id(
            link["href"], base_url, parent_path, "/messages/archive/message/",
        ) if link else None
        if archive_id is None or archive_id in seen:
            raise ParseError("Archive entry has an invalid or duplicate archive ID")
        seen.add(archive_id)
        subject = row.select_one(".sk-message-title")
        sender = row.select_one(".sk-message-senderrecipient-name")
        date_element = row.select_one(".sk-message-send-date")
        if subject is None or sender is None or date_element is None:
            raise ParseError("Archive entry is incomplete")
        raw_date = extract_text(date_element)
        messages.append(ArchivedMessageSummary(
            archive_id=archive_id, subject=extract_text(subject), sender=extract_text(sender),
            date=raw_date, **date_fields(
                raw_date, source_timezone=source_timezone, fetched_at=fetched_at,
                day_only=True, machine_value=_machine_date(date_element),
            ),
        ))
    pages = [
        number for href in archive_page_links(html)
        if (number := _archive_link_id(href, base_url, parent_path, "/messages/archive/"))
        is not None and number > page
    ]
    return ArchivedMessagePage(messages=messages, page=page, next_page=min(pages, default=None))


def parse_archived_message(
    html: str, archive_id: int, *, source_timezone: ZoneInfo | None = None,
    fetched_at: datetime | None = None,
) -> ArchivedMessageDetail:
    """Parse archive content with at most day precision, even for a displayed 00:00."""
    soup = make_soup(html)
    for selector in (".sk-message-subject-text", ".sk-message-text",
                     ".sk-message-senderrecipient-name", ".sk-message-send-date"):
        if soup.select_one(selector) is None:
            raise ParseError("Archive message detail is unavailable or incomplete")
    # When the page exposes an identity, reject a response for a different copy.
    for form in soup.select('form[action*="/messages/archive/message/delete/"]'):
        match = re.search(r"/messages/archive/message/delete/([0-9]+)(?:\?|$)", form["action"])
        if match is None or int(match[1]) != archive_id:
            raise ParseError("Archive detail identity does not match the requested archive ID")
    detail = parse_message_detail_html(
        html, str(archive_id), source_timezone=source_timezone, fetched_at=fetched_at,
        day_only=True,
    )
    return ArchivedMessageDetail(
        archive_id=archive_id, subject=detail.subject, sender=detail.sender,
        date=detail.date, content=detail.content, recipients=detail.recipients,
        attachments=detail.attachments, timestamp=detail.timestamp,
        calendar_date=detail.calendar_date, date_precision=detail.date_precision,
    )
