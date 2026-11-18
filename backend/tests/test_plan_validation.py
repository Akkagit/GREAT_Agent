from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from great_agent.adapters.great_pppflt import GreatPppfltAdapter
from great_agent.capabilities.profiles import ProfileRegistry
from great_agent.config import PppfltSettings
from great_agent.domain.dates import DateSegment, YearDoy, expand_dates
from great_agent.domain.plans import ExperimentSpec, Scenario
from great_agent.domain.rules import ProductKind, required_products
from great_agent.errors import PlanValidationError
from great_agent.services.plan_validation_service import PlanValidationService


def make_spec(**updates: object) -> ExperimentSpec:
    spec = ExperimentSpec(
        scenario=Scenario(mode="static", frequency="DF", ambiguity="float"),
        date_segments=(DateSegment(year=2024, start_doy=366, end_doy=366),),
        station_group_id="custom",
        stations=("BJFS",),
    )
    return spec.model_copy(update=updates)


@pytest.mark.parametrize(
    "updates",
    [
        {"date_segments": (DateSegment(year=2025, start_doy=366, end_doy=366),)},
        {"excluded_dates": (YearDoy(year=2025, doy=366),)},
        {"date_segments": (DateSegment(year=2024, start_doy=20, end_doy=10),)},
        {"excluded_dates": (YearDoy(year=2024, doy=366),)},
        {"date_segments": ()},
        {"stations": ()},
        {"outputs": frozenset()},
        {"scenario": Scenario.model_construct(mode="static", frequency="bad", ambiguity="float")},
        {"concurrency": 9},
        {"input_profile_id": "unregistered"},
        {"solver_profile_id": "unregistered"},
        {"template_profile_id": "unregistered"},
    ],
)
def test_validation_rejects_invalid_plan_before_any_execution(updates: dict) -> None:
    with pytest.raises(PlanValidationError):
        PlanValidationService().validate(make_spec(**updates))


def test_validation_enforces_local_concurrency_policy() -> None:
    with pytest.raises(PlanValidationError, match="concurrency 3 exceeds maximum 2"):
        PlanValidationService(max_concurrency=2).validate(make_spec(concurrency=3))


@pytest.mark.parametrize(
    ("frequency", "ambiguity", "products"),
    [
        ("DF", "float", frozenset()),
        ("DF", "fixed", frozenset({ProductKind.UPD})),
        ("TF", "float", frozenset({ProductKind.IFCB})),
        ("TF", "fixed", frozenset({ProductKind.IFCB, ProductKind.UPD})),
    ],
)
def test_valid_plan_preserves_dates_and_product_rules(
    frequency: str, ambiguity: str, products: frozenset[ProductKind]
) -> None:
    spec = make_spec(
        scenario=Scenario(mode="static", frequency=frequency, ambiguity=ambiguity),
        date_segments=(
            DateSegment(year=2024, start_doy=365, end_doy=366),
            DateSegment(year=2025, start_doy=1, end_doy=2),
            DateSegment(year=2025, start_doy=2, end_doy=3),
        ),
        excluded_dates=(YearDoy(year=2025, doy=2),),
    )
    validated = PlanValidationService().validate(spec)
    assert validated == spec
    assert expand_dates(validated) == (
        YearDoy(year=2024, doy=365), YearDoy(year=2024, doy=366),
        YearDoy(year=2025, doy=1), YearDoy(year=2025, doy=3),
    )
    assert required_products(validated.scenario) == products


@pytest.fixture
def settings(tmp_path: Path) -> PppfltSettings:
    template = tmp_path / "template.xml"
    shutil.copyfile(Path(__file__).parent / "fixtures" / "minimal_pppflt.xml", template)
    return PppfltSettings(
        executable=tmp_path / "bin" / "solver.exe",
        template_path=template,
        observation_root=tmp_path / "observations",
        product_root=tmp_path / "products",
        observation_template="{year}/{doy:03d}/{station_lower}.obs",
        navigation_template="{year}/{doy:03d}/nav.rnx",
    )


def test_default_profiles_resolve_real_settings_without_checking_input_files(
    settings: PppfltSettings,
) -> None:
    profiles = ProfileRegistry.from_settings(settings)
    spec = PlanValidationService(profiles=profiles).validate(make_spec())
    resolved = profiles.resolve(spec)
    assert resolved.input.observation_root == settings.observation_root.resolve()
    assert resolved.input.product_root == settings.product_root.resolve()
    assert resolved.input.observation_template == "{year}/{doy:03d}/{station_lower}.obs"
    assert resolved.solver.executable == settings.executable.resolve()
    assert resolved.template.template_path == settings.template_path.resolve()
    assert not settings.observation_root.exists()


def test_catalog_without_runtime_settings_cannot_resolve_executable() -> None:
    profiles = ProfileRegistry()
    profiles.validate(make_spec())
    with pytest.raises(PlanValidationError, match="settings"):
        profiles.resolve(make_spec())


@pytest.mark.parametrize("field", ["input_profile_id", "solver_profile_id", "template_profile_id"])
@pytest.mark.parametrize("boundary", ["render_task", "preflight_task", "_input_paths"])
def test_adapter_rejects_unknown_profiles_before_creating_artifacts(
    settings: PppfltSettings, tmp_path: Path, field: str, boundary: str
) -> None:
    adapter = GreatPppfltAdapter(settings)
    spec = make_spec(**{field: "unregistered"})
    task_dir = tmp_path / "must-not-be-created"
    arguments = (spec, YearDoy(year=2024, doy=366))
    if boundary != "_input_paths":
        arguments += (task_dir,)
    with pytest.raises(PlanValidationError, match=field):
        getattr(adapter, boundary)(*arguments)
    assert not task_dir.exists()


def test_adapter_uses_registered_roots_template_and_executable(
    settings: PppfltSettings, tmp_path: Path
) -> None:
    adapter = GreatPppfltAdapter(settings)
    spec = make_spec()
    date = YearDoy(year=2024, doy=366)
    resolved = adapter.profiles.resolve(spec)
    rendered = adapter.render_task(spec, date, tmp_path / "task")
    root = ET.parse(rendered.xml_path).getroot()
    assert root.findtext("./inputs/rinexo").strip() == str(
        settings.observation_root / "2024" / "366" / "bjfs.obs"
    )
    assert root.findtext("./inputs/rinexn").strip() == str(
        settings.product_root / "2024" / "366" / "nav.rnx"
    )
    items = adapter.preflight_task(spec, date, tmp_path / "task").items
    paths = {item.kind: item.path for item in items}
    assert paths["executable"] == resolved.solver.executable
    assert paths["template"] == resolved.template.template_path
    assert paths["observation"] == Path(root.findtext("./inputs/rinexo").strip())
    assert paths["rinexn"] == Path(root.findtext("./inputs/rinexn").strip())
    assert adapter.build_command(rendered) == (
        str(resolved.solver.executable), "-x", str(rendered.xml_path)
    )
