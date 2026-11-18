"""Browsers reject module workers if Windows registers .mjs as plain text."""
import mimetypes

import httpx
import pytest

from great_agent.app import create_app
from test_knowledge_chat_wiring import settings_for


@pytest.mark.asyncio
async def test_module_worker_mime_overrides_host_registry(tmp_path,monkeypatch):
    mimetypes.init()
    monkeypatch.setitem(mimetypes.types_map,'.mjs','text/plain')
    folder=tmp_path/'frontend/dist/assets'; folder.mkdir(parents=True)
    (folder/'pdf.worker.mjs').write_text('export const worker = true;',encoding='utf-8')
    app=create_app(settings_for(tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.get('/assets/pdf.worker.mjs')
        assert response.status_code==200
        assert 'javascript' in response.headers['content-type']
        assert response.text=='export const worker = true;'
