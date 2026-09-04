from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class for errors that map to a 4xx HTTP response."""

    status_code = 400
    code = "domain_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"


class ForbiddenError(DomainError):
    status_code = 403
    code = "forbidden"


class UnauthorizedError(DomainError):
    status_code = 401
    code = "unauthorized"


class InvalidTransitionError(ConflictError):
    code = "invalid_transition"


class ValidationError(DomainError):
    status_code = 422
    code = "validation_error"
