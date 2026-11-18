"""Create reproducible position-analysis artifacts without starting a solver."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import threading
from pathlib import Path

from matplotlib import rc_context

from great_agent.domain.position_analysis import analyze_station, read_flt
from great_agent.runtime.result_inspector import ResultFileInfo
from great_agent.runtime.position_plots import (
    PLOT_STYLE, REFERENCE_SCRIPTS, plot_enu, plot_convergence, plot_fix_rate,
)


_PLOT_LOCK = threading.Lock()


def generate_analysis(
    files: tuple[ResultFileInfo, ...],
    output_dir: Path,
    *,
    reference_files: tuple[ResultFileInfo, ...] | None = None,
    hold_seconds: float = 600,
    interval_seconds: float = 30,
    solution_label: str = "DF_Float",
) -> tuple[ResultFileInfo, ...]:
    targets = [item for item in files if item.kind == "flt"]
    references = {
        item.station: item for item in (reference_files if reference_files is not None else targets)
        if item.kind == "flt"
    }
    if not targets:
        raise ValueError("no FLT files to analyze")
    stations = []
    for item in targets:
        if item.station is None or re.fullmatch(r"[A-Z0-9_]{1,16}", item.station) is None:
            raise ValueError("invalid station code for analysis")
        baseline = references.get(item.station)
        if baseline is None:
            raise ValueError(f"missing DF-float reference for {item.station}")
        result = analyze_station(
            read_flt(item.path), reference=read_flt(baseline.path)[-1],
            hold_seconds=hold_seconds, interval_seconds=interval_seconds,
        )
        result.update({
            "station": item.station,
            "source": str(item.path.resolve()),
            "source_sha256": hashlib.sha256(item.path.read_bytes()).hexdigest(),
            "reference_source": str(baseline.path.resolve()),
            "reference_sha256": hashlib.sha256(baseline.path.read_bytes()).hexdigest(),
            "self_reference": item.path.resolve() == baseline.path.resolve(),
            "hold_seconds": hold_seconds,
            "solution": solution_label,
        })
        stations.append(result)

    # Parse every input before creating outputs, so malformed files never produce
    # a partial report that looks like an accepted scientific result.
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = []

    def register(path: Path, kind: str, station: str | None = None) -> None:
        artifacts.append(ResultFileInfo(station, kind, path, path.stat().st_size))

    epoch_rows = {}
    for result in stations:
        path = output_dir / f"{result['station']}-enu.csv"
        rows = result.pop("epochs")
        epoch_rows[result["station"]] = rows
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        register(path, "csv", result["station"])

    report = {
        "schema_version": "1.1",
        "reference_method": "df_float_final_epoch",
        "reference_note": "Relative to a provisional DF-float endpoint, not independent ground truth.",
        "solution": solution_label,
        "plot_profile": {
            "name": "daily_summary_minimal3",
            "reference_scripts": REFERENCE_SCRIPTS,
            "plot_files": ["enu.png", "convergence.png", "fix_rate.png"],
            "components": ["East", "North", "Up"],
            "enu_series": "all stations on three component axes",
            "overview_xlim_hours": [0, 24],
            "overview_ylim_m": [-0.3, 0.3],
            "zoom": None,
            "dpi": 300,
            "cdf": False,
        },
        "statistics": {
            "rms_std_scope": "all observed epochs in the day",
            "std_ddof": 0,
            "fix_rate_denominator": "Fixed plus Float AmbStatus, excluding unknown states",
        },
        "policy": {
            "horizontal_threshold_m": 0.2, "vertical_threshold_m": 0.4,
            "hold_seconds": hold_seconds, "interval_seconds": interval_seconds,
            "convergence_origin": "scheduled GPS day start",
            "convergence_rule": "first continuous window satisfying threshold for hold_seconds",
            "gaps_reset_window": True,
        },
        "stations": stations,
    }
    path = output_dir / "analysis.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    register(path, "analysis")
    path = output_dir / "summary.csv"
    fields = [
        "station", "epoch_count", "valid_epoch_fraction",
        "horizontal_convergence_minutes", "vertical_convergence_minutes",
        "joint_convergence_minutes", "joint_within_fraction", "reference_sow",
        "Solution", "RMS_E", "RMS_N", "RMS_U", "RMS_3D",
        "STD_E", "STD_N", "STD_U", "FixRate(%)", "Conv(min)",
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for station in stations:
            writer.writerow({
                **station, "Solution": solution_label,
                **{f"RMS_{short}": station["rms_all_epochs_m"][key]
                   for short, key in (("E", "east_m"), ("N", "north_m"), ("U", "up_m"))},
                **{f"STD_{short}": station["std_all_epochs_m"][key]
                   for short, key in (("E", "east_m"), ("N", "north_m"), ("U", "up_m"))},
                "RMS_3D": station["rms_3d_all_epochs_m"],
                "FixRate(%)": station["fix_rate"] * 100 if station["fix_rate"] is not None else None,
                "Conv(min)": station["joint_convergence_minutes"],
            })
    register(path, "csv")
    with _PLOT_LOCK, rc_context(PLOT_STYLE):
        path = output_dir / "enu.png"
        plot_enu(path, stations, epoch_rows, solution_label)
        register(path, "plot")
        path = output_dir / "convergence.png"
        plot_convergence(path, stations, hold_seconds, solution_label)
        register(path, "plot")
        path = output_dir / "fix_rate.png"
        plot_fix_rate(path, stations, solution_label)
        register(path, "plot")
    return tuple(artifacts)
