"""Five-turn real repository/material/tool/AgentScope integration, mocked wire only."""
import json
from uuid import uuid4

import httpx
import pytest

from great_agent.config import ModelSettings
from great_agent.research.engine import ResearchEngine
from great_agent.research.repository import ResearchRepository
from great_agent.research.service import ResearchService
from great_agent.research.worker import ResearchWorker
from test_research_engine import completion, tool_call
from test_research_materials import material_context, add_artifact, add_literature, experiment_selection


@pytest.mark.parametrize('mode', ['quick', 'deep'])
async def test_five_turns_cross_sources_reload_evidence_and_retain_counterexample(material_context, mode):
    ctx = material_context
    ctx.settings.model = ModelSettings(base_url='https://fixture.invalid/v1', name='fixture', api_key='fixture-only')
    ctx.settings.research.model = None
    (ctx.root / 'solver.cpp').write_text('bool reset_window(double gap) { return gap > 30; }\n', encoding='utf-8')
    add_artifact(ctx, 'analysis.json', json.dumps({'reference_method': 'df_float_final_epoch',
        'reference_note': 'Not external truth', 'policy': {'hold_seconds': 600},
        'stations': [{'station': 'JL18', 'joint_convergence_minutes': 12}]}))
    _, document_id, version_id = add_literature(ctx)
    service = ResearchService(ResearchRepository(ctx.sessions), ctx.materials, ctx.settings)
    session = service.create_session(goal='TF 收敛变慢的原因', mode=mode, materials=[experiment_selection(),
        {'kind': 'code', 'source_id': 'project', 'paths': ['solver.cpp']},
        {'kind': 'literature', 'document_id': document_id, 'version_id': version_id}])
    worker = ResearchWorker(service)
    materials = {row['kind']: row['id'] for row in session['material_summary']}
    snapshot = ctx.materials.capture([experiment_selection()])
    analysis_id = snapshot['materials'][0]['artifacts'][0]['id']
    hypothesis_id = None
    prior_ids = []
    requests = []
    turn_number = 0

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if not any(message['role'] == 'tool' for message in payload['messages']):
            if turn_number == 0:
                calls = [tool_call('read_experiment_summary', {'material_id': materials['experiment']}, 'experiment'),
                         tool_call('read_artifact_excerpt', {'material_id': analysis_id}, 'analysis'),
                         tool_call('read_code_excerpt', {'material_id': materials['code']}, 'code'),
                         tool_call('search_literature', {'query': 'Historical reset', 'limit': 1}, 'literature')]
            else:
                assert hypothesis_id in str(payload['messages'])
                calls = [tool_call('load_prior_evidence', {'evidence_id': evidence_id}, 'reload-' + str(index))
                         for index, evidence_id in enumerate(prior_ids)]
            return httpx.Response(200, json=completion(calls=calls))
        text = str(payload['messages'])
        assert 'reset_window' in text and 'Historical reset equation' in text
        assert 'rev-old' in text and 'df_float_final_epoch' in text
        return httpx.Response(200, json=completion(json.dumps({
            'answer': '实验[E1]、数值产物[E2]、静态代码[C1]与文献前提[L1]需共同核查；目前属于待验证解释。',
            'source_ids': ['E1', 'E2', 'C1', 'L1'], 'answer_status': 'partial',
            'hypotheses': [{'id': hypothesis_id, 'text': '缺测与重置策略可能影响连续收敛窗口',
                'status': 'challenged' if turn_number >= 2 else 'proposed',
                'support': ['E1', 'E2', 'C1', 'L1'] if turn_number < 2 else ['E1', 'E2', 'C1'],
                'opposition': ['L1'] if turn_number == 2 else [],
                'unknowns': ['历史二进制与所选代码的构建关系未知。']}],
            'limitations': ['静态分析未运行代码；DF 末历元不是外部真值。']}, ensure_ascii=False)))

    service.engine = ResearchEngine(service.model_settings, transport=httpx.MockTransport(respond))
    previous_run = None
    for turn_number, question in enumerate(['为什么收敛慢？', '只看 JL18', '这个解释与文献是否矛盾？', '提出修改建议', '总结仍需验证的点']):
        session = service.get_session(session['id'])
        target = {'run_id': previous_run, 'item_id': hypothesis_id} if previous_run else None
        run = service.submit(session['id'], request_id=str(uuid4()), expected_version=session['version'],
                             message=question, followup_target=target)
        await worker.run_once()
        completed = service.repository.get_run(run['id'])
        assert completed['status'] == 'completed', completed
        session = service.get_session(session['id'])
        assert len(session['items']) == 1
        hypothesis_id = session['items'][0]['id']
        prior_ids = [row['id'] for row in service.repository.list_evidence(run['id'])]
        previous_run = run['id']
    assert len(requests) == 10
    assert len(session['messages']) == 10
    assert session['items'][0]['opposition']  # retained after later rounds omit it
    assert len(list(ctx.runs.iterdir())) == 1  # only the fixture's original artifact
    assert (ctx.root / 'solver.cpp').read_text().startswith('bool reset_window')
