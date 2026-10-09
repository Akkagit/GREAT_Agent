from __future__ import annotations

import calendar
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from great_agent.errors import PlanValidationError

if TYPE_CHECKING:
    from great_agent.domain.plans import ExperimentSpec


class YearDoy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    year: int = Field(ge=1, le=9999)
    doy: int = Field(ge=1, le=366)


class DateSegment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    year: int = Field(ge=1, le=9999)
    start_doy: int = Field(ge=1, le=366)
    end_doy: int = Field(ge=1, le=366)


def expand_dates(spec: ExperimentSpec) -> tuple[YearDoy, ...]:
    expanded: set[YearDoy] = set()
    for segment in spec.date_segments:
        maximum = _maximum_doy(segment.year)
        _require_valid_doy(segment.year, segment.start_doy, maximum)
        _require_valid_doy(segment.year, segment.end_doy, maximum)
        if segment.start_doy > segment.end_doy:
            raise PlanValidationError(f"invalid range for {segment.year}")
        expanded.update(
            YearDoy(year=segment.year, doy=doy)
            for doy in range(segment.start_doy, segment.end_doy + 1)
        )

    for excluded in spec.excluded_dates:
        _require_valid_doy(
            excluded.year, excluded.doy, _maximum_doy(excluded.year)
        )

    remaining = tuple(
        sorted(
            expanded.difference(spec.excluded_dates),
            key=lambda date: (date.year, date.doy),
        )
    )
    if not remaining:
        raise PlanValidationError("date selection is empty")
    return remaining


def _maximum_doy(year: int) -> int:
    return 366 if calendar.isleap(year) else 365


def _require_valid_doy(year: int, doy: int, maximum: int) -> None:
    if doy > maximum:
        raise PlanValidationError(f"{year} DOY {doy} is invalid")
