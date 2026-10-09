from __future__ import annotations

from sqlalchemy.orm import Session

from great_agent.db.models import ExperimentModel, PlanRevisionModel
from great_agent.errors import ExperimentNotFoundError


class PlanRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def require_experiment(self, experiment_id: str) -> ExperimentModel:
        experiment = self.session.get(ExperimentModel, experiment_id)
        if experiment is None:
            raise ExperimentNotFoundError(f"unknown experiment: {experiment_id}")
        return experiment

    def require_current_revision(
        self, experiment: ExperimentModel
    ) -> PlanRevisionModel:
        if experiment.current_revision_id is None:
            raise RuntimeError(f"experiment {experiment.id} has no current revision")
        revision = self.session.get(
            PlanRevisionModel, experiment.current_revision_id
        )
        if revision is None:
            raise RuntimeError(
                f"experiment {experiment.id} references a missing revision"
            )
        return revision
