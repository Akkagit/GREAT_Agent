from __future__ import annotations

import shutil
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from great_agent.adapters.great_pppflt import GreatPppfltAdapter
from great_agent.config import PppfltSettings
from great_agent.domain.dates import DateSegment, YearDoy
from great_agent.domain.plans import ExperimentSpec, Scenario


STATIONS = (
    "AHB0",
    "BJFS",
    "CHAN",
    "DAEJ",
    "GAMG",
    "GMSD",
    "HKSL",
    "JFNG",
    "KUNM",
    "LHAZ",
    "NTUS",
    "SHAO",
    "URUM",
    "WUHN",
    "YELL",
)


def make_spec(
    *,
    mode: str = "static",
    frequency: str = "DF",
    ambiguity: str = "float",
    reset_hours: int = 0,
) -> ExperimentSpec:
    return ExperimentSpec(
        scenario=Scenario(
            mode=mode,  # type: ignore[arg-type]
            frequency=frequency,  # type: ignore[arg-type]
            ambiguity=ambiguity,  # type: ignore[arg-type]
            reset_hours=reset_hours,  # type: ignore[arg-type]
        ),
        date_segments=(DateSegment(year=2024, start_doy=350, end_doy=350),),
        station_group_id="default_15",
        stations=STATIONS,
    )


@pytest.fixture
def adapter(tmp_path: Path) -> GreatPppfltAdapter:
    template = tmp_path / "template.xml"
    shutil.copyfile(
        Path(__file__).parent / "fixtures" / "minimal_pppflt.xml", template
    )
    executable = tmp_path / "bin" / "GREAT_PVT.exe"
    executable.parent.mkdir()
    executable.touch()
    settings = PppfltSettings(
        executable=executable,
        template_path=template,
        observation_root=tmp_path / "observations",
        product_root=tmp_path / "products",
    )
    return GreatPppfltAdapter(settings)


@pytest.fixture
def df_float_spec() -> ExperimentSpec:
    return make_spec()


def test_df_float_xml_is_valid_and_removes_upd_ifcb(
    adapter: GreatPppfltAdapter, df_float_spec: ExperimentSpec, tmp_path: Path
) -> None:
    rendered = adapter.render_task(
        df_float_spec, YearDoy(year=2024, doy=350), tmp_path / "task"
    )
    root = ET.parse(rendered.xml_path).getroot()

    assert root.find("./inputs/upd") is None
    assert root.find("./inputs/ifcb") is None
    assert root.findtext("./process/frequency").strip() == "2"
    assert root.findtext("./ambiguity/fix_mode").strip() == "NO"
    assert len(root.findtext("./gen/rec").split()) == 15
    assert root.find("./filter").attrib["reset_par"] == "0"


def test_rendered_paths_match_target_doy(
    adapter: GreatPppfltAdapter, df_float_spec: ExperimentSpec, tmp_path: Path
) -> None:
    root = ET.parse(
        adapter.render_task(
            df_float_spec, YearDoy(year=2025, doy=6), tmp_path / "task"
        ).xml_path
    ).getroot()

    assert "2025\\006" in root.findtext("./inputs/rinexo")
    assert "2025006" in root.findtext("./inputs/rinexn")
    assert "2024\\350" not in ET.tostring(root, encoding="unicode")


@pytest.mark.parametrize(
    ("frequency", "ambiguity", "has_ifcb", "has_upd"),
    [
        ("DF", "float", False, False),
        ("DF", "fixed", False, True),
        ("TF", "float", True, False),
        ("TF", "fixed", True, True),
    ],
)
def test_product_nodes_follow_scenario(
    adapter: GreatPppfltAdapter,
    tmp_path: Path,
    frequency: str,
    ambiguity: str,
    has_ifcb: bool,
    has_upd: bool,
) -> None:
    root = ET.parse(
        adapter.render_task(
            make_spec(frequency=frequency, ambiguity=ambiguity),
            YearDoy(year=2024, doy=350),
            tmp_path / f"{frequency}-{ambiguity}",
        ).xml_path
    ).getroot()

    assert (root.find("./inputs/ifcb") is not None) is has_ifcb
    assert (root.find("./inputs/upd") is not None) is has_upd
    assert root.findtext("./process/frequency").strip() == (
        "3" if frequency == "TF" else "2"
    )
    assert root.findtext("./ambiguity/fix_mode").strip() == (
        "YES" if ambiguity == "fixed" else "NO"
    )


@pytest.mark.parametrize("reset_hours", [0, 4, 8])
@pytest.mark.parametrize("mode", ["static", "kinematic"])
def test_mode_and_reset_are_rendered(
    adapter: GreatPppfltAdapter,
    tmp_path: Path,
    reset_hours: int,
    mode: str,
) -> None:
    root = ET.parse(
        adapter.render_task(
            make_spec(mode=mode, reset_hours=reset_hours),
            YearDoy(year=2024, doy=350),
            tmp_path / f"{mode}-{reset_hours}",
        ).xml_path
    ).getroot()

    assert root.findtext("./gen/mode").strip() == mode
    assert root.find("./filter").attrib["reset_par"] == str(reset_hours)


def test_output_and_command_are_confined_to_task_directory(
    adapter: GreatPppfltAdapter, df_float_spec: ExperimentSpec, tmp_path: Path
) -> None:
    task_dir = tmp_path / "task"
    rendered = adapter.render_task(
        df_float_spec, YearDoy(year=2024, doy=350), task_dir
    )

    assert rendered.output_dir == (task_dir / "results").resolve()
    assert rendered.output_dir.is_relative_to(task_dir.resolve())
    assert adapter.build_command(rendered) == (
        str(adapter.settings.executable),
        "-x",
        str(rendered.xml_path),
    )


def test_unrelated_template_text_is_unchanged(
    adapter: GreatPppfltAdapter, df_float_spec: ExperimentSpec, tmp_path: Path
) -> None:
    rendered = adapter.render_task(
        df_float_spec, YearDoy(year=2025, doy=6), tmp_path / "task"
    )
    root = ET.parse(rendered.xml_path).getroot()

    assert root.findtext("./meta") == "Reference campaign 2024 used DOY 350."


def test_native_great_template_aliases_render_df_float_safely(
    tmp_path: Path, df_float_spec: ExperimentSpec
) -> None:
    template = tmp_path / "native.xml"
    shutil.copyfile(
        Path(__file__).parent / "fixtures" / "native_pppflt.xml", template
    )
    executable = tmp_path / "GREAT_PVT.exe"
    executable.touch()
    adapter = GreatPppfltAdapter(
        PppfltSettings(
            executable=executable,
            template_path=template,
            observation_root=tmp_path / "observations",
            product_root=tmp_path / "products",
        )
    )

    rendered = adapter.render_task(
        df_float_spec, YearDoy(year=2025, doy=6), tmp_path / "task"
    )
    root = ET.parse(rendered.xml_path).getroot()

    assert root.findtext("./gen/beg").strip() == "2025-01-06 00:00:00"
    assert root.findtext("./gen/end").strip() == "2025-01-06 23:59:30"
    assert root.findtext("./process/pos_kin").strip() == "false"
    assert "2025006" in root.findtext("./inputs/rinexc")
    assert root.findtext("./inputs/atx").strip().endswith("models\\igs20.atx")
    assert root.find("./inputs/ifcb") is None
    assert root.find("./inputs/upd") is None
    assert root.findtext("./ambiguity/fix_mode").strip() == "NO"
    assert Path(root.findtext("./outputs/flt").strip()).parent == rendered.output_dir
    assert "$(rec)" in root.findtext("./outputs/flt")
    assert root.findtext("./outputs/flt").strip().endswith(
        "$(rec)-PPP_sta_DF_Float.flt"
    )


def test_input_replacement_clears_old_paths_after_comments(
    adapter: GreatPppfltAdapter, tmp_path: Path
) -> None:
    template = adapter.settings.template_path
    text = template.read_text(encoding="utf-8")
    root = ET.fromstring(text)
    for name in ("rinexo", "clk"):
        node = root.find(f"./inputs/{name}")
        node.text = "\n"
        comment = ET.SubElement(node, ET.Comment)
        comment.text = " source file description "
        comment.tail = " OLD_CAMPAIGN_PATH OLD_CAMPAIGN_PATH "
    ET.ElementTree(root).write(template, encoding="utf-8")

    rendered = adapter.render_task(
        make_spec(), YearDoy(year=2025, doy=6), tmp_path / "task"
    )
    generated = rendered.xml_path.read_text(encoding="utf-8")
    parsed = ET.fromstring(generated)
    observations = parsed.findtext("./inputs/rinexo").split()
    assert len(observations) == 15
    assert len(set(observations)) == 15
    assert "OLD_CAMPAIGN_PATH" not in generated
    assert generated.count("source file description") == 2
    assert len(parsed.findtext("./inputs/clk").split()) == 1
    lines = [line.strip() for line in generated.splitlines()]
    assert all(lines.count(path) == 1 for path in observations)


def test_filter_attributes_are_written_one_per_line(
    adapter: GreatPppfltAdapter, tmp_path: Path
) -> None:
    tree = ET.parse(adapter.settings.template_path)
    tree.getroot().find("./filter").set("noise_clk", "1000")
    tree.write(adapter.settings.template_path, encoding="utf-8")
    rendered = adapter.render_task(
        make_spec(reset_hours=4), YearDoy(year=2024, doy=350), tmp_path / "task"
    )
    generated = rendered.xml_path.read_text(encoding="utf-8")
    block = re.search(r"<filter\b.*?/>", generated, flags=re.S).group()
    lines = [line.strip() for line in block.splitlines()]
    assert lines == ["<filter", 'reset_par="4"', 'noise_clk="1000"', "/>"]
    assert ET.fromstring(generated).find("./filter").attrib == {
        "reset_par": "4", "noise_clk": "1000"
    }


def test_strict_preflight_reports_exact_missing_station(
    adapter: GreatPppfltAdapter, df_float_spec: ExperimentSpec, tmp_path: Path
) -> None:
    report = adapter.preflight_task(
        df_float_spec, YearDoy(year=2024, doy=350), tmp_path / "task"
    )

    assert report.ok is False
    assert any(
        item.station == "AHB0"
        and item.kind == "observation"
        and item.state == "missing"
        for item in report.missing
    )
