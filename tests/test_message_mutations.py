from __future__ import annotations

import json
from html import escape
from unittest.mock import Mock
from urllib.parse import parse_qs

import pytest
import requests

from pyskoleintra.child import Child
from pyskoleintra.exceptions import NetworkError, NotAuthorizedError, ParseError
from pyskoleintra.http import HttpSession
from pyskoleintra.models import ChildInfo, MessageDetail
from pyskoleintra.parsers.messages import parse_message_detail_json


BASE = "https://school/parent/1/Child/messages/"
PROVIDER = {
    "GetMessageForThreadlessConversationUrl": BASE + "conversations/getmessageforthreadlessconversation",
    "CopyConversationMessageToArchiveUrl": BASE + "archiveMessage",
    "BatchDeleteConversationUrl": BASE + "batchDeleteMessages",
}
MESSAGE = {
    "Id": 123,
    "Subject": "Udflugt sidste sommer",
    "SenderName": "Testlærer",
    "SentReceivedDateText": "25. jun. 2026",
    "BaseText": "<p>Husk madpakken i morgen.</p>",
    "IsOutbox": False,
    "IsCopiedToArchive": False,
    "ActionButtons": [{"EventName": "messageConversationsDeleteSingleMessage"}],
}


def _response(data, status=200):
    response = requests.Response()
    response.status_code = status
    response._content = (data if isinstance(data, str) else json.dumps(data)).encode("utf-8")
    response.encoding = "utf-8"
    return response


def _page(provider=PROVIDER):
    settings = escape(json.dumps({"DataProviderSettings": provider}), quote=True)
    return _response(
        '<div data-clientlogic-settings-messageconversationswitharchivefunctionality="'
        + settings + '"></div>'
    )


def _setup(*responses, cache_dir=None):
    http = HttpSession(cache_dir=str(cache_dir) if cache_dir else None)
    http.session.request = Mock(side_effect=responses)
    child = Child(ChildInfo("Child", 1, "/parent/1/Child"), "https://school", http)
    return child, http


def _posts(http):
    return [call for call in http.session.request.call_args_list if call.args[0] == "POST"]


def _call(child, method, message_id):
    if method == "archive_message":
        return child.archive_message(message_id, mode="copy")
    return getattr(child, method)(message_id)


def test_fresh_inbox_lookup_needs_no_thread_and_preserves_read_status():
    child, http = _setup(_page(), _response(MESSAGE))

    message = child.inbox_message(" 00123 ")

    assert message.id == "123"
    assert message.subject == MESSAGE["Subject"]
    assert message.is_archived is False
    assert message.is_outbox is False
    assert _posts(http) == []
    assert http.session.request.call_args.args == (
        "GET", PROVIDER["GetMessageForThreadlessConversationUrl"] + "?messageId=123",
    )


def test_archive_copies_only_one_message_and_returns_verified_flags():
    archived = {**MESSAGE, "IsCopiedToArchive": True}
    child, http = _setup(_page(), _response(MESSAGE), _response(""), _response(archived))

    message = child.archive_message(123, mode="copy")

    assert message.is_archived is True
    assert message.is_outbox is False
    assert message.content == MESSAGE["BaseText"]
    posts = _posts(http)
    assert len(posts) == 1
    assert posts[0].args == ("POST", PROVIDER["CopyConversationMessageToArchiveUrl"])
    assert posts[0].kwargs["data"] == {"messageId": 123, "isOutbox": "false"}
    assert posts[0].kwargs["allow_redirects"] is False


def test_already_archived_is_a_fresh_noop():
    child, http = _setup(_page(), _response({**MESSAGE, "IsCopiedToArchive": True}))
    assert child.archive_message(123, mode="copy").is_archived is True
    assert _posts(http) == []


@pytest.mark.parametrize("archived", [False, True])
def test_delete_selects_only_one_numeric_message_never_a_thread(archived):
    child, http = _setup(
        _page(), _response({**MESSAGE, "IsCopiedToArchive": archived}),
        _response(True), _response(None),
    )

    assert child.delete_message("123") is None

    posts = _posts(http)
    assert len(posts) == 1
    assert posts[0].args == ("POST", PROVIDER["BatchDeleteConversationUrl"])
    assert posts[0].kwargs["allow_redirects"] is False
    # Check requests' actual form encoding against jQuery's single-ID selection.
    body = requests.Request("POST", BASE, data=posts[0].kwargs["data"]).prepare().body
    assert parse_qs(body) == {"MessageIds[]": ["123"]}


def test_delete_accepts_explicit_count_without_looking_up_a_deleted_id():
    # Observed live: POST returns JSON 1; looking up the deleted ID returns 500.
    child, http = _setup(_page(), _response(MESSAGE), _response(1), _response("error", 500))

    assert child.delete_message(123) is None

    assert len(_posts(http)) == 1
    assert http.session.request.call_count == 3


@pytest.mark.parametrize("count", [0, 2, -1])
def test_delete_rejects_unexpected_deleted_count_without_retry(count):
    child, http = _setup(_page(), _response(MESSAGE), _response(count))
    with pytest.raises(ParseError, match=f"server reported {count}"):
        child.delete_message(123)
    assert len(_posts(http)) == 1
    assert http.session.request.call_count == 3


@pytest.mark.parametrize("reply", [True, 1.0, '"1"', "", {}])
def test_non_integer_acknowledgement_still_requires_fresh_verification(reply):
    child, http = _setup(_page(), _response(MESSAGE), _response(reply), _response("error", 500))
    with pytest.raises(ParseError, match="verification failed"):
        child.delete_message(123)
    assert len(_posts(http)) == 1
    assert http.session.request.call_count == 4


@pytest.mark.parametrize("method", ["inbox_message", "archive_message", "delete_message"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "", "0", "-1", "１２３",
                                  "123&messageId=456", "thread-uuid", [123], None])
def test_invalid_ids_never_make_a_request(method, value):
    child, http = _setup()
    with pytest.raises(ValueError, match="Message ID"):
        _call(child, method, value)
    http.session.request.assert_not_called()


def test_explicit_null_means_not_found():
    child, _ = _setup(_page(), _response(None))
    assert child.inbox_message(123) is None


@pytest.mark.parametrize("method", ["archive_message", "delete_message"])
def test_missing_message_cannot_be_mutated(method):
    child, http = _setup(_page(), _response(None))
    with pytest.raises(ValueError, match="not present"):
        _call(child, method, 123)
    assert _posts(http) == []


@pytest.mark.parametrize("bad", [
    "<html>Login</html>", "", False, [], {},
    {**MESSAGE, "Id": 456}, {**MESSAGE, "Id": "123"},
    {**MESSAGE, "IsOutbox": None}, {**MESSAGE, "IsOutbox": "false"},
    {**MESSAGE, "IsCopiedToArchive": "false"},
])
def test_bad_lookup_cannot_be_treated_as_a_message_or_absence(bad):
    child, http = _setup(_page(), _response(bad))
    with pytest.raises(ParseError):
        child.delete_message(123)
    assert _posts(http) == []


@pytest.mark.parametrize("method", ["inbox_message", "archive_message", "delete_message"])
def test_sent_messages_are_out_of_scope(method):
    child, http = _setup(_page(), _response({**MESSAGE, "IsOutbox": True}))
    with pytest.raises(NotAuthorizedError, match="received inbox"):
        _call(child, method, 123)
    assert _posts(http) == []


@pytest.mark.parametrize("actions", [None, [], {}, [{"EventName": "deleteWholeThread"}]])
def test_delete_requires_the_single_message_action(actions):
    child, http = _setup(_page(), _response({**MESSAGE, "ActionButtons": actions}))
    with pytest.raises(NotAuthorizedError, match="delete action"):
        child.delete_message(123)
    assert _posts(http) == []


@pytest.mark.parametrize("method,key", [
    ("inbox_message", "GetMessageForThreadlessConversationUrl"),
    ("archive_message", "CopyConversationMessageToArchiveUrl"),
    ("delete_message", "BatchDeleteConversationUrl"),
])
@pytest.mark.parametrize("bad", [
    None, "https://other-school/messages/delete",
    "https://school/parent/2/Other/messages/batchDeleteMessages",
    BASE + "batchDeleteMessages?ThreadIds[]=some-thread",
    BASE + "batchDeleteMessages#fragment",
    BASE + "conversations/../batchDeleteMessages?messageId=456",
    BASE + "conversations/%2e%2e/batchDeleteMessages",
])
def test_endpoint_discovery_rejects_missing_or_unexpected_routes(method, key, bad):
    provider = {**PROVIDER, key: bad}
    child, http = _setup(_page(provider), _response(MESSAGE))
    with pytest.raises(ParseError, match="endpoint"):
        _call(child, method, 123)
    assert _posts(http) == []
    assert all(call.args[1] in (
        BASE + "conversations",
        PROVIDER["GetMessageForThreadlessConversationUrl"] + "?messageId=123",
    ) for call in http.session.request.call_args_list)


def test_relative_provider_routes_are_supported():
    provider = {key: url.removeprefix("https://school") for key, url in PROVIDER.items()}
    child, _ = _setup(_page(provider), _response(MESSAGE))
    assert child.inbox_message(123).id == "123"


@pytest.mark.parametrize("page", [_response("Login"), _page({}), _response("", 302)])
def test_missing_inbox_or_session_never_posts(page):
    child, http = _setup(page)
    with pytest.raises(ParseError):
        child.delete_message(123)
    assert _posts(http) == []


@pytest.mark.parametrize("method", ["archive_message", "delete_message"])
@pytest.mark.parametrize("status", [302, 403, 404, 500])
def test_failed_preflight_never_posts(method, status):
    child, http = _setup(_page(), _response("", status))
    with pytest.raises(ParseError, match=f"HTTP {status}"):
        _call(child, method, 123)
    assert _posts(http) == []


@pytest.mark.parametrize("method", ["archive_message", "delete_message"])
@pytest.mark.parametrize("response", [_response("", 302), _response("", 500), _response(False)])
def test_failed_post_is_not_followed_or_retried(method, response):
    child, http = _setup(_page(), _response(MESSAGE), response)
    with pytest.raises(ParseError, match="before retrying"):
        _call(child, method, 123)
    assert len(_posts(http)) == 1
    assert http.session.request.call_count == 3


@pytest.mark.parametrize("method", ["archive_message", "delete_message"])
def test_network_failure_after_post_is_uncertain_and_never_retried(method):
    child, http = _setup(_page(), _response(MESSAGE), requests.Timeout("lost reply"))
    with pytest.raises(NetworkError, match="may have succeeded"):
        _call(child, method, 123)
    assert len(_posts(http)) == 1
    assert http.session.request.call_count == 3


@pytest.mark.parametrize("method", ["archive_message", "delete_message"])
@pytest.mark.parametrize("response", [
    _response("<html>Login</html>"), _response("", 404),
    _response(False), _response([]), requests.Timeout("lost verification"),
])
def test_failed_verification_reports_uncertainty_without_repeating_post(method, response):
    child, http = _setup(_page(), _response(MESSAGE), _response("", 204), response)
    with pytest.raises(ParseError, match="may have succeeded, but verification failed"):
        _call(child, method, 123)
    assert len(_posts(http)) == 1


@pytest.mark.parametrize("method", ["archive_message", "delete_message"])
def test_http_success_without_effect_is_not_success(method):
    child, http = _setup(_page(), _response(MESSAGE), _response(True), _response(MESSAGE))
    with pytest.raises(ParseError, match="before retrying"):
        _call(child, method, 123)
    assert len(_posts(http)) == 1


def test_archive_must_leave_message_visible():
    child, _ = _setup(_page(), _response(MESSAGE), _response(True), _response(None))
    with pytest.raises(ParseError, match="archive copy could not be verified"):
        child.archive_message(123, mode="copy")


def test_mutation_and_lookup_bypass_stale_disk_cache(tmp_path):
    lookup_url = PROVIDER["GetMessageForThreadlessConversationUrl"] + "?messageId=123"
    child, http = _setup(
        _page(), _response(MESSAGE), _response(True), _response(None), cache_dir=tmp_path,
    )
    http._cache_write("GET", BASE + "conversations", _response("stale login page"))
    http._cache_write("GET", lookup_url, _response({**MESSAGE, "Id": 456}))

    child.delete_message(123)

    assert http.session.request.call_count == 4
    assert http._cache_read("GET", lookup_url).json()["Id"] == 456


def test_message_detail_flags_keep_legacy_constructor_and_missing_values():
    legacy = MessageDetail("1", "Subject", "Sender", "Date", "Body", [], [], "Expiry")
    assert legacy.is_archived is None
    assert legacy.is_outbox is None
    minimal = parse_message_detail_json('{"Id": 1}')[0]
    assert minimal.is_archived is None
    assert minimal.is_outbox is None
    parsed = parse_message_detail_json(json.dumps([MESSAGE]))[0]
    assert parsed.is_archived is False
    assert parsed.is_outbox is False


ARCHIVED = {**MESSAGE, "IsCopiedToArchive": True}
COPY_URL = BASE + "archive/message/77"


def _archive_list(*, link=COPY_URL, subject=MESSAGE["Subject"], sender=MESSAGE["SenderName"]):
    return _response(
        '<div class="sk-messages-list" data-clientlogic-settings-messages="{}">'
        f'<li class="sk-message-list-item"><a href="{escape(link, quote=True)}">'
        f'<div class="sk-message-title">{escape(subject)}</div>'
        f'<li class="sk-message-senderrecipient-name">{escape(sender)}</li>'
        '</a></li></div>'
    )


def _archive_detail(*, body=MESSAGE["BaseText"], date=MESSAGE["SentReceivedDateText"], attachment=""):
    return _response(
        f'<div class="sk-message-subject-text">{escape(MESSAGE["Subject"])}</div>'
        f'<div class="sk-message-senderrecipient-name"><span>{escape(MESSAGE["SenderName"])}</span></div>'
        f'<div class="sk-message-send-date"><span>{escape(date)}</span></div>'
        f'<div class="sk-message-text">{body}</div>{attachment}'
    )


def test_archive_defaults_to_move_after_verifying_content_then_checks_copy_again():
    child, http = _setup(
        _page(), _response(MESSAGE), _response(""), _response(ARCHIVED),
        _archive_list(link=COPY_URL + "?pageIndex=1"), _archive_detail(),
        _response(ARCHIVED), _response(1), _archive_detail(),
    )

    result = child.archive_message(123)

    assert result.id == "123" and result.is_archived is True
    posts = _posts(http)
    assert [p.args[1] for p in posts] == [
        PROVIDER["CopyConversationMessageToArchiveUrl"], PROVIDER["BatchDeleteConversationUrl"],
    ]
    assert posts[0].kwargs["data"] == {"messageId": 123, "isOutbox": "false"}
    assert posts[1].kwargs["data"] == {"MessageIds[]": [123]}
    calls = http.session.request.call_args_list
    assert calls[5].args == ("GET", COPY_URL)
    assert calls[-1].args == ("GET", COPY_URL)
    assert all(c.kwargs["allow_redirects"] is False for c in calls)


def test_move_reuses_existing_copy_without_creating_duplicate():
    child, http = _setup(
        _page(), _response(ARCHIVED), _archive_list(), _archive_detail(),
        _response(ARCHIVED), _response(1), _archive_detail(),
    )
    assert child.archive_message(123, mode="move").is_archived is True
    assert len(_posts(http)) == 1
    assert _posts(http)[0].args[1] == PROVIDER["BatchDeleteConversationUrl"]


def test_move_searches_later_archive_pages_and_keeps_original_search_filter():
    first_page = _archive_list().text + f'<a href="{BASE}archive/2?searchRequest=other">Next</a>'
    child, http = _setup(
        _page(), _response(ARCHIVED), _response(first_page), _archive_detail(body="Other content"),
        _archive_list(), _archive_detail(), _response(ARCHIVED), _response(1), _archive_detail(),
    )
    child.archive_message(123)
    assert http.session.request.call_args_list[4].args == (
        "GET", BASE + "archive/2?searchRequest=Udflugt+sidste+sommer",
    )


def test_archive_pagination_never_leaves_the_child():
    listing = _response(
        '<div class="sk-messages-list" data-clientlogic-settings-messages="{}"></div>'
        '<a href="https://other/parent/1/Child/messages/archive/2">Next</a>'
    )
    child, http = _setup(_page(), _response(ARCHIVED), listing)
    with pytest.raises(ParseError, match="pagination link"):
        child.archive_message(123)
    assert _posts(http) == []
    assert http.session.request.call_count == 3


@pytest.mark.parametrize("mode", [None, False, "", "MOVE", "delete", [], 1])
def test_archive_mode_is_validated_before_any_io(mode):
    child, http = _setup()
    with pytest.raises(ValueError, match="mode"):
        child.archive_message(123, mode=mode)
    http.session.request.assert_not_called()


@pytest.mark.parametrize("value", [True, 0, -1, [], "thread-uuid"])
def test_move_rejects_invalid_ids_before_io(value):
    child, http = _setup()
    with pytest.raises(ValueError, match="Message ID"):
        child.archive_message(value)
    http.session.request.assert_not_called()


def test_move_checks_delete_permission_before_creating_a_copy():
    child, http = _setup(_page(), _response({**MESSAGE, "ActionButtons": []}))
    with pytest.raises(NotAuthorizedError):
        child.archive_message(123)
    assert _posts(http) == []


def test_move_checks_delete_endpoint_before_creating_a_copy():
    child, http = _setup(_page({**PROVIDER, "BatchDeleteConversationUrl": "https://other/delete"}), _response(MESSAGE))
    with pytest.raises(ParseError, match="endpoint"):
        child.archive_message(123)
    assert _posts(http) == []


@pytest.mark.parametrize("detail", [
    _archive_detail(body="Different message"), _archive_detail(date="26. jun. 2026"),
    _response("Login"), _response("", 500),
])
def test_archive_flag_alone_cannot_authorize_deletion(detail):
    child, http = _setup(_page(), _response(ARCHIVED), _archive_list(), detail)
    with pytest.raises(ParseError, match="move could not be confirmed"):
        child.archive_message(123)
    assert _posts(http) == []


@pytest.mark.parametrize("listing", [
    _response("Login"), _response("", 500),
    _response('<div class="sk-messages-list" data-clientlogic-settings-messages="{}"></div>'),
    _archive_list(subject="Other subject"), _archive_list(sender="Other sender"),
])
def test_missing_archive_copy_preserves_inbox_even_after_copy_post(listing):
    child, http = _setup(_page(), _response(MESSAGE), _response(""), _response(ARCHIVED), listing)
    with pytest.raises(ParseError, match="move could not be confirmed"):
        child.archive_message(123)
    assert len(_posts(http)) == 1
    assert _posts(http)[0].args[1] == PROVIDER["CopyConversationMessageToArchiveUrl"]


@pytest.mark.parametrize("link", [
    "https://other/messages/archive/message/77",
    "https://school/parent/2/Other/messages/archive/message/77",
    BASE + "archive/message/delete/77", COPY_URL + "#fragment",
])
def test_archive_copy_link_must_stay_on_this_childs_read_route(link):
    child, http = _setup(_page(), _response(ARCHIVED), _archive_list(link=link))
    with pytest.raises(ParseError, match="unexpected origin"):
        child.archive_message(123)
    assert _posts(http) == []
    assert http.session.request.call_count == 3


@pytest.mark.parametrize("current", [None, MESSAGE, {**ARCHIVED, "BaseText": "Changed"}])
def test_changed_inbox_message_is_not_deleted(current):
    child, http = _setup(
        _page(), _response(ARCHIVED), _archive_list(), _archive_detail(), _response(current),
    )
    with pytest.raises(ParseError, match="changed while preparing"):
        child.archive_message(123)
    assert _posts(http) == []


def test_permission_can_be_revoked_after_copy_verification():
    child, http = _setup(
        _page(), _response(ARCHIVED), _archive_list(), _archive_detail(),
        _response({**ARCHIVED, "ActionButtons": []}),
    )
    with pytest.raises(ParseError, match="delete action"):
        child.archive_message(123)
    assert _posts(http) == []


@pytest.mark.parametrize("failure,exception", [
    (_response("", 500), ParseError), (_response(0), ParseError),
    (requests.Timeout("lost delete response"), NetworkError),
])
def test_move_delete_failure_is_reported_as_partial_or_uncertain_without_retry(failure, exception):
    child, http = _setup(
        _page(), _response(MESSAGE), _response(""), _response(ARCHIVED),
        _archive_list(), _archive_detail(), _response(ARCHIVED), failure,
    )
    with pytest.raises(exception, match="move could not be confirmed"):
        child.archive_message(123)
    assert len(_posts(http)) == 2


def test_move_checks_that_copy_survived_deletion():
    child, http = _setup(
        _page(), _response(ARCHIVED), _archive_list(), _archive_detail(),
        _response(ARCHIVED), _response(1), _response("", 404),
    )
    with pytest.raises(ParseError, match="move could not be confirmed"):
        child.archive_message(123)
    assert len(_posts(http)) == 1


def test_archive_preserves_date_but_may_lose_time_of_day():
    from pyskoleintra.parsers.messages import archive_message_matches

    original = parse_message_detail_json(json.dumps({
        **ARCHIVED, "SentReceivedDateText": "Torsdag, 25. jun. 2026 12:26",
    }))[0]
    assert archive_message_matches(_archive_detail(date="Torsdag, 25. jun. 2026 00:00").text, original)
    assert not archive_message_matches(_archive_detail(date="Fredag, 26. jun. 2026 00:00").text, original)


def test_move_does_not_delete_when_attachments_cannot_be_verified():
    message = {**ARCHIVED, "AttachmentsLinks": [{"Name": "Note.pdf", "Url": "/attachment/1"}]}
    child, http = _setup(_page(), _response(message), _archive_list(), _archive_detail())
    with pytest.raises(ParseError, match="No matching readable archive copy"):
        child.archive_message(123)
    assert _posts(http) == []
