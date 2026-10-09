from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: AnyHttpUrl
    name: str = Field(min_length=1)
    connect_timeout_seconds: float = Field(default=10, gt=0)
    read_timeout_seconds: float = Field(default=120, gt=0)
    api_key: SecretStr = SecretStr("")


class PppfltSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executable: Path
    template_path: Path
    observation_root: Path
    product_root: Path
    default_concurrency: int = Field(default=1, ge=1, le=8)
    observation_template: str = (
        r"{year}\{doy:03d}\{station_lower}{doy:03d}0.{yy:02d}o"
    )
    navigation_template: str = r"{year}\{doy:03d}\brdm{year}{doy:03d}.rnx"
    orbit_template: str = r"{year}\{doy:03d}\igs{year}{doy:03d}.sp3"
    clock_template: str = r"{year}\{doy:03d}\igs{year}{doy:03d}.clk"
    bias_template: str = r"{year}\{doy:03d}\igs{year}{doy:03d}.bia"
    ifcb_template: str = r"{year}\{doy:03d}\igs{year}{doy:03d}.ifcb"
    upd_template: str = r"{year}\{doy:03d}\igs{year}{doy:03d}.upd"
    antenna_template: str = r"models\igs20.atx"
    tide_template: str = r"models\oceanload.blq"
    erp_template: str = r"models\finals.erp"
    planetary_template: str = r"models\de430.bsp"


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["local"] = "local"
    project_root: Path
    run_root: Path
    database_path: Path
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    max_concurrency: int = Field(default=2, ge=1, le=8)
    model: ModelSettings
    pppflt: PppfltSettings

    @classmethod
    def load(cls, path: Path, environ: Mapping[str, str]) -> AppSettings:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        raw.setdefault("model", {})["api_key"] = environ.get(
            "GREAT_AGENT_MODEL_API_KEY", ""
        )
        settings = cls.model_validate(raw)
        settings.assert_safe_paths()
        return settings

    def assert_safe_paths(self) -> None:
        project_root = self.project_root.resolve()
        run_root = self.run_root.resolve()
        database_path = self.database_path.resolve()

        self._require_descendant(run_root, project_root, "run_root")
        self._require_descendant(database_path, project_root, "database_path")
        if self.mode == "local" and self.host != "127.0.0.1":
            raise ValueError("local mode requires host 127.0.0.1")

        self.project_root = project_root
        self.run_root = run_root
        self.database_path = database_path

    @staticmethod
    def _require_descendant(path: Path, root: Path, field_name: str) -> None:
        if path == root or not path.is_relative_to(root):
            raise ValueError(f"{field_name} must be inside project_root")
