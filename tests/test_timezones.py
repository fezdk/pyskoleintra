from datetime import date, datetime, timedelta, timezone
import json
import os
import time
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
import requests

from pyskoleintra import Child, ChildInfo, Skoleintra
from pyskoleintra.dates import in_timezone
from pyskoleintra.http import HttpSession
from pyskoleintra.parsers.calendar import parse_calendar_events
from pyskoleintra.parsers.homework import parse_homework
from pyskoleintra.parsers.schedule import parse_lesson_events
from test_sfo import DASHBOARD_HTML


CPH = ZoneInfo("Europe/Copenhagen")


def response(body):
    result = requests.Response()
    result.status_code = 200
    result._content = body.encode()
    result.encoding = "utf-8"
    return result


@pytest.mark.parametrize("zone_name", ["Europe/Copenhagen", "UTC", "America/New_York"])
def test_instance_timezone_is_inherited_by_every_child_and_tabulex(monkeypatch, zone_name):
    from pyskoleintra import client as client_module
    infos = [ChildInfo("One", 1, "/parent/1/One"), ChildInfo("Two", 2, "/parent/2/Two")]
    monkeypatch.setattr(client_module, "parse_children_from_page", lambda *args: infos)
    client = Skoleintra("school", source_timezone=zone_name)
    client._http.get = Mock(return_value=response(""))
    client._discover_children("/parent/1/One")
    assert len(client.children) == 2
    for child in client.children:
        assert child.source_timezone is client.source_timezone
        assert child.tabulex.source_timezone is client.source_timezone
        assert child.source_timezone.key == zone_name
    assert Skoleintra("school").source_timezone.key == "Europe/Copenhagen"


@pytest.mark.parametrize("value", ["not/a-zone", "eu/copenhagen", "", None, 123])
def test_invalid_instance_timezone_fails_immediately(value):
    with pytest.raises((ValueError, ZoneInfoNotFoundError)):
        Skoleintra("school", source_timezone=value)


@pytest.mark.parametrize("month, offset", [(1, 1), (7, 2)])
def test_calendar_and_schedule_use_instance_zone_for_real_instants(month, offset):
    instant = datetime(2026, month, 3, 23, 30, tzinfo=timezone.utc)
    start_ms = int(instant.timestamp() * 1000)
    end_ms = start_ms + 30 * 60 * 1000
    data = json.dumps([{"startDate": f"/Date({start_ms})/", "endDate": end_ms}])
    event = parse_calendar_events(data, source_timezone=CPH)[0]
    assert event.start.utcoffset() == timedelta(hours=offset)
    assert event.start.day == 4
    assert event.start.timestamp() == instant.timestamp()
    lesson_data = json.dumps([{"startDate": f"/Date({start_ms})/", "endDate": f"/Date({end_ms})/"}])
    cph = parse_lesson_events(lesson_data, source_timezone=CPH)[0]
    utc = parse_lesson_events(lesson_data, source_timezone=ZoneInfo("UTC"))[0]
    assert cph.date.date() == date(2026, month, 4)
    assert utc.date.date() == date(2026, month, 3)
    assert cph.lessons[0].time == ("00:30-01:00" if offset == 1 else "01:30-02:00")
    assert utc.lessons[0].time == "23:30-00:00"


def test_calendar_iso_offsets_and_naive_input():
    data = json.dumps([{"start": "2026-07-01T10:00:00", "end": "2026-07-01T09:00:00Z"}])
    event = parse_calendar_events(data, source_timezone=CPH)[0]
    assert event.start.isoformat() == "2026-07-01T10:00:00+02:00"
    assert event.end.isoformat() == "2026-07-01T11:00:00+02:00"
    assert parse_calendar_events('[{"start": "2026-03-29T02:30:00", "end": "2026-03-29T04:00:00"}]') == []


@pytest.mark.parametrize("value", [datetime(2026, 3, 29, 2, 30), datetime(2026, 10, 25, 2, 30)])
def test_ambiguous_or_nonexistent_naive_query_time_is_rejected(value):
    http = HttpSession()
    http.session.request = Mock()
    child = Child(ChildInfo("Child", 1, "/parent/1/Child"), "https://school", http)
    with pytest.raises(ValueError, match="aware datetime"):
        child.calendar_events(start=value)
    http.session.request.assert_not_called()


def test_explicit_fold_can_be_used_for_query():
    value = datetime(2026, 10, 25, 2, 30, tzinfo=CPH, fold=1)
    assert in_timezone(value, CPH).timestamp() == value.timestamp()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="Host timezone test requires tzset")
@pytest.mark.parametrize("method", ["calendar_events", "schedule"])
def test_query_windows_ignore_host_timezone(method):
    previous = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "Pacific/Honolulu"
        time.tzset()
        http = HttpSession()
        http.session.request = Mock(side_effect=(
            [response("LessonsEvents?className=02Y&test=1"), response("[]")]
            if method == "schedule" else [response("[]")]
        ))
        child = Child(ChildInfo("Child", 1, "/parent/1/Child"), "https://school", http,
                      source_timezone="Europe/Copenhagen")
        start = datetime(2026, 3, 28)
        end = datetime(2026, 3, 30)
        getattr(child, method)(start=start, end=end)
        query = parse_qs(urlsplit(http.session.request.call_args.args[1]).query)
        assert int(query["start"][0]) == int(start.replace(tzinfo=CPH).timestamp())
        assert int(query["end"][0]) == int(end.replace(tzinfo=CPH).timestamp())
        assert int(query["end"][0]) - int(query["start"][0]) == 47 * 3600
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def test_homework_preserves_calendar_day_with_instance_zone():
    html = '''<div id="sk-diary-notes-container"><ul class="sk-list"><li>
        <div class="sk-white-box"><b>Mandag 22 juni 2026</b></div><table><tbody>
        <tr><th>Fag</th><th>Opgave</th></tr><tr><td>Dansk</td><td>Læs bogen</td></tr>
        </tbody></table></li></ul></div>'''
    entry = parse_homework(html, source_timezone=ZoneInfo("UTC"))[0]
    assert entry.date == datetime(2026, 6, 22, tzinfo=timezone.utc)


def test_tabulex_agenda_uses_original_fetch_date_in_instance_zone(monkeypatch):
    http = HttpSession()
    body = DASHBOARD_HTML.replace("Fredag 14 august", "Fredag 1 januar")
    reply = response(body)
    reply.headers["Date"] = "Thu, 31 Dec 2026 23:30:00 GMT"
    http.session.request = Mock(return_value=reply)
    fetched = http.get("https://sfo.example/guardian")
    child = Child(ChildInfo("Child", 1, "/parent/1/Child"), "https://school", http)
    monkeypatch.setattr(child.tabulex, "_page", lambda: fetched)
    assert child.tabulex.agenda()[0].date == date(2027, 1, 1)
    assert child.tabulex.dashboard().appointments[0].date == date(2027, 1, 1)
    assert child.tabulex.overview().dashboard.appointments[0].date == date(2027, 1, 1)


def test_tabulex_legacy_cache_does_not_guess_a_year(monkeypatch):
    child = Child(ChildInfo("Child", 1, "/parent/1/Child"), "https://school", HttpSession())
    monkeypatch.setattr(child.tabulex, "_page", lambda: response(DASHBOARD_HTML))
    assert child.tabulex.agenda() == []
    assert child.tabulex.dashboard().status == "Gået hjem kl. 14:04"
