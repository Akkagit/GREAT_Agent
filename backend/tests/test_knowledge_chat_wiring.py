import asyncio
from types import SimpleNamespace

import pytest

from great_agent.app import create_app
from great_agent.config import AppSettings, KnowledgeSettings


def settings_for(tmp_path, **knowledge):
    return AppSettings.model_validate({
        'project_root': tmp_path, 'run_root': tmp_path / 'runs', 'database_path': tmp_path / 'app.db',
        'model': {'base_url': 'https://example.invalid', 'name': 'test'},
        'conversation': {'enabled': False}, 'knowledge': knowledge,
        'pppflt': {'executable': tmp_path / 'solver.exe', 'template_path': tmp_path / 'template.xml',
                  'observation_root': tmp_path / 'obs', 'product_root': tmp_path / 'products'},
    })


def test_chat_enabled_by_default_but_embedding_opt_in():
    settings = KnowledgeSettings().model_dump()
    assert settings.get('chat_enabled') is True
    assert settings.get('embedding_enabled') is False


def test_explicit_local_embedding_paths_resolve_without_downloading(tmp_path):
    settings = settings_for(tmp_path, embedding_enabled=True,
                            embedding_model_path=tmp_path / 'weights' / 'model.onnx',
                            embedding_tokenizer_path=tmp_path / 'weights' / 'tokenizer.json')
    settings.assert_safe_paths()
    assert settings.knowledge.embedding_model_path == (tmp_path / 'weights' / 'model.onnx').resolve()
    assert not (tmp_path / 'weights').exists()


@pytest.mark.parametrize('invalid', [-1, 0])
def test_embedding_memory_guard_requires_positive_mb(invalid):
    with pytest.raises(ValueError):
        KnowledgeSettings(embedding_min_available_mb=invalid)


def test_app_lifespan_closes_single_knowledge_chat_owner(tmp_path):
    class Chat:
        closed = 0
        async def close(self):
            self.closed += 1
    chat = Chat()
    services = SimpleNamespace(knowledge_service=None, knowledge_chat_service=chat)
    app = create_app(settings_for(tmp_path), services=services)
    async def exercise_lifespan():
        async with app.router.lifespan_context(app):
            assert chat.closed == 0
    asyncio.run(exercise_lifespan())
    assert chat.closed == 1


def test_build_app_can_explicitly_disable_chat(tmp_path):
    from great_agent.cli import build_app
    app = build_app(settings_for(tmp_path, chat_enabled=False))
    assert app.state.services.knowledge_chat_service is None
