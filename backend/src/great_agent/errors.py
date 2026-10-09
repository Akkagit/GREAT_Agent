class GreatAgentError(Exception):
    """Base class for errors with a stable application-level meaning."""


class PlanValidationError(GreatAgentError, ValueError):
    """Raised when a typed plan violates a domain or local policy rule."""


class ExperimentNotFoundError(GreatAgentError, LookupError):
    """Raised when an experiment identifier is unknown."""


class RevisionConflictError(GreatAgentError):
    """Raised when a revision write is based on stale state."""


class PlanFrozenError(GreatAgentError):
    """Raised when attempting to mutate a frozen experiment plan."""
