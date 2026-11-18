"""Daily ENU, convergence and ambiguity fixing-rate summary figures.

The ENU overview retains the display conventions of ppp_enu_plot.py, with
all stations sharing three component axes. No external runtime import is
required. CDF rendering remains available for explicit callers.
"""

from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure


REFERENCE_SCRIPTS = ["plot/pppflt_new/ppp_enu_plot.py"]
PLOT_STYLE = {
    "font.family": ["Times New Roman", "DejaVu Serif"],
    "xtick.direction": "in", "ytick.direction": "in",
}
SOLUTION_COLORS = {"DF_Float": "#086CA2", "DF_Fixed": "#2CA02C", "TF_Fixed": "#D62728"}


def plot_enu(
    path: Path, stations: list[dict], epoch_rows: dict[str, list[dict]], solution_label: str,
) -> None:
    figure = Figure(figsize=(16, 10), layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots(3, 1, sharex=True)
    for index, (key, label) in enumerate((
        ("east_m", "East / m"), ("north_m", "North / m"), ("up_m", "Up / m")
    )):
        axis = axes[index]
        for station in stations:
            rows = epoch_rows[station["station"]]
            axis.plot([row["elapsed_minutes"] / 60 for row in rows],
                      [row[key] for row in rows], marker=".", markersize=2,
                      linestyle="None", label=station["station"])
        axis.set_ylabel(label, fontsize=17, weight="bold")
        axis.set_ylim(-0.3, 0.3)
        axis.set_yticks([i / 10 for i in range(-3, 4)])
        axis.set_xlim(0, 24)
        axis.set_xticks(range(0, 25, 2))
        axis.grid(True, linestyle="--", color="gray", alpha=0.4)
        axis.tick_params(axis="both", labelsize=12, direction="in")
    axes[-1].set_xlabel("GPS Hour of Day / h", fontsize=17)
    handles, labels = axes[0].get_legend_handles_labels()
    figure.legend(handles, labels, frameon=False, loc="outside lower center",
                  ncols=min(8, len(stations)), fontsize=9)
    figure.suptitle(
        f"{solution_label} | all stations | full day, display +/-0.3 m\n"
        "Provisional DF-float final-epoch reference",
        fontsize=14,
    )
    figure.savefig(path, dpi=300)
    figure.clear()


def plot_convergence(path: Path, stations: list[dict], hold_seconds: float, solution_label: str) -> None:
    figure = Figure(figsize=(12, 6), layout="constrained")
    FigureCanvasAgg(figure)
    axis = figure.subplots()
    color = SOLUTION_COLORS.get(solution_label, "#086CA2")
    for index, item in enumerate(stations):
        value = item["joint_convergence_minutes"]
        axis.bar(index, value if value is not None else 0, color=color,
                 label=solution_label if index == 0 else None)
        axis.text(index, value if value is not None else 0, f"{value:.1f}" if value is not None else "N/C",
                  ha="center", va="bottom", fontsize=10)
    axis.set_xticks(range(len(stations)), [item["station"] for item in stations], rotation=45, ha="right")
    axis.set_xlabel("Site", fontsize=14)
    axis.set_ylabel("Convergence Time / min", fontsize=14)
    axis.set_title(f"Horizontal <= 0.2 m and |Up| <= 0.4 m, continuous {hold_seconds / 60:g} min\n"
                   "Time from GPS day start; provisional DF-float endpoint reference", fontsize=12)
    axis.legend(frameon=False)
    axis.grid(True, linestyle="--", alpha=0.4)
    axis.margins(y=0.15)
    figure.savefig(path, dpi=300)
    figure.clear()


def plot_fix_rate(path: Path, stations: list[dict], solution_label: str) -> None:
    figure = Figure(figsize=(12, 6), layout="constrained")
    FigureCanvasAgg(figure)
    axis = figure.subplots()
    color = SOLUTION_COLORS.get(solution_label, "#086CA2")
    for index, item in enumerate(stations):
        rate = item["fix_rate"]
        if rate is None:
            axis.text(index, 0, "N/A", ha="center", va="bottom", fontsize=10)
            continue
        percent = rate * 100
        axis.bar(index, percent, color=color)
        axis.text(index, percent, f"{percent:.1f}%", ha="center", va="bottom", fontsize=10)
    axis.set_xticks(range(len(stations)), [item["station"] for item in stations], rotation=45, ha="right")
    axis.set_xlabel("Site", fontsize=14)
    axis.set_ylabel("Fixing Rate / %", fontsize=14)
    axis.set_ylim(0, 110)
    axis.set_yticks(range(0, 101, 20))
    axis.set_title(f"{solution_label} | Fixed / (Fixed + Float) AmbStatus\n"
                   "Unknown states excluded; N/A when no classified epochs", fontsize=12)
    axis.grid(axis="y", linestyle="--", alpha=0.4)
    figure.savefig(path, dpi=300)
    figure.clear()


def plot_cdf(
    path: Path, points: list[tuple[float, float]], *, xlim: tuple[float, float],
    xlabel: str, solution_label: str, sample_note: str,
) -> None:
    figure = Figure(figsize=(6, 4.2), layout="constrained")
    FigureCanvasAgg(figure)
    axis = figure.subplots()
    if points:
        # Restrict display coordinates, not observations or probability mass.
        low_probability = max((y for x, y in points if x <= xlim[0]), default=0)
        high_probability = max((y for x, y in points if x <= xlim[1]), default=0)
        displayed = [(xlim[0], low_probability)] + [
            (x, y) for x, y in points if xlim[0] < x < xlim[1]
        ] + [(xlim[1], high_probability)]
        axis.step([x for x, _ in displayed], [y for _, y in displayed], where="post",
                  color=SOLUTION_COLORS.get(solution_label, "#086CA2"), linewidth=2.2,
                  label=solution_label)
        axis.legend(loc="lower right", frameon=False, fontsize=11)
    else:
        axis.text(0.5, 0.5, "No eligible samples", ha="center", va="center", transform=axis.transAxes)
    axis.set_xlim(*xlim)
    axis.set_ylim(0, 100)
    axis.set_xlabel(xlabel, fontsize=13)
    axis.set_ylabel("Cumulative Probability (%)", fontsize=13)
    axis.set_title(sample_note, fontsize=10)
    axis.tick_params(axis="both", labelsize=11, direction="in")
    axis.grid(True, linestyle="--", linewidth=0.6, alpha=0.45)
    figure.savefig(path, dpi=300)
    figure.clear()
