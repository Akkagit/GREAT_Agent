from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from uvicorn.config import LOGGING_CONFIG


def api_log_path(run_root: Path) -> Path:
    return run_root / "_service_logs" / "api.log"


def build_api_logging_config(run_root: Path) -> dict:
    log_path = api_log_path(run_root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    config = deepcopy(LOGGING_CONFIG)
    config["formatters"]["service_file"] = {
        "format": "%(asctime)s %(levelname)s %(name)s [pid=%(process)d] %(message)s",
    }
    config["handlers"]["service_file"] = {
        "class": "logging.handlers.RotatingFileHandler",
        "filename": str(log_path),
        "maxBytes": 5 * 1024 * 1024,
        "backupCount": 5,
        "encoding": "utf-8",
        "formatter": "service_file",
    }
    # dictConfig re-enables existing loggers. Preserve the model boundary's
    # suppression after Uvicorn starts, including AgentScope's stderr handler.
    config["handlers"]["model_sdk_quiet"] = {"class": "logging.NullHandler"}
    for name in ("as", "openai", "openai._base_client", "openai._response", "openai._legacy_response"):
        config["loggers"][name] = {
            "handlers": ["model_sdk_quiet"], "level": "CRITICAL", "propagate": False,
        }
    config["loggers"]["uvicorn"]["handlers"].append("service_file")
    config["loggers"]["uvicorn.access"]["handlers"].append("service_file")
    config["loggers"]["uvicorn.error.great_agent.chat"] = {
        "handlers": ["default", "service_file"],
        "level": "INFO",
        "propagate": False,
    }
    return config
