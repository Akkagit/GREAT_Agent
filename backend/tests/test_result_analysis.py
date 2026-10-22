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
    assert (output / "AHB0-enu.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (output / "convergence.png").stat().st_size > 1000
    assert len(generated) == 5  # station PNG/CSV, summary PNG/CSV/JSON
    assert len((output / "AHB0-enu.csv").read_text().splitlines()) == 4


def test_all_sources_are_validated_before_writing_analysis(tmp_path: Path) -> None:
    from great_agent.runtime.result_analysis import generate_analysis

    source = tmp_path / "bad.flt"
    source.write_text("solution\n", encoding="utf-8")
    output = tmp_path / "analysis"
    with pytest.raises(ValueError):
        generate_analysis((ResultFileInfo("AHB0", "flt", source, 9),), output)
    assert not output.exists()
