"""Exception hierarchy for the Anjoy client."""

from __future__ import annotations


class AnjoyError(Exception):
    """Base error for all Anjoy transport/protocol failures."""


class AnjoyAPIError(AnjoyError):
    """A device call returned an error envelope (``<error code=…>`` or
    ``{"error":{"code":…}}``). Carries the numeric ``code`` when present
    (see :data:`anjoy.const.ANJOY_ERRORS`)."""

    def __init__(self, message: str, code: int | None = None,
                 endpoint: str | None = None):
        self.code = code
        self.endpoint = endpoint
        prefix = f"{endpoint}: " if endpoint else ""
        suffix = f" (code {code})" if code is not None else ""
        super().__init__(f"{prefix}{message}{suffix}")


class LoginError(AnjoyAPIError):
    """Authentication failed (bad credentials / rejected DES header)."""
