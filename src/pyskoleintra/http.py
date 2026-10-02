"""HTTP session wrapper for Skoleintra with cookie persistence and SAML redirect handling."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Callable

import requests

from .exceptions import NetworkError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def response_fetched_at(response: requests.Response | None) -> datetime | None:
    """Original response time; never substitute today's date for an old cache."""
    value = getattr(response, "_pyskoleintra_fetched_at", None)
    return value if isinstance(value, datetime) and value.utcoffset() is not None else None


def _record_response_time(response: requests.Response) -> None:
    value = datetime.now(timezone.utc)
    try:
        server_time = parsedate_to_datetime(response.headers.get("Date", ""))
        if server_time.utcoffset() is not None:
            value = server_time
    except (TypeError, ValueError, OverflowError):
        pass
    response._pyskoleintra_fetched_at = value


class HttpSession:
    """Persistent HTTP session with cookie jar, redirect control, and optional re-login."""

    def __init__(self, cookie_file: str | None = None, cache_dir: str | None = None):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._cookie_file = cookie_file
        self._last_response: requests.Response | None = None
        self._cache_dir = cache_dir

        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

        if cookie_file:
            self._load_cookies(cookie_file)

    @property
    def last_response(self) -> requests.Response | None:
        return self._last_response

    def get(
        self,
        url: str,
        *,
        allow_redirects: bool = False,
        relogin_callback: Callable[[], bool] | None = None,
        use_cache: bool = True,
    ) -> requests.Response:
        """Send a GET request, optionally handling login redirects.

        ``use_cache=False`` bypasses both cache reads and writes for this call.
        """
        return self._request("GET", url, allow_redirects=allow_redirects,
                             relogin_callback=relogin_callback, use_cache=use_cache)

    def post(
        self,
        url: str,
        data: dict | None = None,
        *,
        allow_redirects: bool = False,
        relogin_callback: Callable[[], bool] | None = None,
    ) -> requests.Response:
        """Send a POST request with form data."""
        return self._request("POST", url, data=data, allow_redirects=allow_redirects,
                             relogin_callback=relogin_callback)

    def _cache_key(self, method: str, url: str) -> str:
        """Generate a filesystem-safe cache key from method + URL."""
        h = hashlib.sha256(f"{method}:{url}".encode()).hexdigest()[:16]
        # Also keep a human-readable prefix from the URL path
        from urllib.parse import urlparse
        path = urlparse(url).path.strip("/").replace("/", "_")[:80]
        return f"{path}__{h}"

    def _cache_path(self, key: str) -> str:
        return os.path.join(self._cache_dir, key + ".html")  # type: ignore[arg-type]

    # URLs matching these substrings are never cached (auth/SSO flow)
    _CACHE_EXCLUDE = ("Account/", "/sso/", "IdpLogin", "login", "saml", "adfs")

    def _should_cache(self, url: str) -> bool:
        """Check whether a URL is eligible for caching."""
        return not any(exc.lower() in url.lower() for exc in self._CACHE_EXCLUDE)

    def _cache_read(self, method: str, url: str) -> requests.Response | None:
        """Return a fake Response from cache if available."""
        if not self._cache_dir or not self._should_cache(url):
            return None
        path = self._cache_path(self._cache_key(method, url))
        if not os.path.exists(path):
            return None
        logger.debug("CACHE HIT: %s %s -> %s", method, url, path)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        resp = requests.Response()
        resp.status_code = 200
        resp._content = text.encode("utf-8")  # noqa: SLF001
        resp.encoding = "utf-8"
        resp.url = url
        try:
            with open(path + ".json", encoding="utf-8") as f:
                metadata = json.load(f)
            if metadata["sha256"] == hashlib.sha256(resp.content).hexdigest():
                fetched_at = datetime.fromisoformat(metadata["fetched_at"])
                if fetched_at.utcoffset() is not None:
                    resp._pyskoleintra_fetched_at = fetched_at
        except (OSError, ValueError, TypeError, KeyError):
            # Legacy, corrupt or mismatched metadata has unknown context.
            pass
        return resp

    def _cache_write(self, method: str, url: str, resp: requests.Response) -> None:
        """Save a response body to the cache directory."""
        if not self._cache_dir or resp.status_code != 200 or not self._should_cache(url):
            return
        key = self._cache_key(method, url)
        path = self._cache_path(key)
        logger.debug("CACHE WRITE: %s %s -> %s", method, url, path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(resp.text)
        fetched_at = response_fetched_at(resp)
        # Bind metadata to the exact UTF-8 cache body. A partial or concurrent
        # write cannot attach a stale date to a different response body.
        with open(path + ".json", "w", encoding="utf-8") as f:
            json.dump({
                "sha256": hashlib.sha256(resp.text.encode("utf-8")).hexdigest(),
                "fetched_at": fetched_at.isoformat() if fetched_at else None,
            }, f)

    def _request(
        self,
        method: str,
        url: str,
        data: dict | None = None,
        allow_redirects: bool = False,
        relogin_callback: Callable[[], bool] | None = None,
        use_cache: bool = True,
    ) -> requests.Response:
        # Mutations and their verification reads must bypass the development cache.
        if use_cache and method == "GET" and data is None:
            cached = self._cache_read(method, url)
            if cached is not None:
                self._last_response = cached
                return cached

        logger.debug("%s %s", method, url)
        try:
            resp = self.session.request(
                method, url, data=data, allow_redirects=allow_redirects, timeout=30
            )
        except requests.RequestException as exc:
            raise NetworkError(f"Request failed: {exc}") from exc

        self._last_response = resp

        # If we got a redirect to a login page, try re-login once
        if (
            resp.status_code in (302, 303)
            and relogin_callback
            and "login" in resp.headers.get("Location", "").lower()
        ):
            logger.debug("Redirected to login, attempting re-login")
            if relogin_callback():
                try:
                    resp = self.session.request(
                        method, url, data=data, allow_redirects=allow_redirects, timeout=30
                    )
                except requests.RequestException as exc:
                    raise NetworkError(f"Request failed after re-login: {exc}") from exc
                self._last_response = resp

        _record_response_time(resp)

        # Cache successful GET responses
        if use_cache and method == "GET" and data is None:
            self._cache_write(method, url, resp)

        return resp

    def follow_redirects(
        self, response: requests.Response, *, max_redirects: int = 10
    ) -> requests.Response:
        """Manually follow a chain of 302/303 redirects."""
        from urllib.parse import urljoin

        count = 0
        while response.status_code in (302, 303) and count < max_redirects:
            count += 1
            location = response.headers.get("Location", "")
            if not location:
                break
            # Resolve relative redirects against the request URL
            if not location.startswith("http"):
                base = str(response.url) if response.url else ""
                location = urljoin(base, location)
            logger.debug("Following redirect %d -> %s", count, location)
            response = self.get(location)
        self._last_response = response
        return response

    def save_cookies(self) -> None:
        """Persist cookies to disk in Netscape cookie-jar format."""
        if not self._cookie_file:
            return
        lines = ["# Netscape HTTP Cookie File"]
        for cookie in self.session.cookies:
            secure = "TRUE" if cookie.secure else "FALSE"
            http_only = "#HttpOnly_" if cookie.has_nonstandard_attr("HttpOnly") else ""
            domain_initial_dot = "TRUE" if cookie.domain.startswith(".") else "FALSE"
            lines.append(
                f"{http_only}{cookie.domain}\t{domain_initial_dot}\t{cookie.path}\t"
                f"{secure}\t{cookie.expires or 0}\t{cookie.name}\t{cookie.value}"
            )
        with open(self._cookie_file, "w") as f:
            f.write("\n".join(lines) + "\n")

    def _load_cookies(self, path: str) -> None:
        """Load cookies from a Netscape cookie-jar file if it exists."""
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("# "):
                        continue
                    # Strip HttpOnly prefix
                    if line.startswith("#HttpOnly_"):
                        line = line[len("#HttpOnly_"):]
                    parts = line.split("\t")
                    if len(parts) >= 7:
                        domain, _, path_val, secure, expires, name, value = parts[:7]
                        self.session.cookies.set(
                            name,
                            value,
                            domain=domain,
                            path=path_val,
                            secure=secure == "TRUE",
                        )
        except FileNotFoundError:
            pass

    def get_cookie(self, name: str, domain: str | None = None) -> str | None:
        """Get a cookie value by name, optionally filtered by domain."""
        for cookie in self.session.cookies:
            if cookie.name == name:
                if domain is None or domain in cookie.domain:
                    return cookie.value
        return None

    def get_cookie_expiry(self, name: str, domain: str | None = None) -> int | None:
        """Get a cookie's expiry timestamp."""
        for cookie in self.session.cookies:
            if cookie.name == name:
                if domain is None or domain in cookie.domain:
                    return cookie.expires
        return None
