from __future__ import annotations

import subprocess
import sys
import textwrap


def configure_logging_in_subprocess(tmp_path, script):
    # dictConfig closes existing handlers and changes logger propagation.
    # Keep pytest's capture handlers and the other API tests isolated from it.
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script), str(tmp_path)],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return result


def test_api_logging_config_persists_chat_events_to_rotating_file(tmp_path) -> None:
    from great_agent.logging_config import api_log_path

    configure_logging_in_subprocess(tmp_path, '''
        import logging
        import logging.config
        import sys
        from pathlib import Path
        from great_agent.logging_config import build_api_logging_config

        logging.config.dictConfig(build_api_logging_config(Path(sys.argv[1])))
        logger = logging.getLogger("uvicorn.error.great_agent.chat")
        logger.info('chat_proposal_stage {"request_id":"safe-id","stage":"model_request"}')
        for handler in logging.getLogger("uvicorn").handlers:
            handler.flush()
    ''')

    log_path = api_log_path(tmp_path)
    assert log_path.is_file()
    text = log_path.read_text(encoding="utf-8")
    assert "chat_proposal_stage" in text
    assert "safe-id" in text


def test_api_logging_configuration_keeps_sdk_bodies_quiet(tmp_path) -> None:
    from great_agent.logging_config import api_log_path

    result = configure_logging_in_subprocess(tmp_path, '''
        import logging
        import logging.config
        import sys
        from pathlib import Path
        from great_agent.logging_config import build_api_logging_config

        sdk_logger = logging.getLogger("as")
        sdk_logger.addHandler(logging.StreamHandler())
        sdk_logger.disabled = True
        logging.config.dictConfig(build_api_logging_config(Path(sys.argv[1])))
        for name in ("as", "openai", "openai._base_client", "openai._response", "openai._legacy_response"):
            logging.getLogger(name).critical("private-provider-body")
        logging.getLogger("uvicorn.error.great_agent.chat").info("chat_proposal_timing safe-metrics")
    ''')
    text = api_log_path(tmp_path).read_text(encoding="utf-8")
    assert "chat_proposal_timing safe-metrics" in text
    assert "private-provider-body" not in text + result.stdout + result.stderr
