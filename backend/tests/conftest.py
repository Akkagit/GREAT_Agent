from __future__ import annotations

from pathlib import Path

import pytest

from great_agent.app import create_app
from great_agent.config import AppSettings


def write_config(
    project_root: Path,
    *,
    run_root: Path | None = None,
    database_path: Path | None = None,
    host: str = "127.0.0.1",
    model_api_key: str | None = None,
) -> Path:
    run_root = run_root or project_root / "runs"
    database_path = database_path or project_root / "data" / "great_agent.db"
    api_key_line = (
        f"api_key = '{model_api_key}'\n" if model_api_key is not None else ""
    )
    config_path = project_root / "app.toml"
    config_path.write_text(
        "\n".join(
            (
                "mode = 'local'",
                f"project_root = '{project_root.as_posix()}'",
                f"run_root = '{run_root.as_posix()}'",
                f"database_path = '{database_path.as_posix()}'",
                f"host = '{host}'",
                "port = 8765",
                "max_concurrency = 2",
                "",
                "[model]",
                "base_url = 'https://dashscope.aliyuncs.com/compatible-mode/v1'",
                "name = 'qwen-plus'",
                "connect_timeout_seconds = 10",
                "read_timeout_seconds = 120",
                api_key_line.rstrip("\n"),
                "",
                "[pppflt]",
                f"executable = '{(project_root / 'tools' / 'GREAT_PVT.exe').as_posix()}'",
                f"template_path = '{(project_root / 'templates' / 'pppflt.xml').as_posix()}'",
                f"observation_root = '{(project_root / 'inputs' / 'observations').as_posix()}'",
                f"product_root = '{(project_root / 'inputs' / 'products').as_posix()}'",
                "default_concurrency = 1",
            )
        ),
        encoding="utf-8",
    )
    return config_path


@pytest.fixture
def valid_config(tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    project_root.mkdir()
    return write_config(project_root, model_api_key="must-not-be-used")


@pytest.fixture
def app(valid_config: Path):
    settings = AppSettings.load(
        valid_config, {"GREAT_AGENT_MODEL_API_KEY": "test-secret"}
    )
    return create_app(settings)
