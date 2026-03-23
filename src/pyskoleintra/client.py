"""Main Skoleintra client — entry point for authentication and child discovery."""

from __future__ import annotations

import logging
import re
import time

from .auth import authenticate
from .child import Child
from .exceptions import NotAuthorizedError, SessionExpiredError
from .http import HttpSession
from .models import ChildInfo
from .parsers.frontpage import parse_children_from_page

logger = logging.getLogger(__name__)


class Skoleintra:
    """Client for the Skoleintra Danish school intranet platform.

    Handles authentication, session management, and child discovery.
    Each child's data is accessed through :class:`Child` instances.

    Example::

        client = Skoleintra("myschool")
        client.login("username", "password")

        for child in client.children:
            print(child.name)
            for entry in child.homework():
                print(f"  {entry.date}: {entry.subject} — {entry.description}")
    """

    def __init__(self, school: str, *, cookie_file: str | None = None, cache_dir: str | None = None):
        """
        Args:
            school: The school subdomain (e.g. ``"myschool"`` for
                ``myschool.m.skoleintra.dk``).
            cookie_file: Optional path to a Netscape cookie-jar file for
                session persistence across runs.
            cache_dir: Optional directory to cache HTTP responses on disk.
                When set, GET responses are saved and served from cache on
                subsequent runs — useful for developing parsers offline.
        """
        self._school = school
        self._base_url = f"https://{school}.m.skoleintra.dk"
        self._http = HttpSession(cookie_file, cache_dir=cache_dir)
        self._children: list[Child] = []
        self._username: str | None = None
        self._password: str | None = None
        self._primary_parent_path: str | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def school(self) -> str:
        return self._school

    @property
    def children(self) -> list[Child]:
        """List of children discovered after login."""
        return list(self._children)

    @property
    def is_authenticated(self) -> bool:
        """Check if we have a valid session based on the User cookie."""
        domain = f"{self._school}.m.skoleintra.dk"
        expiry = self._http.get_cookie_expiry("User", domain)
        if expiry and expiry > time.time():
            return True
        return False

    def login(self, username: str, password: str) -> list[Child]:
        """Authenticate with Skoleintra and discover available children.

        Args:
            username: Skoleintra username.
            password: Skoleintra password.

        Returns:
            List of :class:`Child` instances — one per child on the account.

        Raises:
            AuthenticationError: If login fails.
        """
        self._username = username
        self._password = password

        parent_path = authenticate(
            self._http, self._base_url, self._school, username, password,
        )
        self._primary_parent_path = parent_path

        # Save cookies after successful login
        self._http.save_cookies()

        # Discover children from the frontpage
        self._discover_children(parent_path)

        logger.info(
            "Logged in to %s — found %d child(ren): %s",
            self._school,
            len(self._children),
            ", ".join(c.name for c in self._children),
        )

        return self._children

    def resume_session(self, parent_path: str | None = None) -> list[Child]:
        """Resume a previous session using persisted cookies (no re-login).

        Args:
            parent_path: The parent path (e.g. ``/parent/1234/Oliver``).
                If not provided, attempts to discover it from cookies.

        Returns:
            List of :class:`Child` instances.

        Raises:
            SessionExpiredError: If the session cookie has expired.
        """
        if not self.is_authenticated:
            raise SessionExpiredError("Session cookie expired — call login() instead")

        if parent_path:
            self._primary_parent_path = parent_path
            self._discover_children(parent_path)
        elif self._primary_parent_path:
            self._discover_children(self._primary_parent_path)
        else:
            raise NotAuthorizedError("No parent path available — call login() first")

        return self._children

    def child(self, name: str) -> Child:
        """Get a child by name.

        Args:
            name: The child's name (case-insensitive).

        Raises:
            KeyError: If no child with that name exists.
        """
        for c in self._children:
            if c.name.lower() == name.lower():
                return c
        available = ", ".join(c.name for c in self._children)
        raise KeyError(f"No child named {name!r} — available: {available}")

    def _discover_children(self, primary_path: str) -> None:
        """Discover all children by fetching the frontpage and looking for parent paths."""
        # Always include the primary child from login
        match = re.match(r"/parent/(\d+)/(\w+)", primary_path)
        if not match:
            return

        primary_info = ChildInfo(
            name=match.group(2),
            parent_id=int(match.group(1)),
            parent_path=primary_path,
        )

        # Fetch frontpage to discover additional children
        try:
            resp = self._http.get(f"{self._base_url}{primary_path}/Index")
            if resp.status_code == 200:
                discovered = parse_children_from_page(resp.text)
                # Merge: use discovered list if it contains the primary child, else prepend primary
                known_paths = {c.parent_path for c in discovered}
                if primary_path not in known_paths:
                    discovered.insert(0, primary_info)
                child_infos = discovered
            else:
                child_infos = [primary_info]
        except Exception:
            logger.warning("Could not discover children from frontpage, using primary only")
            child_infos = [primary_info]

        # Create relogin callback
        relogin_cb = None
        if self._username and self._password:
            def _relogin():
                try:
                    authenticate(
                        self._http, self._base_url, self._school,
                        self._username, self._password,
                    )
                    return True
                except Exception:
                    return False
            relogin_cb = _relogin

        self._children = [
            Child(info, self._base_url, self._http)
            for info in child_infos
        ]
