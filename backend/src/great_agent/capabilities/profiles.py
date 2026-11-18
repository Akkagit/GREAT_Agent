from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from great_agent.config import PppfltSettings
from great_agent.domain.plans import ExperimentSpec
from great_agent.errors import PlanValidationError


@dataclass(frozen=True)
class InputProfile:
    observation_root: Path
    product_root: Path
    observation_template: str
    navigation_template: str
    orbit_template: str
    clock_template: str
    bias_template: str
    ifcb_template: str
    upd_template: str
    antenna_template: str
    tide_template: str
    erp_template: str
    planetary_template: str


@dataclass(frozen=True)
class SolverProfile:
    executable: Path


@dataclass(frozen=True)
class TemplateProfile:
    template_path: Path


@dataclass(frozen=True)
class ResolvedProfiles:
    input: InputProfile
    solver: SolverProfile
    template: TemplateProfile


class ProfileRegistry:
    """The existing PPPFLT profiles, optionally bound to local settings.

    The catalog-only form supports domain services without filesystem settings.
    Execution must use ``from_settings``; catalog entries alone are not runnable.
    Paths are resolved without probing whether input files currently exist.
    """

    def __init__(self) -> None:
        self._resolved: ResolvedProfiles | None = None

    @classmethod
    def from_settings(cls, settings: PppfltSettings) -> ProfileRegistry:
        registry = cls()
        registry._resolved = ResolvedProfiles(
            input=InputProfile(
                observation_root=settings.observation_root.resolve(),
                product_root=settings.product_root.resolve(),
                observation_template=settings.observation_template,
                navigation_template=settings.navigation_template,
                orbit_template=settings.orbit_template,
                clock_template=settings.clock_template,
                bias_template=settings.bias_template,
                ifcb_template=settings.ifcb_template,
                upd_template=settings.upd_template,
                antenna_template=settings.antenna_template,
                tide_template=settings.tide_template,
                erp_template=settings.erp_template,
                planetary_template=settings.planetary_template,
            ),
            solver=SolverProfile(executable=settings.executable.resolve()),
            template=TemplateProfile(template_path=settings.template_path.resolve()),
        )
        return registry

    def validate(self, spec: ExperimentSpec) -> None:
        for field, registered in (
            ("input_profile_id", "local_default"),
            ("solver_profile_id", "great_local"),
            ("template_profile_id", "pppflt_base"),
        ):
            value = getattr(spec, field)
            if value != registered:
                raise PlanValidationError(f"unknown {field}: {value}")

    def resolve(self, spec: ExperimentSpec) -> ResolvedProfiles:
        self.validate(spec)
        return self.resolve_defaults()

    def resolve_defaults(self) -> ResolvedProfiles:
        """Resolve the sole registered execution configuration, including commands."""
        if self._resolved is None:
            raise PlanValidationError("runtime profile settings have not been configured")
        return self._resolved
