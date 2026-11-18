"""Local formula evidence survives citations without entering model messages."""
import hashlib
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from great_agent.config import ModelSettings
from great_agent.db.knowledge_chat_models import KnowledgeRetrievalTraceModel
from great_agent.db.knowledge_chat_repository import KnowledgeChatValidationError
from great_agent.knowledge.formula_recognition import FormulaRecognitionService
from great_agent.services.knowledge_chat_service import KnowledgeChatService
from test_knowledge_formula_recognition import env  # noqa: F401
from test_knowledge_chat_service import Retriever
from test_knowledge_chat_engine import completion, call


LATEX = r'x_{\mathrm{LOCALREVIEW}}=\frac{a}{b}'


@pytest.fixture
async def integrated(env):
    model = ModelSettings(name='glm-fixture', base_url='https://example.invalid/v1', api_key='test')
    def forbidden(_):
        pytest.fail('Formula images must never be uploaded by chat integration')
    helper = FormulaRecognitionService(env.chat, env.storage, model,
        transport=httpx.MockTransport(forbidden), allow_remote=False)
    version = env.snapshot['document_versions'][0]['version_id']
    rendered = await helper.render_region(env.snapshot, version, '10')
    await helper.import_reviewed_transcriptions(env.snapshot, [{
        **{k:v for k,v in rendered.items() if k != 'image_png'}, 'version_id':version,
        'equation_number':'10', 'latex':LATEX, 'status':'locally_reviewed'}])
    cid = env.snapshot['conversation_id']
    env.chat.fail_turn(env.chat.get_conversation(cid)['active_run_id'], 'interrupted', 'interrupted')
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert LATEX not in json.dumps(payload, ensure_ascii=False)
        if len(requests) == 1:
            return httpx.Response(200, json=completion(calls=[call('search_knowledge', {'query':'equation (10)'})]))
        tool_content = next(m['content'] for m in payload['messages'] if m['role'] == 'tool')
        public = json.loads(tool_content)['items'][0]
        assert public['formula_transcriptions'] == [{'equation_number':'10','page_index':0,'status':'locally_reviewed'}]
        return httpx.Response(200, json=completion(json.dumps({'answer':'See the locally verified source formula [S1].',
            'source_ids':['S1'], 'answerable':True})))
    from great_agent.runtime.knowledge_chat_engine import KnowledgeChatEngine
    engine = KnowledgeChatEngine(model, transport=httpx.MockTransport(handler))
    service = KnowledgeChatService(env.chat, Retriever(env.chat), engine, model, formula_service=helper)
    yield service, helper, requests, cid
    await service.close()


async def test_formula_is_local_citation_data_and_restores_from_authoritative_trace(env, integrated):
    service, helper, requests, cid = integrated
    request_id = str(uuid4())
    response = await service.send_message(cid, 'Explain formula (10)', request_id)
    citation = response['assistant_message']['blocks'][1]
    formula = citation['formula_transcriptions'][0]
    assert formula['latex'] == LATEX and formula['status'] == 'locally_reviewed'
    assert citation['evidence_text'] == env.items[0]['text']
    assert citation['evidence_text_sha256'] == hashlib.sha256(env.items[0]['text'].encode()).hexdigest()
    assert len(requests) == 2 and helper.allow_remote is False
    restored = env.chat.get_citation(cid, response['assistant_message']['id'], 'S1')
    assert restored['formula_transcriptions'] == citation['formula_transcriptions']
    detail = env.chat.get_conversation(cid)
    assert detail['messages'][-1]['blocks'][1]['formula_transcriptions'] == citation['formula_transcriptions']
    with env.sessions() as session:
        trace = session.get(KnowledgeRetrievalTraceModel, response['run_id'])
        assert json.loads(trace.evidence_json)[0]['formula_transcriptions'][0]['latex'] == LATEX
        assert LATEX not in json.dumps(json.loads(trace.outbound_messages_json), ensure_ascii=False)
    replay = await service.send_message(cid, 'Explain formula (10)', request_id)
    assert replay == response
    assert replay['assistant_message']['blocks'][1]['formula_transcriptions'][0]['latex'] == LATEX
    assert len(requests) == 2


async def test_revocation_and_original_corruption_hide_local_formula_on_get_and_replay(env, integrated):
    service, _, _, cid = integrated
    request_id = str(uuid4())
    response = await service.send_message(cid, 'Explain formula (10)', request_id)
    env.storage.original_path(env.blob['original_relpath']).write_bytes(b'%PDF-corrupt')
    citation = env.chat.get_citation(cid, response['assistant_message']['id'], 'S1')
    assert citation['available'] is False and not citation.get('formula_transcriptions')
    detail = env.chat.get_conversation(cid)
    assert not detail['messages'][-1]['blocks'][1].get('formula_transcriptions')
    replay = await service.send_message(cid, 'Explain formula (10)', request_id)
    assert not replay['assistant_message']['blocks'][1].get('formula_transcriptions')
    env.knowledge.set_enabled(env.document['id'], False)
    citation = env.chat.get_citation(cid, response['assistant_message']['id'], 'S1')
    assert not citation['available'] and not citation.get('formula_transcriptions')


async def test_trace_formula_cannot_be_forged_or_attached_to_another_region(env, integrated):
    service, _, _, cid = integrated
    response = await service.send_message(cid, 'Explain formula (10)', str(uuid4()))
    # Direct trace corruption must not promote arbitrary LaTeX into a trusted
    # restored citation. The original immutable source remains unchanged.
    with env.sessions.begin() as session:
        trace = session.get(KnowledgeRetrievalTraceModel, response['run_id'])
        evidence = json.loads(trace.evidence_json)
        evidence[0]['formula_transcriptions'][0]['latex'] = 'forged=999'
        trace.evidence_json = json.dumps(evidence)
    citation = env.chat.get_citation(cid, response['assistant_message']['id'], 'S1')
    assert not citation.get('formula_transcriptions') and not citation['available']


def test_cli_always_wires_local_helper_without_formula_model_setting(tmp_path):
    from great_agent.cli import build_app
    from test_knowledge_chat_wiring import settings_for
    app = build_app(settings_for(tmp_path))
    service = app.state.services.knowledge_chat_service
    assert isinstance(service.formula_service, FormulaRecognitionService)
    assert service.formula_service.allow_remote is False


async def test_corrupted_formula_metadata_is_unavailable_on_citation_restore(env, integrated):
    service, _, _, cid = integrated
    response = await service.send_message(cid, 'Explain formula (10)', str(uuid4()))
    with env.sessions.begin() as session:
        session.execute(text('UPDATE knowledge_versions SET metadata_json=:metadata WHERE id=:version'),
            {'metadata':'[]', 'version':env.snapshot['document_versions'][0]['version_id']})
    citation = env.chat.get_citation(cid, response['assistant_message']['id'], 'S1')
    assert citation['available'] is False and not citation.get('formula_transcriptions')


async def test_explicit_chinese_formula_number_finds_scoped_parent_without_lexical_hit(env, integrated):
    service, _, _, cid = integrated
    async def irrelevant_search(snapshot, query, limit=8):
        return {'mode':'lexical','items':[],'queries':[query],'candidates':[],'timings':{}}
    service.retriever.search = irrelevant_search
    response = await service.send_message(cid, '请解释文献中的公式（10）及其物理意义。', str(uuid4()))
    citation = response['assistant_message']['blocks'][1]
    assert citation['chunk_id'] == env.items[0]['id']
    assert citation['formula_transcriptions'][0]['latex'] == LATEX
    assert citation['evidence_text'] == env.items[0]['text']
    with env.sessions() as session:
        trace = session.get(KnowledgeRetrievalTraceModel, response['run_id'])
        structural = [item for item in json.loads(trace.candidates_json) if item.get('channel') == 'formula_region']
    assert len(structural) == 1 and structural[0]['equation_number'] == '10'
    assert structural[0]['chunk_id'] == env.items[0]['id']


async def test_structural_formula_lookup_does_not_guess_duplicate_or_unknown_numbers(env, integrated):
    service, helper, _, _ = integrated
    assert service._formula_region_context(env.snapshot, '请解释公式（999）') == ([], [])
    assert service._formula_region_context(env.snapshot, '请总结文章') == ([], [])
    version = env.snapshot['document_versions'][0]['version_id']
    with env.sessions.begin() as session:
        metadata = {'equation_regions':[
            {'equation_number':'10','page_index':0,'bbox':[20,15,100,60],'coordinate_space':'rotated_media_top_left'},
            {'equation_number':'10','page_index':0,'bbox':[20,50,100,90],'coordinate_space':'rotated_media_top_left'}]}
        session.execute(text('UPDATE knowledge_versions SET metadata_json=:metadata WHERE id=:version'),
            {'metadata':json.dumps(metadata),'version':version})
    assert service._formula_region_context(env.snapshot, '请解释公式（10）') == ([], [])
    # A duplicate elsewhere in the fixed scope remains ambiguous even when the
    # lexical results happen to contain a parent from only the first page.
    metadata['equation_regions'][1]['page_index']=1
    with env.sessions.begin() as session:
        session.execute(text('UPDATE knowledge_versions SET metadata_json=:metadata WHERE id=:version'),
            {'metadata':json.dumps(metadata),'version':version})
    assert await helper.start_run(env.snapshot).enrich('请解释公式（10）',env.items) == {}


def test_explicit_number_counts_all_scoped_pages_before_parent_selection(env):
    model=ModelSettings(name='local-only',base_url='https://example.invalid/v1')
    helper=FormulaRecognitionService(env.chat,env.storage,model,allow_remote=False)
    version=env.snapshot['document_versions'][0]['version_id']
    metadata={'equation_regions':[
        {'equation_number':'10','page_index':0,'bbox':[20,15,100,60],'coordinate_space':'rotated_media_top_left'},
        {'equation_number':'10','page_index':1,'bbox':[20,15,100,60],'coordinate_space':'rotated_media_top_left'}]}
    with env.sessions.begin() as session:
        session.execute(text('UPDATE knowledge_versions SET metadata_json=:metadata WHERE id=:version'),
            {'metadata':json.dumps(metadata),'version':version})
    assert helper._candidates(env.snapshot,'请解释公式（10）',env.items) == []


@pytest.mark.parametrize('explanation_top',[191,230])
def test_formula_context_selects_lower_edge_explanation_instead_of_short_formula_line(env, explanation_top):
    from test_knowledge_repository import parsed, publish
    before='The reconstructed single-difference observation is given below.'
    raw_formula='E { delta L - lambda N + gamma I - m T } = b (10)'
    explanation='The right-hand estimate obtains absolute phase OSB after prior correction of the clock bias.'
    payload=parsed('\n'.join([before,raw_formula,explanation]))
    payload['metadata']['equation_regions']=[{'equation_number':'10','page_index':0,
        'bbox':[297.64,126.84,595.28,219.84],'coordinate_space':'rotated_media_top_left'}]
    template=payload['chunks'][0]
    payload['chunks']=[{**template,'ordinal':ordinal,'text':body,
        'locations':[{'page_index':0,'page_label':'i','bbox':bbox}]}
        for ordinal,body,bbox in [(0,before,[265,83,544,195]),
                                 (1,raw_formula,[297,186,544,198]),
                                 (2,explanation,[245,explanation_top,544,448])]]
    job=env.knowledge.queue_reparse(env.document['id'],'baseline','with-explanation')
    publish(env.knowledge,{'document':env.document,'job':job},payload=payload)
    conversation=env.chat.create_conversation(base_id=env.snapshot['base_id'],document_ids=[env.document['id']])
    _,_,snapshot=env.chat.claim_turn(conversation['id'],'解释公式（10）','explanation',90)
    model=ModelSettings(name='local-only',base_url='https://example.invalid/v1')
    helper=FormulaRecognitionService(env.chat,env.storage,model,allow_remote=False)
    service=KnowledgeChatService(env.chat,Retriever(env.chat),None,model,formula_service=helper)
    parents,candidates=service._formula_region_context(snapshot,'解释公式（10）')
    assert [parent['text'] for parent in parents] == [before,explanation]
    assert len(candidates)==2 and all(item['channel']=='formula_region' for item in candidates)
