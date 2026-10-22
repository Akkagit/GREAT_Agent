from pathlib import Path

import pytest


def test_ecef_to_enu_uses_reference_frame() -> None:
    from great_agent.domain.position_analysis import ecef_to_enu

    # At the equator/Greenwich: E=+Y, N=+Z, U=+X.
    assert ecef_to_enu((6378138.0, 2.0, 3.0), (6378137.0, 0.0, 0.0)) == pytest.approx(
        (2.0, 3.0, 1.0)
    )
    reference = (4517590.878848932, 0.0, 4487348.408865919)
    assert ecef_to_enu(
        (reference[0] + 2 ** -0.5, 0.0, reference[2] + 2 ** -0.5), reference
    ) == pytest.approx((0.0, 0.0, 1.0), abs=1e-8)


def test_convergence_requires_elapsed_hold_and_restarts_after_gaps() -> None:
    from great_agent.domain.position_analysis import convergence_start

    assert convergence_start([0, 30, 60, 90], [False, True, True, True], 60, 30) == 30
    assert convergence_start([0, 30, 120, 150], [True] * 4, 60, 30) is None
    assert convergence_start([0, 30], [True, True], 60, 30) is None
    assert convergence_start([0, 30, 60, 90], [True, False, True, True], 30, 30) == 60


@pytest.mark.parametrize("line", ["solution", "30 nan 0 0", "30 1 2", "30 0 0 inf"])
def test_flt_reader_rejects_invalid_rows(tmp_path: Path, line: str) -> None:
    from great_agent.domain.position_analysis import read_flt

    path = tmp_path / "bad.flt"
    path.write_text(line, encoding="utf-8")
    with pytest.raises(ValueError, match="bad.flt"):
        read_flt(path)


def test_flt_reader_rejects_duplicate_epochs(tmp_path: Path) -> None:
    from great_agent.domain.position_analysis import read_flt

    path = tmp_path / "duplicate.flt"
    path.write_text("# header\n30 6378137 0 0\n30 6378137 0 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="increasing"):
        read_flt(path)


def test_final_df_float_reference_and_joint_horizontal_vertical_metrics(tmp_path: Path) -> None:
    from great_agent.domain.position_analysis import analyze_station, read_flt

    path = tmp_path / "AHB0-PPP_sta_DF_Float.flt"
    path.write_text(
        "# seconds XYZ\n"
        "30 6378138 1 1\n"
        "60 6378137.1 0.15 0.15\n"  # Individual E/N pass, horizontal fails.
        "90 6378137.5 0.1 0\n"      # Horizontal passes, vertical fails.
        "120 6378137.1 0.1 0\n"
        "150 6378137.1 0.1 0\n"
        "180 6378137 0 0\n",
        encoding="utf-8",
    )
    result = analyze_station(read_flt(path), hold_seconds=60)
    assert result["reference_xyz_m"] == [6378137.0, 0.0, 0.0]
    assert result["reference_sow"] == 180
    assert result["horizontal_convergence_minutes"] == 1.5
    assert result["vertical_convergence_minutes"] == 2.0
    assert result["joint_convergence_minutes"] == 2.0
    assert result["epochs"][-1]["horizontal_m"] == 0
    assert result["epochs"][-1]["up_m"] == 0


def test_external_df_float_reference_is_shared_without_recentering_target(tmp_path: Path) -> None:
    from great_agent.domain.position_analysis import analyze_station, read_flt

    baseline = tmp_path / "baseline.flt"
    target = tmp_path / "target.flt"
    baseline.write_text("30 6378137 0 0\n60 6378137 0 0\n", encoding="utf-8")
    target.write_text("30 6378138 0 0\n60 6378138 0 0\n", encoding="utf-8")
    result = analyze_station(read_flt(target), reference=read_flt(baseline)[-1], hold_seconds=30)
    assert result["reference_xyz_m"] == [6378137.0, 0.0, 0.0]
    assert result["joint_convergence_minutes"] is None
    assert result["epochs"][-1]["up_m"] == 1.0
