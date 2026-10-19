"""Errors the caller can act on. Each carries a public code; bootstrap maps the class to an
HTTP status, so no route keeps its own list of codes."""


class DomainError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class NotFound(DomainError):
    """The thing does not exist, or belongs to another workspace (indistinguishable)."""

    def __init__(self) -> None:
        super().__init__("NOT_FOUND")


class Conflict(DomainError):
    """The current state refuses the request; the code says which state."""


class LimitReached(DomainError):
    """A cap that protects shared quota is used up for now."""


class Unavailable(DomainError):
    """A dependency is down or not configured; trying again later may work."""


class OutcomeUnknown(Unavailable):
    """An external call may or may not have taken effect; it is reconciled later."""
