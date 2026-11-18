import csv
import json
from pathlib import Path

import pytest

from great_agent.runtime.result_inspector import ResultFileInfo


def test_analysis_writes_plots_and_reference_provenance(tmp_path: Path) -> None:
    from great_agent.runtime.result_analysis import generate_analysis

    source = tmp_path / "AHB0-PPP_sta_DF_Float.flt"
    source.write_text(
        "# seconds XYZ\n30 6378138 1 1\n60 6378137.1 0.1 0\n90 6378137 0 0\n",
        encoding="utf-8",
    )
    item = ResultFileInfo("AHB0", "flt", source, source.stat().st_size)
    output = tmp_path / "analysis"
    generated = generate_analysis((item,), output, hold_seconds=30)
    report = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
    assert report["reference_method"] == "df_float_final_epoch"
    assert report["policy"]["horizontal_threshold_m"] == 0.2
    assert report["policy"]["vertical_threshold_m"] == 0.4
    station = report["stations"][0]
    assert station["joint_convergence_minutes"] == 1.0
    assert station["reference_xyz_m"] == [6378137.0, 0.0, 0.0]
    assert station["reference_source"] == str(source.resolve())
    assert len(station["reference_sha256"]) == 64
    plots = [item for item in generated if item.kind == "plot"]
    assert {item.path.name for item in plots} == {"enu.png", "convergence.png", "fix_rate.png"}
    assert all(item.station is None for item in plots)
    assert {item.path.relative_to(output).as_posix() for item in generated} == {
        "enu.png", "convergence.png", "fix_rate.png", "AHB0-enu.csv", "summary.csv", "analysis.json",
    }
    for item in plots:
        assert item.path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert item.path.stat().st_size > 1000
    assert len((output / "AHB0-enu.csv").read_text().splitlines()) == 4
    assert not (output / "AHB0-enu.png").exists()
    assert not (output / "cdf").exists()
    assert not (output / "cdf.csv").exists()
    assert "STD_E" in (output / "summary.csv").read_text()
    assert "FixRate(%)" in (output / "summary.csv").read_text()
    assert report["plot_profile"]["reference_scripts"] == [
        "plot/pppflt_new/ppp_enu_plot.py"
    ]
    assert set(report["plot_profile"]["plot_files"]) == {item.path.name for item in plots}
    assert report["plot_profile"]["zoom"] is None
    assert report["plot_profile"]["cdf"] is False


def test_multistation_plots_show_full_day_enu_native_fix_rates_and_missing_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from matplotlib.figure import Figure

    from great_agent.runtime.result_analysis import generate_analysis

    # At this equatorial reference, ENU = (Y, Z, X - 6378137).
    samples = {
        "AHB0": [
            (30, 6378138, 1, 2), (60, 6378137.2, 0.1, 0.1),
            (90, 6378137.1, 0.1, 0.1), (82800, 6378137, 0, 0),
        ],
        "BB01": [
            (30, 6378136.5, -0.5, -1), (60, 6378136.9, -0.05, -0.05),
            (90, 6378136.95, -0.05, -0.05), (82800, 6378137, 0, 0),
        ],
        "CC02": [
            (30, 6378137.25, 0.25, 0.5), (60, 6378137.05, 0.025, 0.025),
            (90, 6378137.025, 0.025, 0.025), (82800, 6378137, 0, 0),
        ],
    }
    sources = []
    for station, statuses in (
        ("AHB0", ("Fixed", "Float", "Unknown", "Unknown")),
        ("BB01", ("Unknown",) * 4),
        ("CC02", ("Float",) * 4),
    ):
        path = tmp_path / f"{station}.flt"
        path.write_text("".join(
            " ".join(map(str, (*sample, *([0] * 12), status, "Fixed"))) + "\n"
            for sample, status in zip(samples[station], statuses, strict=True)
        ), encoding="utf-8")
        sources.append(ResultFileInfo(station, "flt", path, path.stat().st_size))

    # Inspect the artists that are actually saved, while still rendering real PNGs.
    saved = {}
    savefig = Figure.savefig

    def capture(figure: Figure, path: Path, **kwargs) -> None:
        saved[Path(path).name] = [{
            "lines": {
                line.get_label(): (list(line.get_xdata()), list(line.get_ydata()))
                for line in axis.lines
            },
            "xlim": axis.get_xlim(),
            "bars": [(bar.get_x() + bar.get_width() / 2, bar.get_height()) for bar in axis.patches],
            "texts": [(text.get_position(), text.get_text()) for text in axis.texts],
            "ticks": [tick.get_text() for tick in axis.get_xticklabels()],
        } for axis in figure.axes]
        savefig(figure, path, **kwargs)

    monkeypatch.setattr(Figure, "savefig", capture)
    output = tmp_path / "analysis"
    generated = generate_analysis(tuple(sources), output, hold_seconds=30)
    assert set(saved) == {"enu.png", "convergence.png", "fix_rate.png"}
    assert len([item for item in generated if item.kind == "plot"]) == 3
    assert {path.name for path in output.rglob("*.png")} == set(saved)
    assert len(saved["enu.png"]) == 3  # E/N/U only, without a first-hour panel.
    expected_components = [
        {"AHB0": [1, 0.1, 0.1, 0], "BB01": [-0.5, -0.05, -0.05, 0], "CC02": [0.25, 0.025, 0.025, 0]},
        {"AHB0": [2, 0.1, 0.1, 0], "BB01": [-1, -0.05, -0.05, 0], "CC02": [0.5, 0.025, 0.025, 0]},
        {"AHB0": [1, 0.2, 0.1, 0], "BB01": [-0.5, -0.1, -0.05, 0], "CC02": [0.25, 0.05, 0.025, 0]},
    ]
    for axis, expected in zip(saved["enu.png"], expected_components, strict=True):
        assert set(axis["lines"]) == {"AHB0", "BB01", "CC02"}
        for station, (times, errors) in axis["lines"].items():
            assert times == pytest.approx([1 / 120, 1 / 60, 1 / 40, 23])
            assert errors == pytest.approx(expected[station])
        assert axis["xlim"][0] <= 1 / 120 and axis["xlim"][1] >= 23

    convergence = saved["convergence.png"][0]
    assert convergence["ticks"] == ["AHB0", "BB01", "CC02"]
    assert [height for _, height in convergence["bars"]] == [1, 1, 1]
    fix_rate = saved["fix_rate.png"][0]
    assert fix_rate["ticks"] == ["AHB0", "BB01", "CC02"]
    assert fix_rate["bars"] == [(0, 50), (2, 0)]  # Unknown has no zero-rate bar.
    assert any(position[0] == 1 and label == "N/A" for position, label in fix_rate["texts"])

    report = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
    assert [station["fix_rate"] for station in report["stations"]] == [0.5, None, 0.0]
    assert report["stations"][0]["ambiguity_counts"] == {"fixed": 1, "float": 1, "unknown": 2}
    with (output / "summary.csv").open(newline="", encoding="utf-8") as stream:
        summary = list(csv.DictReader(stream))
    assert [row["FixRate(%)"] for row in summary] == ["50.0", "", "0.0"]
    assert {path.name for path in output.glob("*-enu.csv")} == {"AHB0-enu.csv", "BB01-enu.csv", "CC02-enu.csv"}


def test_all_sources_are_validated_before_writing_analysis(tmp_path: Path) -> None:
    from great_agent.runtime.result_analysis import generate_analysis

    source = tmp_path / "bad.flt"
    source.write_text("solution\n", encoding="utf-8")
    output = tmp_path / "analysis"
    with pytest.raises(ValueError):
        generate_analysis((ResultFileInfo("AHB0", "flt", source, 9),), output)
    assert not output.exists()


def test_unconverged_station_preserves_missing_summary_values(tmp_path: Path) -> None:
    from great_agent.runtime.result_analysis import generate_analysis

    source = tmp_path / "AHB0.flt"
    source.write_text("30 6378137 1 0\n60 6378137 0 0\n", encoding="utf-8")
    output = tmp_path / "analysis"
    generate_analysis((ResultFileInfo("AHB0", "flt", source, source.stat().st_size),), output)
    report = json.loads((output / "analysis.json").read_text())
    station = report["stations"][0]
    assert station["joint_convergence_minutes"] is None
    assert all(value is None for value in station["rms_after_joint_convergence_m"].values())
    with (output / "summary.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["Conv(min)"] == ""
    assert rows[0]["FixRate(%)"] == ""
    assert not (output / "cdf.csv").exists()
