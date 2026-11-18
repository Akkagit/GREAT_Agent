"""HTTP boundary exposes only validated excerpts and stable, redacted failures."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from test_knowledge_translation import env, service, provider_response  # noqa: F401


@pytest.fixture
def api(env):
    from great_agent.api.knowledge_translation import router, translation_service
    app = FastAPI()
    app.include_router(router)
    s = service(env)
    app.dependency_overrides[translation_service] = lambda: s
    return app, s


def path(env):
    return f'/api/v1/knowledge/conversations/{env.cid}/messages/{env.mid}/citations/S1/translation'


@pytest.mark.asyncio
async def test_translation_http_and_input_allowlist(env, api):
    app, _ = api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        result = await client.post(path(env), json={'target_language':'zh-CN'})
        assert result.status_code == 200, result.text
        assert result.json()['original_text'] == env.body
        assert result.json()['cached'] is False
        assert (await client.post(path(env), json={'target_language':'zh-CN'})).json()['cached'] is True
        for body in ({'target_language':'fr'}, {'target_language':'en','api_key':'browser-secret'},
                     {'target_language':'en','evidence_text':'forged'}):
            assert (await client.post(path(env),json=body)).status_code == 422


@pytest.mark.asyncio
async def test_translation_http_unavailable_clears_derived_ui_with_stable_code(env, api):
    app, _ = api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        env.original.write_bytes(b'corrupt')
        unavailable = await client.post(path(env),json={'target_language':'en'})
        assert unavailable.status_code == 422
        assert unavailable.json()['code'] == 'knowledge_source_unavailable'
        missing = await client.post(path(env).replace('/S1/', '/S9/'),json={'target_language':'en'})
        assert missing.status_code == 404
        assert missing.json()['code'] == 'knowledge_source_unavailable'


@pytest.mark.asyncio
async def test_translation_http_provider_body_is_redacted(env):
    from great_agent.api.knowledge_translation import router, translation_service
    app = FastAPI(); app.include_router(router)
    s = service(env, lambda _: httpx.Response(500, text='server-secret customer-content'))
    app.dependency_overrides[translation_service] = lambda:s
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        result = await client.post(path(env),json={'target_language':'en'})
        assert result.status_code == 502
        assert result.json()['code'] == 'knowledge_translation_failed'
        assert 'server-secret' not in result.text and 'customer-content' not in result.text


def test_build_app_wires_translator_and_lifespan_closes_it(tmp_path):
    from test_knowledge_chat_wiring import settings_for
    from great_agent.cli import build_app
    app = build_app(settings_for(tmp_path))
    translation = app.state.services.knowledge_translation_service
    assert translation is not None
    assert translation.source_validator.repository is app.state.services.knowledge_chat_service.repository
    assert translation.model_settings is app.state.services.knowledge_chat_service.model_settings
    # Avoid starting ingestion background polling in this focused wiring test.
    app.state.services.knowledge_service = None
    async def close():
        async with app.router.lifespan_context(app):
            assert not translation._closed
    asyncio.run(close())
    assert translation._closed
