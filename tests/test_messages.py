from __future__ import annotations

import pytest

from pyskoleintra.child import Child
from pyskoleintra.exceptions import ParseError
from pyskoleintra.models import ChildInfo


class _Response:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code


class _Http:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code
        self.posts: list[tuple[str, object]] = []

    def post(self, url: str, data=None, **_kwargs):
        self.posts.append((url, data))
        return _Response(self.status_code)

    def invalidate_get_cache_prefix(self, prefix):
        assert prefix == "https://school/parent/1/Child/messages/"


def _child(http: _Http) -> Child:
    return Child(
        ChildInfo("Child", 1, "/parent/1/Child"),
        "https://school",
        http,
    )


def test_mark_messages_unread_uses_status_endpoint_with_false_flag():
    http = _Http()

    _child(http).mark_messages_unread(["123", 456])

    assert http.posts == [
        (
            "https://school/parent/1/Child/messages/changemessagestatus"
            "?messageIds=123&messageIds=456&readFlag=false",
            None,
        )
    ]


def test_mark_messages_read_accepts_one_id_and_uses_true_flag():
    http = _Http()

    _child(http).mark_messages_read(123)

    assert http.posts == [
        (
            "https://school/parent/1/Child/messages/changemessagestatus"
            "?messageIds=123&readFlag=true",
            None,
        )
    ]


@pytest.mark.parametrize("message_ids", [[], "", "not-a-number", ["123", "bad"]])
def test_message_status_rejects_missing_or_non_numeric_ids(message_ids):
    http = _Http()

    with pytest.raises(ValueError, match="[Mm]essage ID"):
        _child(http).mark_messages_unread(message_ids)

    assert http.posts == []


def test_message_status_raises_on_upstream_error():
    http = _Http(status_code=500)

    with pytest.raises(ParseError, match="HTTP 500"):
        _child(http).mark_messages_read(123)
