from __future__ import annotations

import pytest
from pydantic import ValidationError

from great_agent.domain.dates import DateSegment, YearDoy, expand_dates
from great_agent.domain.plans import ExperimentSpec, Scenario, validate_against_policy
from great_agent.domain.rules import ProductKind, required_products
from great_agent.errors import PlanValidationError


DEFAULT_STATIONS = tuple(f"S{i:03d}" for i in range(15))


def make_spec(
    *,
    segments: list[DateSegment] | None = None,
    excluded: list[YearDoy] | None = None,
    stations: tuple[str, ...] = DEFAULT_STATIONS,
    concurrency: int = 1,
) -> ExperimentSpec:
    return ExperimentSpec(
        scenario=Scenario(mode="static", frequency="DF", ambiguity="float"),
        date_segments=tuple(
            segments
            or [DateSegment(year=2024, start_doy=350, end_doy=350)]
        ),
        excluded_dates=tuple(excluded or ()),
        station_group_id="default_15",
        stations=stations,
        concurrency=concurrency,
    )


def test_expand_dates_cross_year_overlap_and_exclusion() -> None:
    spec = make_spec(
        segments=[
            DateSegment(year=2024, start_doy=365, end_doy=366),
            DateSegment(year=2025, start_doy=1, end_doy=2),
            DateSegment(year=2025, start_doy=2, end_doy=3),
        ],
        excluded=[YearDoy(year=2025, doy=2)],
    )

    assert expand_dates(spec) == (
        YearDoy(year=2024, doy=365),
        YearDoy(year=2024, doy=366),
        YearDoy(year=2025, doy=1),
        YearDoy(year=2025, doy=3),
    )


def test_non_leap_doy_366_is_rejected() -> None:
    with pytest.raises(PlanValidationError, match="2025 DOY 366"):
        expand_dates(
            make_spec(
                segments=[DateSegment(year=2025, start_doy=366, end_doy=366)]
            )
        )


def test_non_leap_excluded_doy_366_is_rejected() -> None:
    with pytest.raises(PlanValidationError, match="2025 DOY 366"):
        expand_dates(make_spec(excluded=[YearDoy(year=2025, doy=366)]))


def test_empty_final_date_set_is_rejected() -> None:
    selected = YearDoy(year=2024, doy=350)

    with pytest.raises(PlanValidationError, match="date selection is empty"):
        expand_dates(make_spec(excluded=[selected]))


@pytest.mark.parametrize(
    ("frequency", "ambiguity", "expected"),
    [
        ("DF", "float", frozenset()),
        ("DF", "fixed", frozenset({ProductKind.UPD})),
        ("TF", "float", frozenset({ProductKind.IFCB})),
        ("TF", "fixed", frozenset({ProductKind.IFCB, ProductKind.UPD})),
    ],
)
def test_product_rules(
    frequency: str, ambiguity: str, expected: frozenset[ProductKind]
) -> None:
    scenario = Scenario(
        mode="static", frequency=frequency, ambiguity=ambiguity  # type: ignore[arg-type]
    )

    assert required_products(scenario) == expected


def test_invalid_reset_hours_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Scenario(
            mode="static", frequency="DF", ambiguity="float", reset_hours=6
        )


def test_concurrency_above_local_policy_is_rejected() -> None:
    with pytest.raises(PlanValidationError, match="concurrency 2 exceeds maximum 1"):
        validate_against_policy(make_spec(concurrency=2), max_concurrency=1)


def test_unknown_workflow_is_rejected() -> None:
    payload = make_spec().model_dump()
    payload["workflow"] = "arbitrary"

    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(payload)


def test_duplicate_stations_are_rejected_after_normalization() -> None:
    stations = (*DEFAULT_STATIONS[:-1], DEFAULT_STATIONS[0].lower())

    with pytest.raises(ValidationError, match="station codes must be unique"):
        make_spec(stations=stations)


def test_default_station_snapshot_is_normalized_and_immutable() -> None:
    stations = tuple(station.lower() for station in DEFAULT_STATIONS)
    spec = make_spec(stations=stations)

    assert spec.stations == DEFAULT_STATIONS
    with pytest.raises(ValidationError):
        spec.concurrency = 2


@pytest.mark.parametrize("profile_id", ["../outside", "F:\\results", "has space"])
def test_profile_identifiers_reject_filesystem_paths(profile_id: str) -> None:
    payload = make_spec().model_dump()
    payload["input_profile_id"] = profile_id

    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(payload)


def test_unknown_command_field_is_rejected() -> None:
    payload = make_spec().model_dump()
    payload["command"] = "format F:"

    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(payload)
