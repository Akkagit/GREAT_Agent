from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from great_agent.domain.dates import YearDoy


@dataclass(frozen=True)
class PreflightItem:
    kind: str
    date: YearDoy
    station: str | None
    path: Path
    state: Literal["present", "missing", "empty"]


@dataclass(frozen=True)
class PreflightReport:
    items: tuple[PreflightItem, ...]

    @property
    def missing(self) -> tuple[PreflightItem, ...]:
        return tuple(item for item in self.items if item.state != "present")

    @property
    def ok(self) -> bool:
        return not self.missing


def inspect_files(
    required: list[tuple[str, YearDoy, str | None, Path]],
) -> PreflightReport:
    items = []
    for kind, date, station, path in required:
        if not path.is_file():
            state: Literal["present", "missing", "empty"] = "missing"
        elif path.stat().st_size == 0:
            state = "empty"
        else:
            state = "present"
        items.append(
            PreflightItem(
                kind=kind,
                date=date,
                station=station,
                path=path,
                state=state,
            )
        )
    return PreflightReport(items=tuple(items))
