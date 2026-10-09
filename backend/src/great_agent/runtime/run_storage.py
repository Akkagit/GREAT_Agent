from __future__ import annotations

import json
from pathlib import Path
from typing import TypeAlias
from uuid import UUID

from great_agent.domain.dates import YearDoy
from great_agent.errors import UnsafePathError


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class RunStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, *parts: str) -> Path:
        return self._inside_root(*parts)

    def experiment_dir(self, experiment_id: UUID) -> Path:
        return self._inside_root(str(experiment_id))

    def task_dir(self, experiment_id: UUID, date: YearDoy) -> Path:
        return self._inside_root(
            str(experiment_id), "tasks", f"{date.year:04d}_{date.doy:03d}"
        )

    def atomic_json(self, path: Path, value: JsonValue) -> None:
        resolved = path.resolve()
        self._require_inside(resolved)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        temporary = resolved.with_suffix(resolved.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(resolved)

    def _inside_root(self, *parts: str) -> Path:
        candidate = self.root.joinpath(*parts).resolve()
        self._require_inside(candidate)
        return candidate

    def _require_inside(self, path: Path) -> None:
        if path == self.root or not path.is_relative_to(self.root):
            raise UnsafePathError("run path must be inside run_root")
