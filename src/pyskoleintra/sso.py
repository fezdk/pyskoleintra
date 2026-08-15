"""Shared helpers for HTML form based SAML/WS-Federation handshakes."""

from __future__ import annotations

from .parsers.common import parse_first_form, resolve_url

_SSO_FIELD_NAMES = {
    "samlrequest",
    "samlresponse",
    "relaystate",
    "wa",
    "wctx",
    "wresult",
}


def is_sso_form(form: dict) -> bool:
    """Return whether a parsed form contains known identity-provider fields."""
    names = {str(name).lower() for name in form.get("inputs", {})}
    return bool(names & _SSO_FIELD_NAMES)


def follow_sso(http, response, *, max_steps: int = 10):
    """Follow redirects and identity-provider forms, never application forms."""
    response = http.follow_redirects(response)
    for _ in range(max_steps):
        if response.status_code != 200:
            break
        form = parse_first_form(response.text)
        if not form or not is_sso_form(form):
            break
        action = resolve_url(str(response.url or ""), form["form"]["action"])
        response = http.post(action, data=form["inputs"])
        response = http.follow_redirects(response)
    return response
