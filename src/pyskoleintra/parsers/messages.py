"""Parsers for Skoleintra message pages (inbox, unread, individual messages)."""

from __future__ import annotations

import json
import re

from ..exceptions import ParseError
from ..models import Attachment, MessageDetail, MessageSummary, MessageThread
from .common import extract_json_attr, extract_text, make_soup


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


def parse_inbox_conversations(html: str) -> list[MessageThread]:
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
        threads.append(_thread_from_json(conv, profile_vm))

    return threads


def parse_inbox_page_json(json_text: str) -> list[MessageThread]:
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
        threads.append(_thread_from_json(conv, profile_vm))

    return threads


def _thread_from_json(conv: dict, profile_vm: dict | None = None) -> MessageThread:
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
    )


def parse_unread_messages_list(html: str) -> list[MessageSummary]:
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
        ))

    return messages


def parse_message_detail_html(html: str, message_id: str) -> MessageDetail:
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
    )


def parse_message_detail_json(json_text: str) -> list[MessageDetail]:
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
        ))

    return details
