"""Custom exceptions for pyskoleintra."""


class SkoleintraError(Exception):
    """Base exception for all Skoleintra errors."""


class AuthenticationError(SkoleintraError):
    """Raised when login or session authentication fails."""


class SessionExpiredError(AuthenticationError):
    """Raised when the session cookie has expired and re-login is needed."""


class NotAuthorizedError(SkoleintraError):
    """Raised when an operation is attempted without a valid session."""


class ParseError(SkoleintraError):
    """Raised when an HTML or JSON response cannot be parsed as expected."""


class NetworkError(SkoleintraError):
    """Raised on HTTP transport failures (timeouts, connection errors)."""


class MaintenanceError(SkoleintraError):
    """Raised when Skoleintra is in maintenance/update mode."""
