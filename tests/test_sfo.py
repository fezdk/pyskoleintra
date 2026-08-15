from __future__ import annotations

from dataclasses import replace
from datetime import date, time

import pytest

from pyskoleintra import (
    Tabulex,
    TabulexAppointmentInput,
    TabulexRecurrence,
    TabulexWeekday,
)
from pyskoleintra.child import Child
from pyskoleintra.models import ChildInfo
from pyskoleintra.parsers.sfo import parse_sfo_page
from pyskoleintra.parsers.tabulex import (
    parse_tabulex_capabilities,
    parse_tabulex_holiday_periods,
    parse_tabulex_identity_preferences,
    parse_tabulex_navigation,
    parse_tabulex_appointment_types,
    parse_tabulex_appointments_page,
    parse_tabulex_dashboard,
)
from pyskoleintra.sso import follow_sso, is_sso_form


DASHBOARD_HTML = """
<html><body>
  <div class="panel"><h4 class="panel-title">Status</h4>
    <div class="panel-body">Gået hjem kl. 14:04</div></div>
  <div class="panel"><h4 id="news_header_1004" class="panel-title">Efterårsferie 2026</h4>
    <div class="panel-body">Tilmelding er åbnet.</div></div>
  <div class="panel"><h4 class="panel-title">Uge 33</h4><div class="panel-body">
    <div id="agenda_plan"><table class="agenda">
      <tr class="day"><th>Fredag 14 august</th></tr>
      <tr class="info"><td><div class="agenda__time">I dag</div>
        <div class="agenda__header">Hentes af en forælder</div></td></tr>
    </table></div>
  </div></div>
  <div class="panel"><h4 class="panel-title">Fødselsdage</h4>
    <div class="panel-body">Ingen fødselsdage.</div></div>
  <div class="panel"><h4 class="panel-title">Billeder</h4>
    <div class="panel-body"><a href="/guardian/gallery">Nyt billedgalleri</a></div></div>
</body></html>
"""


APPOINTMENT_TYPES_HTML = """
<select id="create_appointment_what">
  <option value="">Klik her</option>
  <option value="7-4">Gå hjem</option>
  <option value="9-4">Hentes</option>
</select>
"""


def _child(http) -> Child:
    return Child(ChildInfo("Child", 1, "/parent/1/Child"), "https://school", http)


def test_parse_sfo_frontpage_sections_and_dynamic_tabulex_link():
    html = """
    <table><tr><td>Opslagstavle Velkommen i SFO</td></tr></table>
    <table><tr><td>Ugeplan Husk madpakke</td></tr></table>
    <table><tr><td>Nyt fra SFO Sommerfest fredag</td></tr></table>
    <a href="/dynamic/sso?target=tabulex&amp;id=99">Tabulex SFO</a>
    """
    result = parse_sfo_page(html, "https://school.example")
    assert result.tabulex_url == "/dynamic/sso?target=tabulex&id=99"
    assert "Velkommen" in result.notice_board
    assert "madpakke" in result.weekly_plan
    assert "Sommerfest" in result.news
    assert result.shortcuts["Tabulex SFO"].endswith("id=99")


def test_parse_tabulex_dashboard_boxes():
    result = parse_tabulex_dashboard(DASHBOARD_HTML)
    assert result.status == "Gået hjem kl. 14:04"
    assert result.news[0].title == "Efterårsferie 2026"
    assert result.week_label == "Uge 33"
    assert result.birthday_message == "Ingen fødselsdage."
    assert result.birthdays == []
    assert result.galleries == ["Nyt billedgalleri"]
    assert len(result.appointments) == 1
    assert result.appointments[0].date.month == 8
    assert result.appointments[0].title == "Hentes af en forælder"
    assert result.appointments[0].time_text == "I dag"


def test_parse_tabulex_navigation_and_capabilities():
    html = """
    <ul class="nav-mainmenu__list">
      <li><a href="/guardian/news">Opslagstavle</a></li>
      <li><a href="/guardian/messages">Beskeder <span class="badge">2</span></a></li>
      <li><a href="/guardian/appointments">Aftaler</a></li>
    </ul>
    <form id="form_editappointment_header"></form>
    <form id="form_holiday_header"></form>
    <form id="form_updatechildactivity"></form>
    <div id="sickbox_header"></div>
    """
    navigation = parse_tabulex_navigation(html)
    assert [(item.title, item.path, item.badge_count) for item in navigation] == [
        ("Opslagstavle", "/guardian/news", 0),
        ("Beskeder", "/guardian/messages", 2),
        ("Aftaler", "/guardian/appointments", 0),
    ]
    capabilities = parse_tabulex_capabilities(html)
    assert capabilities.can_create_appointment
    assert capabilities.can_report_sick
    assert capabilities.can_create_day_off
    assert capabilities.can_update_activity


def test_parse_appointment_types_and_typed_appointments():
    types = parse_tabulex_appointment_types(APPOINTMENT_TYPES_HTML)
    assert [(item.id, item.name) for item in types] == [
        ("7-4", "Gå hjem"),
        ("9-4", "Hentes"),
    ]

    html = """
    <table><tbody>
      <tr><td>Fre 14/08-26</td><td>Gå hjem</td><td>kl. 13:45</td></tr>
      <tr><td>Man 17/08-26</td><td>Hentes af forælder</td><td>kl. 14:00</td><td>Ja</td>
        <td><button onclick="popdiv('#delete_container12345')">Slet</button></td></tr>
    </tbody></table>
    """
    items = parse_tabulex_appointments_page(html)
    assert [item.date.isoformat() for item in items] == ["2026-08-14", "2026-08-17"]
    assert items[0].kind == "Gå hjem"
    assert items[0].start_time == time(13, 45)
    assert items[1].id == "12345"
    assert items[1].kind == "Hentes"
    assert items[1].pickup == "forælder"
    assert items[1].start_time == time(14, 0)


def test_parse_full_editable_appointment_view():
    html = """
    <table><tr>
      <td>Tor 20/08-26</td><td>Hentes af forælder</td><td>kl. 15:00</td>
      <td><button data-target="#edit_container54321">Rediger</button></td>
    </tr></table>
    <select id="edit_what54321"><option value="9-4" selected>Hentes</option></select>
    <input id="edit_pickup54321" value="forælder">
    <input id="edit_description54321" value="Ring ved ankomst">
    <select id="edit_beforestarttime_hours54321"><option selected value="14">14</option></select>
    <select id="edit_beforestarttime_minutes54321"><option selected value="00">00</option></select>
    <select id="edit_starttime_hours54321"><option selected value="15">15</option></select>
    <select id="edit_starttime_minutes54321"><option selected value="00">00</option></select>
    <select id="edit_endtime_hours54321"><option selected value="16">16</option></select>
    <select id="edit_endtime_minutes54321"><option selected value="15">15</option></select>
    <input id="edit_enddate54321" value="30/09-26">
    <select id="edit_type54321"><option selected value="1">Hver uge</option></select>
    <select id="edit_ignoreonholiday54321"><option selected value="0">Ja</option></select>
    <input id="monday54321" type="checkbox" value="1" checked>
    <input id="thursday54321" type="checkbox" value="8" checked>
    <input id="edit_transport54321" value="Cykel">
    <input id="homewith_id54321" value="actor-fixture">
    """
    item = parse_tabulex_appointments_page(html)[0]
    assert item.id == "54321"
    assert item.description == "Ring ved ankomst"
    assert item.before_start_time == time(14, 0)
    assert item.end_time == time(16, 15)
    assert item.end_date == date(2026, 9, 30)
    assert item.recurrence is TabulexRecurrence.WEEKLY
    assert item.weekdays == (TabulexWeekday.MONDAY, TabulexWeekday.THURSDAY)
    assert item.ignore_on_holiday
    assert item.transport == "Cykel"
    assert item.owner_id == "actor-fixture"
    updated = replace(item.as_input(), start_time=time(15, 30))
    assert updated.start_time == time(15, 30)
    assert updated.end_date == date(2026, 9, 30)
    assert updated.weekdays == (TabulexWeekday.MONDAY, TabulexWeekday.THURSDAY)
    assert updated.transport == "Cykel"


def test_parse_holiday_period_and_identity_preferences():
    html = """
    <h3>Efterårsferie 2026</h3>
    <table class="reportedHoliday__table">
      <tr><th>Kommer</th><th>Fri</th><th>Dato</th><th>Tidspunkt</th></tr>
      <tr>
        <td><input type="radio"
          name="tx_tmsfo_pi1[formdata][holidays][1424][holidayattending]"
          value="1" checked></td>
        <td><input type="radio"
          name="tx_tmsfo_pi1[formdata][holidays][1424][holidayattending]"
          value="0"></td>
        <td>12/10-26<input type="hidden"
          name="tx_tmsfo_pi1[formdata][holidays][1424][holidaydate]"
          value="12/10-26"></td>
        <td>
          <input id="holidaytime_start_value_1424_0" value="0630">
          <input id="holidaytime_end_value_1424_0" value="1700">
          <input type="hidden"
            name="tx_tmsfo_pi1[formdata][holidays][1424][joint_care]" value="1">
        </td>
      </tr>
    </table>
    <input type="checkbox"
      name="tx_tmsfo_pi1[formdata][childshowonpubliclists]" checked>
    <input type="checkbox"
      name="tx_tmsfo_pi1[formdata][childshowbirthday]" checked>
    <input type="checkbox"
      name="tx_tmsfo_pi1[formdata][childshowpictureinfoboard]">
    """
    periods = parse_tabulex_holiday_periods(html)
    assert len(periods) == 1
    assert periods[0].title == "Efterårsferie 2026"
    day = periods[0].days[0]
    assert day.id == "1424"
    assert day.date == date(2026, 10, 12)
    assert day.attending is True
    assert day.start_time == time(6, 30)
    assert day.end_time == time(17, 0)
    assert day.joint_care

    preferences = parse_tabulex_identity_preferences(html)
    assert preferences.show_on_public_lists
    assert preferences.show_birthday
    assert not preferences.show_picture_on_info_board


def test_only_identity_provider_forms_are_sso_forms():
    assert is_sso_form(
        {"inputs": {"SAMLResponse": "fixture-assertion", "RelayState": "fixture-state"}}
    )
    assert not is_sso_form(
        {"inputs": {"tx_tmsfo_pi1[formdata][action]": "save"}}
    )


def test_follow_sso_resolves_relative_action_and_stops_at_application_form():
    class Response:
        def __init__(self, url, text):
            self.url = url
            self.text = text
            self.status_code = 200

    start = Response(
        "https://identity.example/login",
        """
        <form action="/consume">
          <input name="SAMLResponse" value="fixture-assertion">
          <input name="RelayState" value="fixture-state">
        </form>
        """,
    )
    application = Response(
        "https://sfo.example/",
        """
        <form action="/guardian/appointments">
          <input name="tx_tmsfo_pi1[formdata][action]" value="save">
        </form>
        """,
    )

    class FakeHttp:
        def __init__(self):
            self.posts = []

        def follow_redirects(self, response):
            return response

        def post(self, url, data=None):
            self.posts.append((url, data))
            return application

    http = FakeHttp()
    result = follow_sso(http, start)
    assert result is application
    assert http.posts == [
        (
            "https://identity.example/consume",
            {"SAMLResponse": "fixture-assertion", "RelayState": "fixture-state"},
        )
    ]


def test_child_exposes_one_tabulex_client():
    child = _child(object())
    assert isinstance(child.tabulex, Tabulex)
    assert child.tabulex is child.tabulex


def test_overview_uses_one_landing_page_response(monkeypatch):
    class Response:
        text = f"""
        {DASHBOARD_HTML}
        {APPOINTMENT_TYPES_HTML}
        <ul class="nav-mainmenu__list">
          <li><a href="/guardian/news">Opslagstavle</a></li>
        </ul>
        <form id="form_editappointment_header"></form>
        """

    tabulex = _child(object()).tabulex
    calls = []

    def landing_page(_path=None):
        calls.append(_path)
        return Response()

    monkeypatch.setattr(tabulex, "_page", landing_page)
    overview = tabulex.overview()
    assert calls == [None]
    assert overview.dashboard.status == "Gået hjem kl. 14:04"
    assert [item.name for item in overview.appointment_types] == ["Gå hjem", "Hentes"]
    assert overview.navigation[0].path == "/guardian/news"
    assert overview.capabilities.can_create_appointment


def test_appointment_input_rejects_non_quarter_hour_times():
    with pytest.raises(ValueError, match="15-minute"):
        TabulexAppointmentInput(
            date=date(2026, 8, 20),
            start_time=time(14, 10),
            kind="Hentes",
        )


def test_recurring_appointment_requires_end_date_and_weekdays():
    with pytest.raises(ValueError, match="end date and weekdays"):
        TabulexAppointmentInput(
            date=date(2026, 8, 20),
            start_time=time(14, 0),
            kind="Hentes",
            recurrence=TabulexRecurrence.WEEKLY,
        )


def test_create_appointment_posts_typed_payload_once(monkeypatch):
    class Response:
        url = "https://sfo.example/?tenant=fixture"
        text = f"""
        {APPOINTMENT_TYPES_HTML}
        <form id="form_editappointment_header" method="post">
          <input name="tx_tmsfo_pi1[formdata][uid]" value="123">
          <input name="tx_tmsfo_pi1[formdata][action]" value="editappointment">
        </form>
        """

    class FakeHttp:
        def __init__(self):
            self.posts = []

        def post(self, url, data=None, **_kwargs):
            self.posts.append((url, data))
            return Response()

    http = FakeHttp()
    tabulex = _child(http).tabulex
    monkeypatch.setattr(tabulex, "_page", lambda _path=None: Response())
    tabulex.create_appointment(
        TabulexAppointmentInput(
            date=date(2026, 8, 20),
            start_time=time(14, 30),
            kind="Hentes",
            pickup="Forælder",
        )
    )

    assert len(http.posts) == 1
    assert http.posts[0][0] == "https://sfo.example/guardian/appointments"
    payload = http.posts[0][1]
    assert payload["tx_tmsfo_pi1[formdata][eventtypeid]"] == "9-4"
    assert payload["tx_tmsfo_pi1[formdata][eventstartdate]"] == "20/08-26"
    assert payload["tx_tmsfo_pi1[formdata][eventstarttime]"] == "1430"
    assert payload["tx_tmsfo_pi1[formdata][eventpickup]"] == "Forælder"
    assert payload["eID"] == "ajax"


def test_delete_rejects_non_numeric_id_before_http():
    class NoHttp:
        def get(self, *_args, **_kwargs):
            raise AssertionError("HTTP must not be called")

        def post(self, *_args, **_kwargs):
            raise AssertionError("HTTP must not be called")

    with pytest.raises(ValueError, match="numeric"):
        _child(NoHttp()).tabulex.delete_appointment("not-an-id")


def test_delete_appointment_posts_supplied_id_once(monkeypatch):
    class Response:
        url = "https://sfo.example/guardian/appointments"
        text = """
        <form id="form_editappointment">
          <input name="tx_tmsfo_pi1[formdata][uid]" value="123">
          <input name="tx_tmsfo_pi1[formdata][action]" value="editappointment">
          <input name="tx_tmsfo_pi1[formdata][eventplannedid]" value="">
          <input name="tx_tmsfo_pi1[formdata][eventowner]" value="">
        </form>
        """

    class FakeHttp:
        def __init__(self):
            self.posts = []

        def post(self, url, data=None, **_kwargs):
            self.posts.append((url, data))
            return Response()

    http = FakeHttp()
    tabulex = _child(http).tabulex
    monkeypatch.setattr(tabulex, "_page", lambda _path=None: Response())
    tabulex.delete_appointment("12345")

    assert len(http.posts) == 1
    assert http.posts[0][1]["tx_tmsfo_pi1[formdata][eventplannedid]"] == "12345"
    assert http.posts[0][1]["eID"] == "ajax"


def test_update_appointment_preserves_method_id(monkeypatch):
    class Response:
        url = "https://sfo.example/guardian/appointments"
        text = f"""
        {APPOINTMENT_TYPES_HTML}
        <form id="form_editappointment">
          <input name="tx_tmsfo_pi1[formdata][uid]" value="123">
          <input name="tx_tmsfo_pi1[formdata][action]" value="editappointment">
          <input name="tx_tmsfo_pi1[formdata][eventplannedid]" value="99999">
        </form>
        """

    class FakeHttp:
        def __init__(self):
            self.posts = []

        def post(self, url, data=None, **_kwargs):
            self.posts.append((url, data))
            return Response()

    http = FakeHttp()
    tabulex = _child(http).tabulex
    monkeypatch.setattr(tabulex, "_page", lambda _path=None: Response())
    tabulex.update_appointment(
        "67890",
        TabulexAppointmentInput(
            date=date(2026, 8, 20),
            start_time=time(15, 0),
            kind="Hentes",
            pickup="Forælder",
            end_time=time(16, 0),
            end_date=date(2026, 9, 30),
            recurrence=TabulexRecurrence.WEEKLY,
            weekdays=(TabulexWeekday.THURSDAY,),
            ignore_on_holiday=False,
            transport="Cykel",
            owner_id="actor-fixture",
        ),
    )

    assert len(http.posts) == 1
    assert http.posts[0][1]["tx_tmsfo_pi1[formdata][eventplannedid]"] == "67890"
    assert http.posts[0][1]["tx_tmsfo_pi1[formdata][eventstarttime]"] == "1500"
    assert http.posts[0][1]["tx_tmsfo_pi1[formdata][eventendtime]"] == "1600"
    assert http.posts[0][1]["tx_tmsfo_pi1[formdata][eventenddate]"] == "30/09-26"
    assert http.posts[0][1]["tx_tmsfo_pi1[formdata][eventtype]"] == "1"
    assert http.posts[0][1]["tx_tmsfo_pi1[formdata][eventrulebyday]"] == "8"
    assert http.posts[0][1]["tx_tmsfo_pi1[formdata][eventignoreonholiday]"] == "1"
    assert http.posts[0][1]["tx_tmsfo_pi1[formdata][eventtransport]"] == "Cykel"
    assert http.posts[0][1]["tx_tmsfo_pi1[formdata][eventowner]"] == "actor-fixture"
