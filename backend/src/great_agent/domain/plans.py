from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from great_agent.domain.dates import DateSegment, YearDoy
from great_agent.errors import PlanValidationError


ProfileId = Annotated[
    str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
]


class Scenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["static", "kinematic"]
    frequency: Literal["DF", "TF"]
    ambiguity: Literal["float", "fixed"]
    reset_hours: Literal[0, 4, 8] = 0


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    workflow: Literal["pppflt"] = "pppflt"
    scenario: Scenario
    date_segments: tuple[DateSegment, ...]
    excluded_dates: tuple[YearDoy, ...] = ()
    station_group_id: ProfileId
    stations: tuple[str, ...]
    input_profile_id: ProfileId = "local_default"
    solver_profile_id: ProfileId = "great_local"
    template_profile_id: ProfileId = "pppflt_base"
    concurrency: int = Field(default=1, ge=1, le=8)
    outputs: frozenset[Literal["flt", "log"]] = frozenset({"flt", "log"})

    @field_validator("stations", mode="before")
    @classmethod
    def normalize_stations(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, (str, bytes)):
            raise ValueError("stations must be a sequence of station codes")
        try:
            stations = tuple(str(station).strip().upper() for station in value)  # type: ignore[union-attr]
        except TypeError as error:
            raise ValueError("stations must be a sequence of station codes") from error
        if any(not station for station in stations):
            raise ValueError("station codes must not be empty")
        return stations

    @model_validator(mode="after")
    def validate_station_snapshot(self) -> ExperimentSpec:
        if len(set(self.stations)) != len(self.stations):
            raise ValueError("station codes must be unique")
        if self.station_group_id == "default_15" and len(self.stations) != 15:
            raise ValueError("default_15 requires exactly 15 stations")
        return self


def validate_against_policy(spec: ExperimentSpec, max_concurrency: int) -> None:
    if spec.concurrency > max_concurrency:
        raise PlanValidationError(
            f"concurrency {spec.concurrency} exceeds maximum {max_concurrency}"
        )
