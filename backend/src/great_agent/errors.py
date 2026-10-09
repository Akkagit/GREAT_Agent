class GreatAgentError(Exception):
    """Base class for errors with a stable application-level meaning."""


class PlanValidationError(GreatAgentError, ValueError):
    """Raised when a typed plan violates a domain or local policy rule."""
