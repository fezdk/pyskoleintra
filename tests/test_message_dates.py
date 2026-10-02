from datetime import date, datetime, timedelta, timezone
from html import escape
import json
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
import requests

from pyskoleintra import Child, ChildInfo
from pyskoleintra.http import HttpSession, response_fetched_at
from pyskoleintra.parsers.message_dates import date_fields
from pyskoleintra.parsers import messages


CPH = ZoneInfo("Europe/Copenhagen")
FETCHED = datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("raw, expected, precision", [
    ("Mandag, 22. jun. 2026 12:26", "2026-06-22T12:26:00+02:00", "minute"),
    ("torsdag 1 oktober 2026 kl. 9:05:02", "2026-10-01T09:05:02+02:00", "second"),
    ("2026-01-02T01:30Z", "2026-01-02T02:30:00+01:00", "minute"),
    ("2026-10-01T08:15:01.123456+00:00", "2026-10-01T10:15:01.123456+02:00", "microsecond"),
    ("I dag kl. 8:15", "2026-10-01T08:15:00+02:00", "minute"),
    ("I går, 23:45", "2026-09-30T23:45:00+02:00", "minute"),
    # An explicit offset disambiguates the repeated hour in October.
    ("2026-10-25T02:30:00+01:00", "2026-10-25T02:30:00+01:00", "second"),
])
def test_known_instants(raw, expected, precision):
    result = date_fields(raw, source_timezone=CPH, fetched_at=FETCHED)
    assert result["timestamp"].isoformat() == expected
    assert result["calendar_date"] == date.fromisoformat(expected[:10])
    assert result["date_precision"] == precision


@pytest.mark.parametrize("raw, expected", [
    ("1. okt. 2026", date(2026, 10, 1)),
    ("2026-01-02", date(2026, 1, 2)),
    ("I går", date(2026, 9, 30)),
    ("2026-03-29T02:30", date(2026, 3, 29)),  # spring gap
    ("2026-10-25T02:30", date(2026, 10, 25)),  # autumn fold
])
def test_day_precision_never_invents_midnight(raw, expected):
    assert date_fields(raw, source_timezone=CPH, fetched_at=FETCHED) == {
        "timestamp": None, "calendar_date": expected, "date_precision": "day",
    }


@pytest.mark.parametrize("raw", [
    "23. sep.", "07:16", "", "garbage", "2026-02-29", "2026-10-01T24:01",
    "Tirsdag, 22. jun. 2026 12:26", "2026-10-01T10:00+99:00", "1. ukendt 2026",
    "2026-10-01T10:00+00:99",
    "/Date(1774821600000)/", 1774821600000, None,
])
def test_unknown_values_are_not_guessed(raw):
    assert date_fields(raw, source_timezone=CPH, fetched_at=FETCHED) == {
        "timestamp": None, "calendar_date": None, "date_precision": None,
    }


@pytest.mark.parametrize("fetched_at", [None, datetime(2026, 10, 1)])
def test_relative_dates_require_aware_original_context(fetched_at):
    assert date_fields("I dag 12:00", source_timezone=CPH, fetched_at=fetched_at)["calendar_date"] is None


def test_timezone_and_machine_precedence():
    assert date_fields("22. jun. 2026 12:26")["timestamp"] is None
    result = date_fields("22. jun.", source_timezone=CPH, machine_value="2026-06-21T23:30Z")
    assert result["timestamp"].isoformat() == "2026-06-22T01:30:00+02:00"
    assert date_fields("2026-06-22", machine_value="nonsense")["calendar_date"] == date(2026, 6, 22)
    # Even a machine value must not lend spurious precision to archive data.
    result = date_fields("22. jun.", source_timezone=CPH,
                         machine_value="2026-06-22T00:00:00", day_only=True)
    assert result == {"timestamp": None, "calendar_date": date(2026, 6, 22), "date_precision": "day"}


def test_all_message_parsers_preserve_raw_and_normalize():
    raw = "Mandag, 22. jun. 2026 12:26"
    conversation = {"Date": raw, "ThreadId": "uuid", "LatestMessageId": 123}
    data = json.dumps({"Conversations": [conversation]})
    html = '<div data-clientlogic-settings-messageconversationswitharchivefunctionality="' + escape(data) + '"></div>'
    detail_html = f'''<div class="sk-message-subject-text">Test</div>
        <div class="sk-message-send-date"><span>{raw}</span></div>'''
    unread_html = f'''<div class="sk-messages-list"><li class="sk-message-list-item">
        <a href="/message/123">Test</a><ul><li class="sk-message-send-date">{raw}</li></ul></li></div>'''
    results = [
        messages.parse_inbox_conversations(html, source_timezone=CPH)[0],
        messages.parse_inbox_page_json(data, source_timezone=CPH)[0],
        messages.parse_message_detail_json(json.dumps({"SentReceivedDateText": raw}), source_timezone=CPH)[0],
        messages.parse_message_detail_html(detail_html, "123", source_timezone=CPH),
        messages.parse_unread_messages_list(unread_html, source_timezone=CPH)[0],
    ]
    for result in results:
        assert result.date == raw
        assert result.timestamp.utcoffset() == timedelta(hours=2)
        assert result.calendar_date == date(2026, 6, 22)
        assert result.date_precision == "minute"


def response(body, server_date="Wed, 30 Sep 2026 23:30:00 GMT"):
    result = requests.Response()
    result.status_code = 200
    result._content = body.encode()
    result.encoding = "utf-8"
    result.headers["Date"] = server_date
    return result


def test_cache_uses_original_context_across_sessions_and_timezones(tmp_path):
    http = HttpSession(cache_dir=str(tmp_path))
    body = json.dumps({"Conversations": [{"Date": "I dag 09:00"}]})
    http.session.request = Mock(return_value=response(body))
    child = Child(ChildInfo("Child", 1, "/parent/1/Child"), "https://school", http)
    first = child.inbox(before_message_id=123)[0]
    assert first.calendar_date == date(2026, 10, 1)

    later = HttpSession(cache_dir=str(tmp_path))
    later.session.request = Mock(side_effect=AssertionError("Cache hit must not make a request"))
    child = Child(child._info, "https://school", later, source_timezone="UTC")
    cached = child.inbox(before_message_id=123)[0]
    assert cached.calendar_date == date(2026, 9, 30)
    assert response_fetched_at(later.last_response) == FETCHED
    assert http.session.request.call_count == 1


@pytest.mark.parametrize("metadata", ["missing", "corrupt", "mismatch", "naive"])
def test_old_or_invalid_cache_context_does_not_become_today(tmp_path, metadata):
    http = HttpSession(cache_dir=str(tmp_path))
    http.session.request = Mock(return_value=response("I dag"))
    url = "https://school/messages"
    http.get(url)
    sidecar = next(tmp_path.glob("*.json"))
    if metadata == "missing":
        sidecar.unlink()
    elif metadata == "corrupt":
        sidecar.write_text("bad json")
    else:
        data = json.loads(sidecar.read_text())
        data["sha256" if metadata == "mismatch" else "fetched_at"] = (
            "bad hash" if metadata == "mismatch" else "2026-10-01T12:00:00"
        )
        sidecar.write_text(json.dumps(data))
    cached = http.get(url)
    assert response_fetched_at(cached) is None
    assert date_fields(cached.text, source_timezone=CPH, fetched_at=response_fetched_at(cached))["timestamp"] is None
    assert http.session.request.call_count == 1


def test_network_response_without_server_date_has_aware_fetch_context():
    http = HttpSession()
    http.session.request = Mock(return_value=response("I dag", "invalid"))
    before = datetime.now(timezone.utc)
    fetched = response_fetched_at(http.get("https://school/messages"))
    assert before <= fetched <= datetime.now(timezone.utc)
