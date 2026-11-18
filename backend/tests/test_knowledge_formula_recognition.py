"""Formula transcription stays scoped, derived, bounded and independently rendered."""
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from great_agent.config import ModelSettings
from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base
from great_agent.db.knowledge_repository import KnowledgeRepository
from great_agent.db.knowledge_chat_repository import KnowledgeChatRepository, KnowledgeChatValidationError
from great_agent.knowledge.storage import KnowledgeStorage
from test_knowledge_repository import publish, parsed


def test_formula_module_exists():
    assert importlib.util.find_spec('great_agent.knowledge.formula_recognition') is not None


@pytest.mark.parametrize('name', ['local_pdf_text_v1', 'local_review'])
def test_remote_provider_cannot_use_a_local_cache_identity(name):
    from great_agent.knowledge.formula_recognition import FormulaRecognitionService
    with pytest.raises(ValueError, match='reserved'):
        FormulaRecognitionService(None, None, ModelSettings(name=name,
            base_url='https://example.invalid/v1'), allow_remote=True)


def test_numbered_regions_distinguish_column_edge_from_cross_reference():
    from great_agent.knowledge.formula_recognition import numbered_formula_regions, is_formula_question
    words = [dict(x0=x, x1=x+20, top=y, bottom=y+10, text='body')
             for x in (35, 75, 120, 170, 220, 265, 310, 350, 400, 450, 500, 525)
             for y in (100, 150, 200)]
    tags = [dict(text='(10)', x0=277, x1=289, top=220, bottom=230),
            dict(text='(11)', x0=527, x1=544, top=340, bottom=350),
            dict(text='(3)', x0=322, x1=340, top=400, bottom=410)]
    page = SimpleNamespace(width=595, height=842, bbox=(0, 0, 595, 842),
                           extract_words=lambda: words, search=lambda pattern: tags)
    result = numbered_formula_regions(page)
    assert [r['equation_number'] for r in result] == ['10', '11']
    assert result[0]['bbox'][0] < 100 and result[0]['bbox'][2] <= 300
    assert all(r['bbox'][3]-r['bbox'][1] <= 150 for r in result)
    assert all(r['coordinate_space'] == 'rotated_media_top_left' for r in result)
    assert is_formula_question('解释公式（10）') and is_formula_question('Equation (11) in LaTeX')
    assert not is_formula_question('summarize the innovation')


@pytest.fixture
def env(tmp_path, request):
    engine, sessions = create_engine_and_session_factory(tmp_path / 'formula.sqlite')
    Base.metadata.create_all(engine)
    storage = KnowledgeStorage(tmp_path / 'knowledge')
    pdf = Path(__file__).parent / 'fixtures/knowledge-coordinates/rotate-0.pdf'
    bbox = [20, 15, 100, 60]
    if getattr(request, 'param', None) == 'digital':
        from test_knowledge_formula_text import digital_pdf, line
        pdf = digital_pdf(tmp_path/'digital.pdf', [line('y = (2x+3)/5')], number='10')
        bbox = [300, 127, 600, 220]
    blob = storage.store_bytes(pdf.read_bytes(), 'formula.pdf')
    knowledge = KnowledgeRepository(sessions)
    base = knowledge.create_base('papers')
    payload = parsed('The model equation (10) has a physical source.')
    payload['metadata']['equation_regions'] = [dict(equation_number='10', page_index=0,
        bbox=bbox, coordinate_space='rotated_media_top_left')]
    document = publish(knowledge, knowledge.register_upload(base['id'], **blob,
        filename='formula.pdf', parser_name='baseline'), payload=payload)
    chat = KnowledgeChatRepository(sessions)
    conversation = chat.create_conversation(base_id=base['id'], document_ids=[document['id']])
    _, _, snapshot = chat.claim_turn(conversation['id'], 'formula (10)?', 'request', 90)
    items = chat.list_snapshot_chunks(snapshot)
    yield SimpleNamespace(chat=chat, knowledge=knowledge, storage=storage, document=document,
                          snapshot=snapshot, items=items, blob=blob, sessions=sessions)
    engine.dispose()


def service(env, handler):
    from great_agent.knowledge.formula_recognition import FormulaRecognitionService
    return FormulaRecognitionService(env.chat, env.storage, ModelSettings(name='vision-test',
        base_url='https://example.invalid/v1', api_key='server-secret'), transport=httpx.MockTransport(handler), allow_remote=True)


def response(content=None):
    return httpx.Response(200, json={'choices':[{'finish_reason':'stop', 'message':{'role':'assistant',
        'content':content or json.dumps({'formulas':[{'equation_number':'10', 'latex':r'x_i = \\frac{a}{b}'}]})}}]})


async def test_scoped_transcription_renders_crop_and_reuses_persistent_cache(env):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return response()
    s = service(env, handler)
    run = s.start_run(env.snapshot)
    result = await run.enrich('Explain equation (10)', env.items)
    formula = result[env.items[0]['id']][0]
    assert formula['equation_number'] == '10' and formula['status'] == 'machine_transcription'
    assert formula['source_sha256'] == env.blob['sha256'] and len(formula['image_sha256']) == 64
    assert formula['page_index'] == 0 and formula['bbox'] == [20, 15, 100, 60]
    assert run.requests == 1 and len(calls) == 1
    payload = calls[0]
    assert 'tools' not in payload and payload['max_tokens'] == 2048
    assert payload['thinking'] == {'type':'disabled'}
    encoded = payload['messages'][1]['content'][1]['image_url']['url']
    assert encoded.startswith('data:image/png;base64,') and len(encoded) < 2_800_000
    assert len(list((env.storage.root/'derived/formulas').glob('*.json'))) == 1
    second = s.start_run(env.snapshot)
    assert await second.enrich('公式(10)', env.items) == result
    assert second.requests == 0 and len(calls) == 1
    assert await s.start_run(env.snapshot).enrich('公式(999)', env.items) == {}
    assert env.chat.read_snapshot_chunk(env.snapshot, env.items[0]['id'])['text'] == env.items[0]['text']
    await s.close()


async def test_cache_cannot_bypass_disabled_source_or_changed_original(env):
    s = service(env, lambda request: response())
    await s.start_run(env.snapshot).enrich('formula (10)', env.items)
    path = env.storage.original_path(env.blob['original_relpath'])
    path.write_bytes(b'%PDF-changed')
    with pytest.raises(KnowledgeChatValidationError):
        await s.start_run(env.snapshot).enrich('formula (10)', env.items)
    await s.close()


async def test_provider_output_rejected_when_unknown_number_duplicate_json_or_tools(env):
    from great_agent.knowledge.formula_recognition import FormulaRecognitionError
    for body in ('{"formulas":[],"formulas":[]}',
                 '{"formulas":[{"equation_number":"999","latex":"x=1"}]}',
                 '{"formulas":[{"equation_number":"10","latex":"x=1","other":1}]}'):
        s = service(env, lambda request: response(body))
        with pytest.raises(FormulaRecognitionError):
            await s.start_run(env.snapshot).enrich('equation (10)', env.items)
        assert not list((env.storage.root/'derived/formulas').glob('*.json'))
        await s.close()


async def test_scope_revoked_during_provider_never_returns_or_caches(env):
    def handler(request):
        env.knowledge.set_enabled(env.document['id'], False)
        return response()
    s = service(env, handler)
    with pytest.raises(KnowledgeChatValidationError):
        await s.start_run(env.snapshot).enrich('formula (10)', env.items)
    assert not list((env.storage.root/'derived/formulas').glob('*.json'))
    await s.close()


async def test_concurrent_singleflight_and_cancelled_waiter(env):
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []
    async def handler(request):
        calls.append(1)
        started.set()
        await finish.wait()
        return response()
    s = service(env, handler)
    first, second = s.start_run(env.snapshot), s.start_run(env.snapshot)
    a = asyncio.create_task(first.enrich('formula (10)', env.items))
    await started.wait()
    b = asyncio.create_task(second.enrich('formula (10)', env.items))
    await asyncio.sleep(.05)
    a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a
    finish.set()
    assert await b
    assert len(calls) == 1 and first.requests + second.requests == 1
    await s.close()


def test_renderer_rejects_nonfinite_large_and_unqualified_geometry(tmp_path):
    from great_agent.knowledge.formula_render_process import render_formula_crop
    fixtures = Path(__file__).parent / 'fixtures/knowledge-coordinates'
    for name, bbox in [('rotate-90.pdf', [1, 1, 100, 40]),
                       ('rotate-0.pdf', [0, 0, 100, float('nan')]),
                       ('rotate-0.pdf', [0, 0, 500, 800])]:
        with pytest.raises(ValueError):
            render_formula_crop(fixtures/name, 0, bbox, tmp_path/'bad.png')


async def test_remote_default_off_and_local_reviewed_import_is_scoped(env):
    from great_agent.knowledge.formula_recognition import FormulaRecognitionService
    def forbidden(request):
        pytest.fail('Default service must not upload formula images')
    s = FormulaRecognitionService(env.chat, env.storage, ModelSettings(name='text-model',
        base_url='https://example.invalid/v1', api_key='server-secret'), transport=httpx.MockTransport(forbidden))
    assert await s.start_run(env.snapshot).enrich('formula (10)', env.items) == {}
    source = env.snapshot['document_versions'][0]
    rendered = await s.render_region(env.snapshot, source['version_id'], '10')
    assert rendered['image_png'].startswith(b'\x89PNG')
    record = {k:v for k,v in rendered.items() if k != 'image_png'}
    record.update(version_id=source['version_id'], equation_number='10', status='locally_reviewed', latex=r'x_i=a/b')
    await s.import_reviewed_transcriptions(env.snapshot, [record])
    run = s.start_run(env.snapshot)
    result = await run.enrich('公式(10)', env.items)
    formula = result[env.items[0]['id']][0]
    assert formula['status'] == 'locally_reviewed' and formula['latex'] == r'x_i=a/b'
    assert formula['model'] == 'local_review' and run.requests == 0
    env.knowledge.set_enabled(env.document['id'], False)
    with pytest.raises(KnowledgeChatValidationError):
        await s.start_run(env.snapshot).enrich('formula (10)', env.items)
    await s.close()


async def test_reviewed_import_rejects_wrong_crop_hash_and_unknown_region(env):
    from great_agent.knowledge.formula_recognition import FormulaRecognitionService
    s = FormulaRecognitionService(env.chat, env.storage, ModelSettings(name='text-model', base_url='https://example.invalid/v1'))
    record = dict(version_id=env.snapshot['document_versions'][0]['version_id'], equation_number='10',
        source_sha256=env.blob['sha256'], page_index=0, bbox=[20,15,100,60], image_sha256='a'*64,
        status='locally_reviewed', latex='a=b')
    with pytest.raises(KnowledgeChatValidationError):
        await s.import_reviewed_transcriptions(env.snapshot, [record])
    record['equation_number'] = '999'
    with pytest.raises(KnowledgeChatValidationError):
        await s.import_reviewed_transcriptions(env.snapshot, [record])
    await s.close()


async def test_provider_failure_is_safe_and_cancelled_only_waiter_leaves_no_cache(env):
    from great_agent.knowledge.formula_recognition import FormulaRecognitionError
    def broken(request):
        raise httpx.ReadTimeout('provider private detail server-secret')
    s = service(env, broken)
    with pytest.raises(FormulaRecognitionError) as caught:
        await s.start_run(env.snapshot).enrich('formula (10)', env.items)
    assert 'server-secret' not in str(caught.value)
    await s.close()
    started = asyncio.Event()
    async def delayed(request):
        started.set()
        await asyncio.sleep(20)
        return response()
    s = service(env, delayed)
    task = asyncio.create_task(s.start_run(env.snapshot).enrich('formula (10)', env.items))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not list((env.storage.root/'derived/formulas').glob('*.json'))
    assert not list((env.storage.root/'staging').glob('formula-*'))
    await s.close()


async def test_refusal_tool_calls_truncation_and_wrong_role_are_rejected(env):
    from great_agent.knowledge.formula_recognition import FormulaRecognitionError
    valid = {'role':'assistant','content':'{"formulas":[{"equation_number":"10","latex":"x=1"}]}'}
    for reason, change in [('length',{}), ('stop',{'tool_calls':[{'function':{'name':'unsafe'}}]}),
                           ('stop',{'refusal':'refused'}), ('stop',{'role':'tool'})]:
        def handler(request):
            return httpx.Response(200, json={'choices':[{'finish_reason':reason,'message':{**valid, **change}}]})
        s = service(env, handler)
        with pytest.raises(FormulaRecognitionError):
            await s.start_run(env.snapshot).enrich('formula (10)', env.items)
        await s.close()


async def test_same_run_concurrent_enrichment_cannot_start_two_provider_requests(env):
    s = service(env, lambda request: response())
    run = s.start_run(env.snapshot)
    # Force both callers to finish the read-only candidate lookup together.
    import threading
    barrier = threading.Barrier(2)
    original = s._candidates
    def concurrent_candidates(*args):
        found = original(*args)
        barrier.wait(timeout=5)
        return found
    s._candidates = concurrent_candidates
    a, b = await asyncio.gather(run.enrich('formula (10)', env.items), run.enrich('formula (10)', env.items))
    assert sum(bool(result) for result in (a, b)) == 1
    assert run.requests == 1
    await s.close()

@pytest.mark.parametrize('env', ['digital'], indirect=True)
async def test_local_text_cache_survives_model_change_review_wins_and_original_is_checked(env, monkeypatch):
    from great_agent.knowledge.formula_recognition import FormulaRecognitionService

    def helper(name):
        return FormulaRecognitionService(env.chat, env.storage, ModelSettings(name=name,
            base_url='https://example.invalid/v1'), transport=httpx.MockTransport(lambda _: pytest.fail('HTTP')))
    first = helper('first')
    row = env.items[0]
    result = await first.start_run(env.snapshot).enrich('公式(10)', env.items)
    value = result[row['id']][0]
    assert (value['model'], value['status'], value['latex']) == (
        'local_pdf_text_v1', 'machine_transcription', 'y=(2x+3)/5')
    second = helper('second')
    source = env.snapshot['document_versions'][0]
    evidence = dict(document_id=source['document_id'], version_id=source['version_id'],
                    source_sha256=source['sha256'], locations=row['locations'])
    async def no_extraction(*args, **kwargs):
        pytest.fail('A warm cache must not re-open an extraction subprocess')
    with monkeypatch.context() as patch:
        patch.setattr(second, '_process_strip', no_extraction)
        assert await second.start_run(env.snapshot).enrich('formula (10)', env.items) == result
    with env.sessions() as session:
        assert second.validate_transcriptions(env.snapshot, evidence, [value], session=session) == [value]
        with pytest.raises(KnowledgeChatValidationError):
            second.validate_transcriptions(env.snapshot, evidence, [{**value, 'bbox':[1,2,3,4]}], session=session)
    rendered = await second.render_region(env.snapshot, source['version_id'], '10')
    assert rendered['image_sha256'] == value['image_sha256']
    record = {k:v for k,v in value.items() if k != 'model'}
    record.update(version_id=source['version_id'], latex='z=7', status='locally_reviewed')
    await second.import_reviewed_transcriptions(env.snapshot, [record])
    assert (await second.start_run(env.snapshot).enrich('formula (10)', env.items))[row['id']][0]['latex'] == 'z=7'
    env.knowledge.set_enabled(env.document['id'], False)
    with env.sessions() as session, pytest.raises(KnowledgeChatValidationError):
        second.validate_transcriptions(env.snapshot, evidence, [value], session=session)
    env.knowledge.set_enabled(env.document['id'], True)
    env.storage.original_path(env.blob['original_relpath']).write_bytes(b'%PDF-mutated')
    with env.sessions() as session, pytest.raises(KnowledgeChatValidationError):
        second.validate_transcriptions(env.snapshot, evidence, [value], session=session)
    await first.close()
    await second.close()


async def test_formula_child_pid_is_interpreter_and_cancellation_cleans_staging(env, monkeypatch, tmp_path):
    import os
    import sys
    import psutil

    helper = service(env, lambda _: pytest.fail('HTTP'))
    marker = tmp_path/'child-pid.txt'
    spawn = asyncio.create_subprocess_exec
    child = None

    async def launch_probe(program, *args, **kwargs):
        nonlocal child
        # Exercise the actual interpreter and environment chosen by _process_strip.
        # Replace the module only, to keep the parser alive for cancellation.
        script = ('import os,sys,time,pdfplumber; from pathlib import Path; '
                  'Path(sys.argv[1]).write_text(str(os.getpid())+"|"+sys.prefix); time.sleep(30)')
        child = await spawn(program, '-c', script, str(marker), **kwargs)
        return child

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', launch_probe)
    source = env.storage.original_path(env.blob['original_relpath'])
    task = asyncio.create_task(helper._process_strip(source, {'page_index':0,'bbox':[0,0,20,20],
                                                             'equation_number':'7'}, extract=True))
    interpreter = None
    try:
        async with asyncio.timeout(8):
            while not marker.exists() or not marker.stat().st_size:
                await asyncio.sleep(.03)
        pid, prefix = marker.read_text().split('|')
        interpreter = psutil.Process(int(pid))
        assert os.path.normcase(prefix) == os.path.normcase(sys.prefix)
        assert interpreter.pid == child.pid
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        if interpreter and interpreter.is_running():
            # Clean up the pre-fix orphan even when the identity assertion fails.
            interpreter.kill()
            await asyncio.to_thread(interpreter.wait, 5)
        await helper.close()
    assert not list((env.storage.root/'staging').glob('formula-*'))
