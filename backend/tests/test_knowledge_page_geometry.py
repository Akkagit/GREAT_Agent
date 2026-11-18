"""Real PDFs: parser geometry preserves Rotate, MediaBox and CropBox meaning."""
import json
from pathlib import Path

import httpx
import pytest

from great_agent.knowledge.parsers import _parse_pdf
from test_knowledge_chat_api import api  # noqa: F401
from test_knowledge_chat_repository import env  # noqa: F401

FIXTURES=Path(__file__).parent/'fixtures/knowledge-coordinates'
CASES=json.loads((FIXTURES/'cases.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case',CASES,ids=lambda c:c['name'])
def test_actual_parser_emits_original_pdf_transform(case):
    parsed=_parse_pdf(FIXTURES/(case['name']+'.pdf'),max_pages=1)
    geometry=parsed.metadata['page_geometry']['0']
    assert geometry['coordinate_space']=='rotated_media_top_left'
    assert geometry['to_pdf_transform']==case['to_pdf_transform']
    assert geometry['rotation']==case['rotation']
    assert parsed.pages[0].width==case['page_width']
    assert parsed.pages[0].height==case['page_height']
    assert list(parsed.elements[0].locations[0].bbox)==pytest.approx(case['bbox'])
    assert parsed.parser.config['page_coordinate_version']=='pdfplumber-media-1'


@pytest.mark.asyncio
async def test_page_api_reads_persisted_geometry_without_parsing_original(env,api):
    from types import SimpleNamespace
    from great_agent.api.dependencies import get_services
    from great_agent.db.knowledge_models import KnowledgeVersionModel
    app,_=api; case=CASES[1]
    geometry={'coordinate_space':'rotated_media_top_left','to_pdf_transform':case['to_pdf_transform'],'rotation':90}
    with env[-1].begin() as session:
        version=session.get(KnowledgeVersionModel,env[3]['active_version_id'])
        metadata=json.loads(version.metadata_json); metadata['page_geometry']={'0':geometry}
        version.metadata_json=json.dumps(metadata)
    app.dependency_overrides[get_services]=lambda:SimpleNamespace(knowledge_service=SimpleNamespace(repository=env[1]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        path=f"/api/v1/knowledge/documents/{env[3]['id']}/versions/{env[3]['active_version_id']}/pages/0"
        result=(await c.get(path)).json()
        assert result['to_pdf_transform']==geometry['to_pdf_transform']
        assert result['coordinate_space']=='rotated_media_top_left'


@pytest.mark.asyncio
async def test_historical_unqualified_geometry_never_claims_exact_boxes(env,api):
    from types import SimpleNamespace
    from great_agent.api.dependencies import get_services
    app,_=api
    app.dependency_overrides[get_services]=lambda:SimpleNamespace(knowledge_service=SimpleNamespace(repository=env[1]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        path=f"/api/v1/knowledge/documents/{env[3]['id']}/versions/{env[3]['active_version_id']}/pages/0"
        result=(await c.get(path)).json()
        assert result['coordinate_space']=='unverified_top_left'
        assert result.get('to_pdf_transform') is None
