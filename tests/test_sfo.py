from __future__ import annotations

import pytest

from pyskoleintra.child import Child
from pyskoleintra.models import ChildInfo
from pyskoleintra.parsers.sfo import (
    parse_sfo_page,
    parse_tabulex_appointments_page,
    parse_tabulex_dashboard,
)


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
    assert result.appointments[0].fields["header"] == "Hentes af en forælder"


def test_parse_guardian_appointments_across_weeks():
    html = """
    <table><tbody>
      <tr><td>Fre 14/08-26</td><td>Hentes af forælder A</td><td>kl. 14:00</td></tr>
      <tr><td>Man 17/08-26</td><td>Hentes af forælder B</td><td>kl. 14:00</td><td>Ja</td>
        <td><button onclick="popdiv('#delete_container12345')">Slet</button></td></tr>
    </tbody></table>
    """
    items = parse_tabulex_appointments_page(html)
    assert [item.date.date().isoformat() for item in items] == [
        "2026-08-14",
        "2026-08-17",
    ]
    assert "Hentes af forælder B" in items[1].fields["summary"]
    assert "14:00" in items[1].fields["summary"]
    assert items[1].fields["eventplannedid"] == "12345"


def test_only_identity_provider_forms_are_sso_forms():
    assert Child._is_sso_form(
        {"inputs": {"SAMLResponse": "fixture-assertion", "RelayState": "fixture-state"}}
    )
    assert not Child._is_sso_form(
        {"inputs": {"tx_tmsfo_pi1[formdata][action]": "save"}}
    )


def test_delete_forms_are_not_exposed_by_modal_submitter():
    child = Child(ChildInfo("Child", 1, "/parent/1/Child"), "https://school", object())
    with pytest.raises(ValueError, match="Unsupported"):
        child._tabulex_submit_modal("form_redirect_appointment", {})


def test_appointment_posts_once_to_forms_js_route(monkeypatch):
    class Response:
        url = "https://sfo.example/?tenant=fixture"
        text = """
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
    child = Child(ChildInfo("Child", 1, "/parent/1/Child"), "https://school", http)
    monkeypatch.setattr(child, "_tabulex_page", lambda: Response())
    child.tabulex_submit_appointment({"tx_tmsfo_pi1[formdata][eventtypeid]": "9-4"})
    assert len(http.posts) == 1
    assert http.posts[0][0] == "https://sfo.example/guardian/appointments"
    assert http.posts[0][1]["eID"] == "ajax"
    assert http.posts[0][1]["tx_tmsfo_pi1[formdata][eventtypeid]"] == "9-4"


def test_delete_rejects_non_numeric_id_before_http():
    class NoHttp:
        def get(self, *_args, **_kwargs):
            raise AssertionError("HTTP must not be called")

        def post(self, *_args, **_kwargs):
            raise AssertionError("HTTP must not be called")

    child = Child(ChildInfo("Child", 1, "/parent/1/Child"), "https://school", NoHttp())
    with pytest.raises(ValueError, match="numeric"):
        child.tabulex_delete_appointment("not-an-id")


def test_delete_posts_supplied_id_once(monkeypatch):
    class Response:
        url = "https://sfo.example/guardian/appointments"
        status_code = 200
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
    child = Child(ChildInfo("Child", 1, "/parent/1/Child"), "https://school", http)
    monkeypatch.setattr(child, "_tabulex_page", lambda _path=None: Response())
    child.tabulex_delete_appointment("12345")
    assert len(http.posts) == 1
    assert http.posts[0][0] == "https://sfo.example/guardian/appointments"
    assert http.posts[0][1]["tx_tmsfo_pi1[formdata][eventplannedid]"] == "12345"
    assert http.posts[0][1]["eID"] == "ajax"


def test_prepare_and_edit_preserve_appointment_id(monkeypatch):
    class Response:
        url = "https://sfo.example/guardian/appointments"
        status_code = 200
        text = """
        <table><tr><td>Tor 20/08-26</td><td>Hentes af forælder</td><td>kl. 14:30</td>
          <td><button onclick="popdiv('#delete_container67890')">Slet</button></td></tr></table>
        <select id="create_appointment_what"><option value="9-4">Hentes</option></select>
        <form id="form_editappointment">
          <input name="tx_tmsfo_pi1[formdata][uid]" value="123">
          <input name="tx_tmsfo_pi1[formdata][action]" value="editappointment">
          <input name="tx_tmsfo_pi1[formdata][eventplannedid]" value="">
        </form>
        """

    class FakeHttp:
        def __init__(self):
            self.posts = []

        def post(self, url, data=None, **_kwargs):
            self.posts.append((url, data))
            return Response()

    http = FakeHttp()
    child = Child(ChildInfo("Child", 1, "/parent/1/Child"), "https://school", http)
    monkeypatch.setattr(child, "_tabulex_page", lambda _path=None: Response())
    _current, values = child.tabulex_prepare_edit_appointment(
        "67890", date="20/08-2026", time="15:00", kind="Hentes", pickup="Forælder"
    )
    assert values["tx_tmsfo_pi1[formdata][eventplannedid]"] == "67890"
    assert values["tx_tmsfo_pi1[formdata][eventstarttime]"] == "1500"
    values["tx_tmsfo_pi1[formdata][eventplannedid]"] = "99999"
    child.tabulex_edit_appointment("67890", values)
    assert len(http.posts) == 1
    assert http.posts[0][0] == "https://sfo.example/guardian/appointments"
    assert http.posts[0][1]["tx_tmsfo_pi1[formdata][eventplannedid]"] == "67890"
    assert http.posts[0][1]["tx_tmsfo_pi1[formdata][eventpickup]"] == "Forælder"
    assert http.posts[0][1]["eID"] == "ajax"
