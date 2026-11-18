from __future__ import annotations

from pydantic import ValidationError

from great_agent.capabilities.profiles import ProfileRegistry
from great_agent.domain.dates import expand_dates
from great_agent.domain.plans import ExperimentSpec, validate_against_policy
from great_agent.errors import PlanValidationError


class PlanValidationService:
    """Validate a complete candidate independently of transient input files."""

    def __init__(
        self, *, max_concurrency: int = 8, profiles: ProfileRegistry | None = None
    ) -> None:
        if not 1 <= max_concurrency <= 8:
            raise ValueError("max_concurrency must be between 1 and 8")
        self.max_concurrency = max_concurrency
        self.profiles = profiles if profiles is not None else ProfileRegistry()

    def validate(self, spec: ExperimentSpec) -> ExperimentSpec:
        # Dump/rebuild also validates objects made with model_copy/model_construct;
        # model_validate(spec) alone trusts existing Pydantic instances.
        try:
            validated = ExperimentSpec.model_validate(spec.model_dump())
        except ValidationError as error:
            raise PlanValidationError(str(error)) from error
        if not validated.stations:
            raise PlanValidationError("station selection is empty")
        if not validated.outputs:
            raise PlanValidationError("output selection is empty")
        expand_dates(validated)
        validate_against_policy(validated, self.max_concurrency)
        self.profiles.validate(validated)
        return validated
