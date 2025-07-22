class NeedToHandle(Exception):
    """Exception raised when a specific condition needs to be handled."""

    pass


class NeedCSRF(NeedToHandle):
    """Exception raised when a CSRF token is required."""

    pass
