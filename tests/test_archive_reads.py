from datetime import date
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from pyskoleintra import ArchivedMessageDetail, ArchivedMessagePage, Child, ChildInfo, ParseError
from pyskoleintra.http import HttpSession


BASE = "https://school/parent/1/Child/messages/archive/"
ROW = '''<li class="sk-message-list-item"><a href="{link}">
    <div class="sk-message-title">Test &amp; ferie</div><ul>
    <li class="sk-message-senderrecipient-name">Testlærer</li>
    <li class="sk-message-send-date">22. jun.</li></ul></a></li>'''
DETAIL = '''<div class="sk-message-subject-text">Test &amp; ferie</div>
    <div class="sk-message-senderrecipient-name"><span class="semibold">Testlærer</span></div>
    <div class="sk-message-send-date"><span>Mandag, 22. jun. 2026 00:00</span></div>
    <div class="sk-message-text"><p>En arkivkopi.</p></div>
    <div class="sk-attachments-list"><a href="/file/one">Bilag</a></div>
    <form action="/parent/1/Child/messages/archive/message/delete/4803?pageIndex=1"></form>'''


def page(rows=None, links=""):
    rows = ROW.format(link=BASE + "message/4803?pageIndex=1") if rows is None else rows
    return '<div class="sk-messages-list" data-clientlogic-settings-messages="{}">' + rows + '</div>' + links


def setup(*bodies, cache_dir=None):
    http = HttpSession(cache_dir=str(cache_dir) if cache_dir else None)
    responses = []
    for body in bodies:
        status, text = body if isinstance(body, tuple) else (200, body)
        response = requests.Response()
        response.status_code = status
        response._content = text.encode()
        response.encoding = "utf-8"
        responses.append(response)
    http.session.request = Mock(side_effect=responses)
    child = Child(ChildInfo("Child", 1, "/parent/1/Child"), "https://school", http)
    return child, http


def test_list_is_one_get_and_pagination_is_explicit():
    child, http = setup(page(links=f'<a href="{BASE}2">2</a><a href="{BASE}3">3</a>'))
    result = child.archived_messages(query="Test & ferie + sommer")
    assert isinstance(result, ArchivedMessagePage)
    assert result.page == 1 and result.next_page == 2
    assert result.messages[0].archive_id == 4803
    assert result.messages[0].subject == "Test & ferie"
    assert result.messages[0].date == "22. jun."
    assert result.messages[0].timestamp is None
    assert result.messages[0].calendar_date is None  # no invented year
    assert result.messages[0].date_precision is None
    assert not hasattr(result.messages[0], "id")
    assert http.session.request.call_count == 1
    method, url = http.session.request.call_args.args
    assert method == "GET" and urlsplit(url).path.endswith("/archive/1")
    assert parse_qs(urlsplit(url).query) == {"searchRequest": ["Test & ferie + sommer"]}


def test_detail_uses_archive_namespace_with_only_day_precision():
    child, http = setup(DETAIL)
    detail = child.archived_message("004803")
    assert isinstance(detail, ArchivedMessageDetail)
    assert detail.archive_id == 4803
    assert not hasattr(detail, "id")
    assert detail.calendar_date == date(2026, 6, 22)
    assert detail.timestamp is None
    assert detail.date_precision == "day"
    assert detail.content == "En arkivkopi."
    assert detail.attachments[0].url == "/file/one"
    assert detail.sender == "Testlærer"
    assert http.session.request.call_count == 1
    assert http.session.request.call_args.args == ("GET", BASE + "message/4803")
    assert http.session.request.call_args.kwargs["allow_redirects"] is False


def test_empty_archive_and_untrusted_pagination_links():
    links = ''.join(f'<a href="{url}">Next</a>' for url in [
        "https://other/parent/1/Child/messages/archive/3",
        "https://school/parent/2/Other/messages/archive/3", BASE + "1", BASE + "0",
    ])
    child, http = setup(page("", links))
    assert child.archived_messages(page=2) == ArchivedMessagePage([], 2, None)
    assert http.session.request.call_count == 1


@pytest.mark.parametrize("body", [(302, "login"), (401, "denied"), (404, "missing"), (500, "error"), "<html>Login</html>"])
@pytest.mark.parametrize("detail", [False, True])
def test_errors_are_not_empty_or_absent(body, detail):
    child, http = setup(body)
    with pytest.raises(ParseError):
        child.archived_message(4803) if detail else child.archived_messages()
    assert http.session.request.call_count == 1
    assert http.session.request.call_args.args[0] == "GET"


@pytest.mark.parametrize("link", [
    "https://other/parent/1/Child/messages/archive/message/4803",
    "https://school/parent/2/Other/messages/archive/message/4803",
    BASE + "message/0", BASE + "message/4803/delete",
])
def test_invalid_archive_row_fails_without_following_its_link(link):
    child, http = setup(page(ROW.format(link=link)))
    with pytest.raises(ParseError):
        child.archived_messages()
    assert http.session.request.call_count == 1


def test_detail_rejects_different_archive_identity():
    child, _ = setup(DETAIL.replace("delete/4803", "delete/9999"))
    with pytest.raises(ParseError, match="identity"):
        child.archived_message(4803)


@pytest.mark.parametrize("value", [True, 0, -1, "", "abc", "4803&x=1", [4803], None])
def test_invalid_archive_id_never_makes_request(value):
    child, http = setup()
    with pytest.raises(ValueError):
        child.archived_message(value)
    http.session.request.assert_not_called()


@pytest.mark.parametrize("page_number", [0, -1, True, "2", 1.5])
def test_invalid_page_never_makes_request(page_number):
    child, http = setup()
    with pytest.raises(ValueError):
        child.archived_messages(page=page_number)
    http.session.request.assert_not_called()


@pytest.mark.parametrize("detail", [False, True])
def test_refresh_bypasses_both_cache_reads_and_writes(tmp_path, detail):
    initial = DETAIL if detail else page()
    child, http = setup(initial, initial.replace("Test &amp; ferie", "Changed"), cache_dir=tmp_path)
    def fetch(**kwargs):
        result = child.archived_message(4803, **kwargs) if detail else child.archived_messages(**kwargs)
        return result if detail else result.messages[0]
    assert fetch().subject == "Test & ferie"
    assert fetch(refresh=True).subject == "Changed"
    assert fetch().subject == "Test & ferie"
    assert http.session.request.call_count == 2
