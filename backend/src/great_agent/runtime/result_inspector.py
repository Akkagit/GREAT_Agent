from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResultFileInfo:
    station: str | None
    kind: str
    path: Path
    size_bytes: int


@dataclass(frozen=True)
class ResultReport:
    ok: bool
    error_code: str | None
    missing_stations: tuple[str, ...]
    files: tuple[ResultFileInfo, ...]


class PppfltResultInspector:
    def inspect(
        self, attempt_dir: Path, *, expected_stations: tuple[str, ...]
    ) -> ResultReport:
        result_dir = attempt_dir / "results"
        flt_files = tuple(
            sorted(
                (
                    path
                    for path in result_dir.iterdir()
                    if path.is_file() and path.suffix.lower() == ".flt"
                ),
                key=lambda path: path.name.lower(),
            )
        ) if result_dir.is_dir() else ()
        def station_for(path: Path) -> str:
            stem = path.stem.upper()
            for station in expected_stations:
                normalized = station.upper()
                if stem == normalized or stem.startswith(f"{normalized}-"):
                    return normalized
            return stem

        by_station = {station_for(path): path for path in flt_files}
        missing = tuple(
            station
            for station in expected_stations
            if station.upper() not in by_station
            or by_station[station.upper()].stat().st_size == 0
        )
        files = [
            ResultFileInfo(
                station=station_for(path),
                kind="flt",
                path=path,
                size_bytes=path.stat().st_size,
            )
            for path in flt_files
        ]
        log_path = attempt_dir / "solver.log"
        if log_path.is_file():
            files.append(
                ResultFileInfo(
                    station=None,
                    kind="log",
                    path=log_path,
                    size_bytes=log_path.stat().st_size,
                )
            )
        return ResultReport(
            ok=not missing,
            error_code="missing_output_error" if missing else None,
            missing_stations=missing,
            files=tuple(files),
        )
