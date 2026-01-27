class NeedToHandleError(Exception):
    """Exception raised when a specific condition needs to be handled."""


class NeedCSRFError(NeedToHandleError):
    """Exception raised when a CSRF token is required."""
