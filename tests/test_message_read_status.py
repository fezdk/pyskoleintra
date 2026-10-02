import json
from unittest.mock import Mock

import pytest
import requests

from pyskoleintra.child import Child
from pyskoleintra.http import HttpSession
from pyskoleintra.models import ChildInfo
from pyskoleintra.parsers.messages import parse_message_detail_json

BASE = "https://school/parent/1/Child/messages/"


def response(text="cached", status=200):
    item = requests.Response()
    item.status_code = status
    item._content = text.encode()
    item.encoding = "utf-8"
    return item


@pytest.mark.parametrize("value,expected", [(True, True), (False, False), (None, None), ("true", None), (1, None), (0, None)])
def test_exact_detail_flag_is_boolean_only(value, expected):
    detail = parse_message_detail_json(json.dumps({"Id": 123, "ShowUnreadIndication": value, "IsUnread": not bool(value)}))[0]
    assert detail.is_unread is expected


def test_missing_flag_never_inferred_from_conversation():
    assert parse_message_detail_json('{"Id":123,"IsUnread":true}')[0].is_unread is None


@pytest.mark.parametrize("method", ["mark_messages_read", "mark_messages_unread"])
def test_status_invalidates_only_child_origin_and_survives_restart(tmp_path, method):
    http = HttpSession(cache_dir=str(tmp_path))
    child = Child(ChildInfo("Child", 1, "/parent/1/Child"), "https://school", http)
    changed = [BASE+"unread", BASE+"conversations", BASE+"conversations/getconversationsbypageindex?takeFromRootMessageId=123"]
    untouched = [BASE.replace("https://school", "https://other-school")+"unread",
                 BASE.replace("/1/Child/", "/2/Other/")+"unread", "https://school/parent/1/Child/calendar"]
    for url in changed+untouched:
        http._cache_write("GET", url, response())
    http.session.request = Mock(return_value=response("", 204))
    getattr(child, method)(123)
    assert http.session.request.call_count == 1
    later = HttpSession(cache_dir=str(tmp_path))
    for url in changed:
        assert later._cache_read("GET", url) is None
    for url in untouched:
        assert later._cache_read("GET", url).text == "cached"
    later._cache_write("GET", changed[0], response("fresh"))
    assert later._cache_read("GET", changed[0]).text == "fresh"


def test_legacy_body_and_sidecar_are_both_invalid_after_status_change(tmp_path):
    http = HttpSession(cache_dir=str(tmp_path))
    url = BASE+"unread"
    http._cache_write("GET", url, response())
    # Old cache metadata did not record generations; no deletion of user data.
    from pathlib import Path
    sidecar = Path(http._cache_path(http._cache_key("GET", url))+".json")
    meta = json.loads(sidecar.read_text()); meta.pop("generations")
    sidecar.write_text(json.dumps(meta))
    http.invalidate_get_cache_prefix(BASE)
    assert http._cache_read("GET", url) is None
    sidecar.unlink()
    assert http._cache_read("GET", url) is None


def test_late_get_cannot_repopulate_invalidated_cache(tmp_path):
    http = HttpSession(cache_dir=str(tmp_path))
    def arriving(*args, **kwargs):
        http.invalidate_get_cache_prefix(BASE)
        return response("stale response")
    http.session.request = Mock(side_effect=arriving)
    http.get(BASE+"unread")
    assert http._cache_read("GET", BASE+"unread") is None


def test_unknown_post_outcome_invalidates_without_replay(tmp_path):
    http = HttpSession(cache_dir=str(tmp_path))
    child = Child(ChildInfo("Child", 1, "/parent/1/Child"), "https://school", http)
    http._cache_write("GET", BASE+"unread", response())
    http.session.request = Mock(side_effect=requests.Timeout("lost response"))
    from pyskoleintra.exceptions import NetworkError
    with pytest.raises(NetworkError): child.mark_messages_read(123)
    assert http.session.request.call_count == 1
    assert http._cache_read("GET", BASE+"unread") is None
