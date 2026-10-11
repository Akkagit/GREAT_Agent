from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from great_agent.adapters.pppflt_preflight import PreflightReport, inspect_files
from great_agent.adapters.pppflt_xml import (
    remove_input,
    require_node,
    set_text,
    write_xml,
)
from great_agent.config import PppfltSettings
from great_agent.domain.dates import YearDoy
from great_agent.domain.plans import ExperimentSpec
from great_agent.domain.rules import ProductKind, required_products
from great_agent.errors import UnsafePathError, XmlValidationError


@dataclass(frozen=True)
class RenderedTask:
    xml_path: Path
    output_dir: Path


class GreatPppfltAdapter:
    def __init__(self, settings: PppfltSettings) -> None:
        self.settings = settings

    def render_task(
        self, spec: ExperimentSpec, date: YearDoy, task_dir: Path
    ) -> RenderedTask:
        resolved_task_dir = task_dir.resolve()
        output_dir = (resolved_task_dir / "results").resolve()
        self._require_inside(output_dir, resolved_task_dir, "output directory")
        output_dir.mkdir(parents=True, exist_ok=True)

        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        try:
            tree = ET.parse(self.settings.template_path, parser=parser)
        except (ET.ParseError, OSError) as error:
            raise XmlValidationError(f"cannot parse template: {error}") from error
        root = tree.getroot()
        paths = self._input_paths(spec, date)
        native_template = root.find("./gen/beg") is not None

        set_text(root, "./gen/rec", " ".join(spec.stations))
        if native_template:
            day = datetime(date.year, 1, 1) + timedelta(days=date.doy - 1)
            set_text(root, "./gen/beg", day.strftime("%Y-%m-%d 00:00:00"))
            set_text(root, "./gen/end", day.strftime("%Y-%m-%d 23:59:30"))
            set_text(
                root,
                "./process/pos_kin",
                "true" if spec.scenario.mode == "kinematic" else "false",
            )
        else:
            set_text(root, "./gen/mode", spec.scenario.mode)
            set_text(root, "./gen/year", str(date.year))
            set_text(root, "./gen/doy", f"{date.doy:03d}")
        set_text(root, "./inputs/rinexo", " ".join(map(str, paths["observation"])))
        input_nodes = {
            "rinexn": ("rinexn",),
            "sp3": ("sp3",),
            "clk": ("clk", "rinexc"),
            "bias": ("bias",),
            "ifcb": ("ifcb",),
            "upd": ("upd",),
            "antenna": ("antenna", "atx"),
            "tide": ("tide", "blq"),
            "erp": ("erp", "eop"),
            "planetary": ("planetary", "de"),
        }
        for kind, aliases in input_nodes.items():
            if kind in paths:
                self._set_first(root, aliases, str(paths[kind]))

        products = required_products(spec.scenario)
        if ProductKind.IFCB not in products:
            remove_input(root, "ifcb")
        if ProductKind.UPD not in products:
            remove_input(root, "upd")
        set_text(
            root,
            "./process/frequency",
            "3" if spec.scenario.frequency == "TF" else "2",
        )
        set_text(
            root,
            "./ambiguity/fix_mode",
            ("SEARCH" if native_template else "YES")
            if spec.scenario.ambiguity == "fixed"
            else "NO",
        )
        require_node(root, "./filter").set(
            "reset_par", str(spec.scenario.reset_hours)
        )
        if native_template:
            for name in ("ppp", "flt"):
                node = require_node(root, f"./outputs/{name}")
                filename = Path(node.text.strip()).name
                if name == "flt":
                    filename = (
                        "$(rec)-PPP_sta_"
                        f"{spec.scenario.frequency}_"
                        f"{spec.scenario.ambiguity.capitalize()}.flt"
                    )
                node.text = f" {output_dir / filename} "
        else:
            set_text(root, "./outputs/root", str(output_dir))

        xml_path = resolved_task_dir / "config.xml"
        write_xml(tree, str(xml_path))
        rendered = RenderedTask(xml_path=xml_path, output_dir=output_dir)
        self._validate_round_trip(rendered, spec, date, products)
        return rendered

    def preflight_task(
        self, spec: ExperimentSpec, date: YearDoy, task_dir: Path
    ) -> PreflightReport:
        del task_dir
        paths = self._input_paths(spec, date)
        required: list[tuple[str, YearDoy, str | None, Path]] = [
            ("executable", date, None, self.settings.executable.resolve()),
            ("template", date, None, self.settings.template_path.resolve()),
        ]
        required.extend(
            ("observation", date, station, path)
            for station, path in zip(spec.stations, paths["observation"], strict=True)
        )
        required.extend(
            (kind, date, None, paths[kind])
            for kind in (
                "rinexn",
                "sp3",
                "clk",
                "bias",
                "antenna",
                "tide",
                "erp",
                "planetary",
            )
        )
        products = required_products(spec.scenario)
        if ProductKind.IFCB in products:
            required.append(("ifcb", date, None, paths["ifcb"]))
        if ProductKind.UPD in products:
            required.append(("upd", date, None, paths["upd"]))
        return inspect_files(required)

    def build_command(self, rendered: RenderedTask) -> tuple[str, ...]:
        return (str(self.settings.executable), "-x", str(rendered.xml_path))

    def _input_paths(
        self, spec: ExperimentSpec, date: YearDoy
    ) -> dict[str, Path | tuple[Path, ...]]:
        values = {"year": date.year, "doy": date.doy, "yy": date.year % 100}
        observations = tuple(
            self._render_under(
                self.settings.observation_root,
                self.settings.observation_template,
                **values,
                station=station,
                station_lower=station.lower(),
            )
            for station in spec.stations
        )
        templates = {
            "rinexn": self.settings.navigation_template,
            "sp3": self.settings.orbit_template,
            "clk": self.settings.clock_template,
            "bias": self.settings.bias_template,
            "ifcb": self.settings.ifcb_template,
            "upd": self.settings.upd_template,
            "antenna": self.settings.antenna_template,
            "tide": self.settings.tide_template,
            "erp": self.settings.erp_template,
            "planetary": self.settings.planetary_template,
        }
        paths: dict[str, Path | tuple[Path, ...]] = {"observation": observations}
        paths.update(
            {
                kind: self._render_under(
                    self.settings.product_root, template, **values
                )
                for kind, template in templates.items()
            }
        )
        return paths

    def _render_under(self, root: Path, template: str, **values: object) -> Path:
        resolved_root = root.resolve()
        rendered = (resolved_root / template.format(**values)).resolve()
        self._require_inside(rendered, resolved_root, "input path")
        return rendered

    @staticmethod
    def _require_inside(path: Path, root: Path, label: str) -> None:
        if path == root or not path.is_relative_to(root):
            raise UnsafePathError(f"{label} must be inside its configured root")

    def _validate_round_trip(
        self,
        rendered: RenderedTask,
        spec: ExperimentSpec,
        date: YearDoy,
        products: frozenset[ProductKind],
    ) -> None:
        try:
            root = ET.parse(rendered.xml_path).getroot()
        except (ET.ParseError, OSError) as error:
            raise XmlValidationError(f"rendered XML cannot be parsed: {error}") from error
        native_template = root.find("./gen/beg") is not None
        day = datetime(date.year, 1, 1) + timedelta(days=date.doy - 1)
        checks = {
            "mode": (
                require_node(root, "./process/pos_kin").text.strip()
                == ("true" if spec.scenario.mode == "kinematic" else "false")
                if native_template
                else require_node(root, "./gen/mode").text.strip()
                == spec.scenario.mode
            ),
            "date": (
                require_node(root, "./gen/beg").text.strip().startswith(
                    day.strftime("%Y-%m-%d")
                )
                if native_template
                else (
                    require_node(root, "./gen/year").text.strip()
                    == str(date.year)
                    and require_node(root, "./gen/doy").text.strip()
                    == f"{date.doy:03d}"
                )
            ),
            "stations": tuple(require_node(root, "./gen/rec").text.split())
            == spec.stations,
            "frequency": require_node(root, "./process/frequency").text.strip()
            == ("3" if spec.scenario.frequency == "TF" else "2"),
            "ambiguity": require_node(root, "./ambiguity/fix_mode").text.strip()
            == (
                ("SEARCH" if native_template else "YES")
                if spec.scenario.ambiguity == "fixed"
                else "NO"
            ),
            "reset": require_node(root, "./filter").get("reset_par")
            == str(spec.scenario.reset_hours),
            "output": (
                Path(require_node(root, "./outputs/flt").text.strip()).parent
                == rendered.output_dir
                if native_template
                else require_node(root, "./outputs/root").text.strip()
                == str(rendered.output_dir)
            ),
            "ifcb": (root.find("./inputs/ifcb") is not None)
            == (ProductKind.IFCB in products),
            "upd": (root.find("./inputs/upd") is not None)
            == (ProductKind.UPD in products),
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise XmlValidationError(
                f"rendered XML does not match frozen plan: {', '.join(failed)}"
            )

    @staticmethod
    def _set_first(root: ET.Element, aliases: tuple[str, ...], value: str) -> None:
        for alias in aliases:
            node = root.find(f"./inputs/{alias}")
            if node is not None:
                node.text = f" {value} "
                return
        raise XmlValidationError(
            f"missing XML node: one of {', '.join(f'./inputs/{name}' for name in aliases)}"
        )
