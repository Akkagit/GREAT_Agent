"""Create reproducible position-analysis artifacts without starting a solver."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import threading
from pathlib import Path

from great_agent.domain.position_analysis import analyze_station, read_flt
from great_agent.runtime.result_inspector import ResultFileInfo


_PLOT_LOCK = threading.Lock()


def generate_analysis(
    files: tuple[ResultFileInfo, ...],
    output_dir: Path,
    *,
    reference_files: tuple[ResultFileInfo, ...] | None = None,
    hold_seconds: float = 600,
    interval_seconds: float = 30,
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
        })
        stations.append(result)

    # Parse every input before creating outputs, so malformed files never produce
    # a partial report that looks like an accepted scientific result.
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = []

    def register(path: Path, kind: str, station: str | None = None) -> None:
        artifacts.append(ResultFileInfo(station, kind, path, path.stat().st_size))

    for result in stations:
        path = output_dir / f"{result['station']}-enu.csv"
        rows = result.pop("epochs")
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        register(path, "csv", result["station"])
        with _PLOT_LOCK:
            plot_path = output_dir / f"{result['station']}-enu.png"
            _plot_station(plot_path, result, rows)
        register(plot_path, "plot", result["station"])

    report = {
        "schema_version": "1.0",
        "reference_method": "df_float_final_epoch",
        "reference_note": "Relative to a provisional DF-float endpoint, not independent ground truth.",
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
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(stations)
    register(path, "csv")
    with _PLOT_LOCK:
        path = output_dir / "convergence.png"
        _plot_convergence(path, stations, hold_seconds)
    register(path, "plot")
    return tuple(artifacts)


def _plot_station(path: Path, result: dict, rows: list[dict]) -> None:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(14, 8), layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots(3, 2)
    convergence = result["joint_convergence_minutes"]
    for column, subset in enumerate((rows, [row for row in rows if row["elapsed_minutes"] <= 60])):
        scale = 60 if column == 0 else 1
        times = [row["elapsed_minutes"] / scale for row in subset]
        axes[0, column].plot(times, [row["east_m"] for row in subset], label="East", linewidth=0.8)
        axes[0, column].plot(times, [row["north_m"] for row in subset], label="North", linewidth=0.8)
        axes[0, column].set_title("Full day" if column == 0 else "First hour")
        axes[1, column].plot(times, [row["up_m"] for row in subset], label="Up", linewidth=0.8, color="#b45309")
        for limit in (-0.4, 0.4):
            axes[1, column].axhline(limit, color="#7f1d1d", linestyle="--", linewidth=0.8)
        axes[2, column].plot(times, [row["horizontal_m"] for row in subset], label="Horizontal", linewidth=0.8, color="#047857")
        axes[2, column].axhline(0.2, label="0.2 m limit", color="#7f1d1d", linestyle="--", linewidth=0.8)
        axes[2, column].set_xlabel("GPS time since day start (hours)" if column == 0 else "GPS time since day start (min)")
        for row_number, label in enumerate(("East / North (m)", "Up (m); limits +/-0.4", "Horizontal (m)")):
            axis = axes[row_number, column]
            axis.set_ylabel(label)
            axis.set_xlim(0, 24 if column == 0 else 60)
            if convergence is not None:
                axis.axvline(convergence / scale, color="#334155", linestyle=":", linewidth=0.8)
            axis.grid(True, alpha=0.25)
            axis.legend(loc="upper right")
    figure.suptitle(
        f"{result['station']} | provisional DF-float final-epoch reference\n"
        + (f"Joint convergence: {convergence:.1f} min" if convergence is not None else "Joint convergence: not reached")
        + f" | continuous hold: {result['hold_seconds'] / 60:g} min"
    )
    figure.savefig(path, dpi=150)
    figure.clear()


def _plot_convergence(path: Path, stations: list[dict], hold_seconds: float) -> None:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(11, 5), layout="constrained")
    FigureCanvasAgg(figure)
    axis = figure.subplots()
    for index, item in enumerate(stations):
        value = item["joint_convergence_minutes"]
        axis.bar(index, value if value is not None else 0, color="#0f766e")
        axis.text(index, value if value is not None else 0, f"{value:.1f}" if value is not None else "N/C",
                  ha="center", va="bottom", fontsize=8)
    axis.set_xticks(range(len(stations)), [item["station"] for item in stations], rotation=45)
    axis.set_ylabel("Joint convergence from GPS day start (min)")
    axis.set_title(
        f"Horizontal <= 0.2 m and |Up| <= 0.4 m, continuous {hold_seconds / 60:g} min\n"
        "Provisional DF-float final-epoch reference"
    )
    axis.grid(axis="y", alpha=0.25)
    axis.margins(y=0.15)
    figure.savefig(path, dpi=150)
    figure.clear()
