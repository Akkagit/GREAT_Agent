from great_agent.config import AppSettings


def test_research_defaults_are_independent_of_short_chat(valid_config):
    settings = AppSettings.load(valid_config, {'GREAT_AGENT_MODEL_API_KEY': 'base-secret'})
    assert settings.research.enabled
    assert settings.research.code_roots == {'project': settings.project_root}
    assert settings.research.model is None
    assert settings.conversation.max_model_requests == 4


def test_explicit_research_model_has_separate_environment_credential(valid_config):
    with valid_config.open('a', encoding='utf-8') as stream:
        stream.write("\n[research.model]\nbase_url='https://research.invalid/v1'\nname='research-model'\nthinking='enabled'\nmax_tokens=8192\napi_key='must-not-use-file-key'\n")
    settings = AppSettings.load(valid_config, {'GREAT_AGENT_MODEL_API_KEY': 'base-secret',
        'GREAT_AGENT_RESEARCH_MODEL_API_KEY': 'research-secret'})
    assert settings.research.model.api_key.get_secret_value() == 'research-secret'
    assert settings.model.api_key.get_secret_value() == 'base-secret'
    assert settings.research.model.thinking == 'enabled'
