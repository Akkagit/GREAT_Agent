from pathlib import Path

import pytest

from great_agent.config import AppSettings

from conftest import write_config


def test_settings_reject_run_root_outside_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = write_config(project, run_root=tmp_path / "outside")

    with pytest.raises(ValueError, match="run_root must be inside project_root"):
        AppSettings.load(config, {})


def test_settings_reject_database_outside_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = write_config(project, database_path=tmp_path / "outside.db")

    with pytest.raises(ValueError, match="database_path must be inside project_root"):
        AppSettings.load(config, {})


def test_local_mode_rejects_non_loopback_host(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = write_config(project, host="0.0.0.0")

    with pytest.raises(ValueError, match="local mode requires host 127.0.0.1"):
        AppSettings.load(config, {})


def test_api_key_only_comes_from_environment(valid_config: Path) -> None:
    settings = AppSettings.load(
        valid_config, {"GREAT_AGENT_MODEL_API_KEY": "secret"}
    )

    assert settings.model.api_key.get_secret_value() == "secret"
    assert "secret" not in settings.model_dump_json()
    assert "must-not-be-used" not in settings.model_dump_json()


def test_missing_environment_api_key_does_not_use_toml(valid_config: Path) -> None:
    settings = AppSettings.load(valid_config, {})

    assert settings.model.api_key.get_secret_value() == ""
