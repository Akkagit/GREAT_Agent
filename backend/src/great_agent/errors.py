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


class UnsafePathError(GreatAgentError, ValueError):
    """Raised when a configured or generated path escapes its allowed root."""


class XmlValidationError(GreatAgentError, ValueError):
    """Raised when a PPPFLT template or rendered XML is structurally invalid."""


class PlanNotFrozenError(GreatAgentError):
    """Raised when execution material is requested from an editable plan."""


class PreflightError(GreatAgentError):
    """Raised when one or more required inputs fail strict preflight."""

    def __init__(self, message: str, missing: tuple[object, ...]) -> None:
        super().__init__(message)
        self.missing = missing
