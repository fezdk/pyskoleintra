"""Attachment files stay binary and use only the existing school session."""
import json
from unittest.mock import Mock, call

import pytest
import requests
from requests.adapters import BaseAdapter

from pyskoleintra import Attachment, NetworkError, NotAuthorizedError, ParseError
from pyskoleintra.child import Child
from pyskoleintra.http import HttpSession
from pyskoleintra.models import ChildInfo

BASE = "https://school.example"
URL = BASE + "/file/messageattach/example/plan.pdf"
PDF = b"%PDF-1.7\r\n\x00\xff\xfe\x80binary file\r\n%%EOF"


def response(body=PDF, status=200, content_type="application/pdf", **headers):
    result = requests.Response()
    result.status_code, result._content = status, body
    result.headers.update({"Content-Type": content_type, **headers})
    return result


def child(http=None):
    http = http if http is not None else Mock()
    return Child(ChildInfo("Child", 1, "/parent/1/Child"), BASE, http), http


@pytest.mark.parametrize("attachment", [
    Attachment("plan.pdf", "/file/messageattach/example/plan.pdf"),
    "/file/messageattach/example/plan.pdf", "file/messageattach/example/plan.pdf", URL,
    "https://school.example:443/file/messageattach/example/plan.pdf",
])
def test_download_one_attachment_or_url_returns_exact_bytes(attachment):
    instance, http = child()
    http.get.return_value = response()
    assert instance.download_attachment(attachment) == PDF
    http.get.assert_called_once()
    assert http.get.call_args.kwargs == {"use_cache": False, "allow_redirects": False}
    http.post.assert_not_called()


def test_every_attachment_in_every_message_is_retained_even_with_duplicate_names():
    instance, http = child()
    first, second, third = "/file/messageattach/1/plan.pdf", "/file/messageattach/2/photo.png", "/file/messageattach/3/plan.pdf"
    thread = [
        {"Id": 10, "AttachmentsLinks": [
            {"Text": "plan.pdf", "HrefAttributeValue": first},
            {"Text": "photo.png", "HrefAttributeValue": second},
        ]},
        {"Id": 11, "AttachmentsLinks": [{"Text": "plan.pdf", "HrefAttributeValue": third}]},
    ]
    bodies = [PDF, b"\x89PNG\r\n\x1a\n\x00\xffimage bytes", b"%PDF-1.7 another plan"]
    http.get.side_effect = [response(json.dumps(thread).encode(), content_type="application/json"),
                            response(bodies[0]), response(bodies[1], content_type="image/png"),
                            response(bodies[2])]
    messages = instance.message(11, source="thread", thread_id="example-thread")
    assert [len(m.attachments) for m in messages] == [2, 1]
    downloaded = [(m.id, a.name, instance.download_attachment(a))
                  for m in messages for a in m.attachments]
    assert downloaded == [("10", "plan.pdf", bodies[0]), ("10", "photo.png", bodies[1]),
                          ("11", "plan.pdf", bodies[2])]
    assert http.get.call_args_list[1:] == [
        call(BASE + path, use_cache=False, allow_redirects=False) for path in (first, second, third)]
    http.post.assert_not_called()


def test_original_query_and_escaped_filename_are_preserved():
    instance, http = child()
    http.get.return_value = response()
    url = BASE + "/file/messageattach/1/For%C3%A6ldre%20%26%20b%C3%B8rn.pdf?token=opaque%2Bvalue&download=1"
    instance.download_attachment(url)
    assert http.get.call_args.args == (url,)


@pytest.mark.parametrize("url", [
    "", "https://elsewhere.example/file/1.pdf", "//elsewhere.example/file/1.pdf",
    "http://school.example/file/1.pdf", "https://school.example:444/file/1.pdf",
    "https://user:password@school.example/file/1.pdf", "file:///tmp/private",
    "data:application/pdf;base64,JVBERi0=", "javascript:void(0)",
    "/parent/1/Child/messages/delete/12", "/file/../parent/1/Child/delete",
    "/file/%2e%2e/parent/delete", "/file/%252e%252e/parent/delete",
    "/file/%5c..%5cparent/delete", "/file/1.pdf\nInjected", "/file/%0aInjected",
    "/file/1.pdf#fragment", "https://school.example@elsewhere.example/file/1.pdf",
    "https://school.example.elsewhere.example/file/1.pdf",
])
def test_unsupported_urls_are_rejected_before_network(url):
    instance, http = child()
    with pytest.raises(ValueError):
        instance.download_attachment(url)
    http.get.assert_not_called()


@pytest.mark.parametrize("value", [None, 123, [], {"url": URL}, Attachment("bad", None)])
def test_wrong_argument_types_fail_before_network(value):
    instance, http = child()
    with pytest.raises(TypeError):
        instance.download_attachment(value)
    http.get.assert_not_called()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_same_school_file_redirects_are_followed_without_cache(status):
    instance, http = child()
    http.get.side_effect = [response(b"", status, Location="other.pdf"), response()]
    assert instance.download_attachment(URL) == PDF
    assert http.get.call_args_list == [
        call(URL, use_cache=False, allow_redirects=False),
        call(BASE + "/file/messageattach/example/other.pdf", use_cache=False, allow_redirects=False),
    ]


@pytest.mark.parametrize("location,error", [
    ("/Account/Login?returnUrl=%2Ffile%2F1", NotAuthorizedError),
    ("https://identity.example/signin", NotAuthorizedError),
    ("https://other.example/file/1.pdf", ParseError),
    ("http://school.example/file/1.pdf", ParseError),
    ("/parent/1/Child/messages/delete/1", ParseError),
    ("/file/%2e%2e/admin", ParseError),
    ("https://user:password@school.example/file/1", ParseError),
    ("https://[invalid-host/file/1", ParseError),
])
def test_unacceptable_redirect_is_not_requested(location, error):
    instance, http = child()
    http.get.return_value = response(b"", 302, Location=location)
    with pytest.raises(error):
        instance.download_attachment(URL)
    http.get.assert_called_once()


def test_redirect_loop_and_redirect_limit():
    instance, http = child()
    http.get.return_value = response(b"", 302, Location=URL)
    with pytest.raises(ParseError, match="loop"):
        instance.download_attachment(URL)
    http.get.assert_called_once()
    http.get.reset_mock()
    http.get.side_effect = [response(b"", 302, Location=f"/file/redirect/{n}") for n in range(6)]
    with pytest.raises(ParseError, match="five redirects"):
        instance.download_attachment(URL)
    assert http.get.call_count == 6


def test_redirect_without_location_fails():
    instance, http = child()
    http.get.return_value = response(b"", 302)
    with pytest.raises(ParseError, match="Location"):
        instance.download_attachment(URL)


@pytest.mark.parametrize("status,error", [
    (401, NotAuthorizedError), (403, NotAuthorizedError),
    (404, NetworkError), (500, NetworkError), (204, NetworkError), (206, NetworkError),
])
def test_http_failures_never_return_error_page_bytes(status, error):
    instance, http = child()
    http.get.return_value = response(b"Error page", status, content_type="text/html")
    with pytest.raises(error):
        instance.download_attachment(URL)
    http.get.assert_called_once()


@pytest.mark.parametrize("content_type", ["text/html; charset=utf-8", "application/octet-stream"])
@pytest.mark.parametrize("body", [
    b'<!DOCTYPE html><html><form><input type="password" name="Password"></form></html>',
    b'<form action="/Account/Login"><input name="username"></form>',
])
def test_login_page_returned_with_200_is_not_a_file(body, content_type):
    instance, http = child()
    http.get.return_value = response(body, content_type=content_type)
    with pytest.raises(NotAuthorizedError, match="login"):
        instance.download_attachment(URL)


def test_unexpected_html_is_rejected_but_explicit_html_attachment_is_allowed():
    instance, http = child()
    body = b"<!doctype html><html><body>School information</body></html>"
    http.get.return_value = response(body, content_type="text/html")
    with pytest.raises(ParseError, match="HTML"):
        instance.download_attachment(URL)
    http.get.return_value.headers["Content-Disposition"] = 'attachment; filename="information.html"'
    assert instance.download_attachment(URL) == body


@pytest.mark.parametrize("body,content_type", [
    (b"", "application/octet-stream"),
    (b"\xff\xfe\x00\x80", "application/octet-stream"),
    (b"Information\r\n", "text/plain; charset=iso-8859-1"),
    (b'<?xml version="1.0"?><svg></svg>', "image/svg+xml"),
    (b"PK\x03\x04\xff\x00document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
])
def test_file_formats_are_not_decoded_or_limited_to_pdf(body, content_type):
    instance, http = child()
    http.get.return_value = response(body, content_type=content_type)
    assert instance.download_attachment(URL) == body


def test_transport_error_is_propagated_without_retry():
    instance, http = child()
    http.get.side_effect = NetworkError("connection failed")
    with pytest.raises(NetworkError):
        instance.download_attachment(URL)
    http.get.assert_called_once()


class FileAdapter(BaseAdapter):
    def __init__(self):
        self.requests = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        result = response()
        result.request, result.url = request, request.url
        return result

    def close(self):
        pass


def test_existing_login_cookie_and_binary_bytes_bypass_html_cache(tmp_path):
    http = HttpSession(cache_dir=str(tmp_path))
    http.session.cookies.set("User", "fake-existing-session", domain="school.example", secure=True)
    http._cache_write("GET", URL, response(b"stale decoded cache"))
    original_cache = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    adapter = FileAdapter()
    http.session.mount("https://", adapter)
    instance, _ = child(http)
    assert instance.download_attachment(Attachment("../never-written.pdf", URL)) == PDF
    assert len(adapter.requests) == 1
    assert adapter.requests[0].headers["Cookie"] == "User=fake-existing-session"
    assert adapter.requests[0].method == "GET"
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == original_cache
