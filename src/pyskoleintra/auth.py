"""SAML authentication flow for Skoleintra.

Handles the multi-step login process:
1. GET /Account/IdpLogin — fetch login form with CSRF token
2. POST credentials — receive 302 to SSO completion page
3. GET SSO completion — parse SAML assertion form
4. POST SAML assertion — receive 302 redirect to parent URL
5. Extract parent path (e.g. /parent/1234/Oliver) from final redirect
"""

from __future__ import annotations

import logging
import re

from .exceptions import AuthenticationError, MaintenanceError
from .http import HttpSession
from .parsers.common import parse_first_form

logger = logging.getLogger(__name__)


def authenticate(
    http: HttpSession,
    base_url: str,
    school_url: str,
    username: str,
    password: str,
) -> str:
    """Run the full SAML login flow and return the parent URL path.

    Returns:
        The parent path, e.g. ``/parent/1234/Oliver``.

    Raises:
        AuthenticationError: If any step of the login flow fails.
        MaintenanceError: If Skoleintra is in maintenance mode.
    """
    login_url = (
        f"{base_url}/Account/IdpLogin"
        f"?partnerSp=urn%3Aitslearning%3Ansi%3Asaml%3A2.0%3A{school_url}.m.skoleintra.dk"
    )

    # Step 1: Fetch login page
    resp = http.get(login_url)

    if resp.status_code == 302:
        location = resp.headers.get("Location", "")
        if "/updating" in location:
            raise MaintenanceError("Skoleintra is in maintenance mode")

    if resp.status_code != 200:
        raise AuthenticationError(f"Login page returned HTTP {resp.status_code}")

    # Step 2: Parse login form and submit credentials
    login_form = parse_first_form(resp.text)
    if not login_form:
        raise AuthenticationError("Could not parse login form")

    login_form["inputs"]["UserName"] = username
    login_form["inputs"]["Password"] = password

    action_url = login_form["form"]["action"]
    if not action_url.startswith("http"):
        action_url = base_url + action_url

    resp = http.post(action_url, data=login_form["inputs"])

    if resp.status_code != 302:
        raise AuthenticationError("Login POST did not redirect — invalid credentials?")

    # Step 3: Follow redirect to SSO completion page
    sso_url = resp.headers["Location"]
    if not sso_url.startswith("http"):
        sso_url = base_url + sso_url

    logger.debug("SSO redirect -> %s", sso_url)
    resp = http.get(sso_url)

    if resp.status_code != 200:
        raise AuthenticationError(f"SSO completion page returned HTTP {resp.status_code}")

    # Step 4: Parse SSO SAML form and submit
    sso_form = parse_first_form(resp.text)
    if not sso_form:
        raise AuthenticationError("Could not parse SSO completion form")

    sso_action = sso_form["form"]["action"]
    if not sso_action.startswith("http"):
        sso_action = base_url + sso_action

    logger.debug("Posting SAML assertion to %s", sso_action)
    resp = http.post(sso_action, data=sso_form["inputs"])

    if resp.status_code not in (302, 303):
        raise AuthenticationError("SAML assertion POST did not redirect")

    # Step 5: Extract parent URL from final redirect
    final_url = resp.headers.get("Location", "")
    parent_path = _extract_parent_path(final_url, base_url)

    if not parent_path:
        raise AuthenticationError(f"Could not extract parent path from redirect: {final_url}")

    logger.debug("Authenticated — parent path: %s", parent_path)

    # Follow the final redirect to complete the session setup
    if not final_url.startswith("http"):
        final_url = base_url + final_url
    http.get(final_url)

    return parent_path


def _extract_parent_path(url: str, base_url: str) -> str | None:
    """Extract /parent/{id}/{name} from a URL or redirect path."""
    # Remove base URL if present
    path = url.replace(base_url, "")
    # Remove trailing /Index
    path = re.sub(r"/Index$", "", path)
    # Validate it looks like a parent path
    if re.match(r"/parent/\d+/\w+", path):
        return path
    return None
