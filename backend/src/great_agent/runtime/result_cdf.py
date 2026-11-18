"""Summary and post-convergence CDFs corresponding to ppp_cdf_plot.py."""

import csv
from pathlib import Path

from great_agent.domain.position_analysis import empirical_cdf
from great_agent.runtime.position_plots import plot_cdf
from great_agent.runtime.result_inspector import ResultFileInfo


def generate_cdfs(
    output_dir: Path, stations: list[dict], epoch_rows: dict[str, list[dict]], solution_label: str
) -> tuple[ResultFileInfo, ...]:
    metrics = [
        ("convergence", [s["joint_convergence_minutes"] for s in stations], (0, 60), "Convergence Time / min", True),
        ("rms_3D", [s["rms_3d_all_epochs_m"] for s in stations], (0, 0.8), "RMS 3D / m", False),
        ("fix_rate", [s["fix_rate"] * 100 if s["fix_rate"] is not None else None for s in stations],
         (0, 105), "Fixing Rate / % (classified ambiguity states)", False),
    ]
    for short, key in (("E", "east_m"), ("N", "north_m"), ("U", "up_m")):
        metrics.append((f"rms_{short}", [s["rms_all_epochs_m"][key] for s in stations],
                        (0, 0.5), f"RMS {short} / m", False))
        metrics.append((f"std_{short}", [s["std_all_epochs_m"][key] for s in stations],
                        (0, 0.3), f"STD {short} / m", False))
    after = [
        row for station in stations if station["joint_convergence_minutes"] is not None
        for row in epoch_rows[station["station"]]
        if row["elapsed_minutes"] >= station["joint_convergence_minutes"]
    ]
    for short, key, limit in (
        ("E", "east_m", 0.3), ("N", "north_m", 0.3), ("U", "up_m", 0.3),
        ("Horizontal", "horizontal_m", 0.3), ("3D", "three_d_m", 0.5),
    ):
        metrics.append((f"post_convergence_{short}", [abs(row[key]) for row in after],
                        (0, limit), f"Post-convergence |{short}| / m", False))
    cdf_dir = output_dir / "cdf"
    cdf_dir.mkdir(parents=True, exist_ok=True)
    artifacts = []
    csv_path = output_dir / "cdf.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["metric", "solution", "value", "cumulative_percent", "observed_count", "denominator_count"])
        for name, values, limits, xlabel, censored in metrics:
            points = empirical_cdf(values, include_missing=censored)
            observed = sum(value is not None for value in values)
            denominator = len(values) if censored else observed
            writer.writerows([name, solution_label, x, y, observed, denominator] for x, y in points)
            path = cdf_dir / f"{name}.png"
            note = f"{solution_label} | {observed}/{denominator} stations converged" if censored else (
                f"{solution_label} | n={observed} " + ("epochs" if name.startswith("post_") else "station-day samples")
            )
            plot_cdf(path, points, xlim=limits, xlabel=xlabel, solution_label=solution_label, sample_note=note)
            artifacts.append(ResultFileInfo(None, "plot", path, path.stat().st_size))
    artifacts.append(ResultFileInfo(None, "csv", csv_path, csv_path.stat().st_size))
    return tuple(artifacts)
