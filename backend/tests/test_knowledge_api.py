"""Upload, durable job and source boundaries exercised through the real API."""
import importlib.util
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


def test_knowledge_service_is_available():
    assert importlib.util.find_spec('great_agent.services.knowledge_service') is not None


@pytest.fixture
def knowledge_app(tmp_path):
    from great_agent.cli import build_app
    from great_agent.config import AppSettings
    settings = AppSettings.model_validate({
        'project_root':str(tmp_path), 'run_root':str(tmp_path/'runs'),
        'database_path':str(tmp_path/'app.db'), 'conversation':{'enabled':False},
        'model':{'name':'test-only', 'base_url':'https://example.invalid'},
        'knowledge':{'poll_interval_seconds':0.02,'parser_timeout_seconds':20},
        'pppflt':{'executable':str(tmp_path/'unused.exe'),'template_path':str(tmp_path/'unused.xml'),
                  'observation_root':str(tmp_path/'obs'),'product_root':str(tmp_path/'products')},
    })
    return build_app(settings)


def wait_job(client, job_id):
    deadline = time.monotonic()+25
    while time.monotonic()<deadline:
        r = client.get(f'/api/v1/knowledge/jobs/{job_id}')
        assert r.status_code == 200, r.text
        if r.json()['status'] not in ('queued','parsing','indexing'):
            return r.json()
        time.sleep(0.03)
    pytest.fail('ingestion did not finish before test deadline')


def test_upload_dedupe_search_disable_and_exact_source(knowledge_app):
    with TestClient(knowledge_app) as client:
        base = client.post('/api/v1/knowledge/bases',json={'name':'GNSS'}).json()
        uri = f"/api/v1/knowledge/bases/{base['id']}"
        source = b'# Receiver clock\n\nSatellite clock corrections are updated every six seconds.'
        r = client.post(uri+'/documents?filename=clock.md', content=source, headers={'content-type':'text/markdown'})
        assert r.status_code == 202, r.text
        registered=r.json(); did=registered['document']['id']
        job=wait_job(client,registered['job']['id'])
        assert job['status']=='completed',job
        duplicate=client.post(uri+'/documents?filename=other-name.md',content=source).json()
        assert duplicate['duplicate'] is True
        assert duplicate['document']['id']==did
        assert len(client.get(uri+'/documents').json()['items'])==1
        hits=client.post(uri+'/search',json={'query':'satellite clock'}).json()
        assert hits['mode']=='lexical'
        assert hits['items'][0]['document_id']==did
        chunk=client.get('/api/v1/knowledge/chunks/'+hits['items'][0]['chunk_id']).json()
        assert 'six seconds' in chunk['text']
        assert chunk['version_id']==hits['items'][0]['version_id']
        assert client.get(f'/api/v1/knowledge/documents/{did}/original').content==source
        detail=client.get(f'/api/v1/knowledge/documents/{did}').json()
        assert detail['latest_job']['status']=='completed'
        assert detail['active_version_id']==chunk['version_id']
        assert client.patch(f'/api/v1/knowledge/documents/{did}',json={'enabled':False}).status_code==200
        assert client.post(uri+'/search',json={'query':'clock'}).json()['items']==[]
        assert client.get(f'/api/v1/knowledge/documents/{did}/original').status_code==404
        assert client.get('/api/v1/knowledge/chunks/'+chunk['id']).status_code==404


def test_failed_reparse_preserves_active_version_and_reports_reason(knowledge_app):
    with TestClient(knowledge_app) as client:
        base=client.post('/api/v1/knowledge/bases',json={'name':'Papers'}).json()
        uri=f"/api/v1/knowledge/bases/{base['id']}"
        r=client.post(uri+'/documents?filename=bad.pdf',content=b'%PDF-not-a-real-file')
        assert r.status_code==202
        job=wait_job(client,r.json()['job']['id'])
        assert job['status']=='failed'
        assert job['error_code']=='parse_failed'
        assert job['error_message']
        assert client.post(uri+'/search',json={'query':'anything'}).json()['items']==[]


@pytest.mark.parametrize('filename,data',[('../escape.md',b'abc'),('bad.exe',b'abc'),('empty.txt',b''),('not.pdf',b'not-pdf')])
def test_invalid_uploads_do_not_register_documents(knowledge_app,filename,data):
    with TestClient(knowledge_app) as client:
        base=client.post('/api/v1/knowledge/bases',json={'name':'Safe'}).json()
        uri=f"/api/v1/knowledge/bases/{base['id']}"
        r=client.post(uri+'/documents',params={'filename':filename},content=data)
        assert r.status_code==422,r.text
        assert client.get(uri+'/documents').json()['items']==[]


def test_cross_base_filter_and_fts_characters_are_safe(knowledge_app):
    with TestClient(knowledge_app) as client:
        a=client.post('/api/v1/knowledge/bases',json={'name':'A'}).json()['id']
        b=client.post('/api/v1/knowledge/bases',json={'name':'B'}).json()['id']
        r=client.post(f'/api/v1/knowledge/bases/{a}/documents?filename=a.txt',content=b'ionosphere delay')
        wait_job(client,r.json()['job']['id'])
        foreign=r.json()['document']['id']
        hits=client.post(f'/api/v1/knowledge/bases/{b}/search',json={'query':'ionosphere','document_ids':[foreign]})
        assert hits.status_code in (200,422)
        if hits.status_code==200: assert hits.json()['items']==[]
        query=client.post(f'/api/v1/knowledge/bases/{a}/search',json={'query':'" OR * NEAR( ) - :','limit':8})
        assert query.status_code==200,query.text
        assert client.get('/api/v1/knowledge/documents/'+str(uuid4())+'/original').status_code==404


def test_knowledge_root_cannot_replace_runs_or_code(tmp_path):
    from great_agent.config import AppSettings
    base={'project_root':tmp_path,'run_root':tmp_path/'runs','database_path':tmp_path/'app.db',
          'model':{'name':'test','base_url':'https://example.invalid'},
          'pppflt':{'executable':tmp_path/'unused.exe','template_path':tmp_path/'unused.xml','observation_root':tmp_path/'obs','product_root':tmp_path/'products'}}
    for root in [tmp_path, tmp_path/'runs',tmp_path/'backend',tmp_path/'frontend'/'dist',tmp_path.parent/'outside']:
        settings=AppSettings.model_validate({**base,'knowledge':{'root':root}})
        with pytest.raises(ValueError): settings.assert_safe_paths()


def test_streamed_upload_limit_and_unknown_base_leave_no_staging(knowledge_app):
    with TestClient(knowledge_app) as client:
        base=client.post('/api/v1/knowledge/bases',json={'name':'Small'}).json()['id']
        service=knowledge_app.state.services.knowledge_service
        service.storage.max_upload_bytes=4
        response=client.post(f'/api/v1/knowledge/bases/{base}/documents?filename=a.txt',content=iter([b'abc',b'def']))
        assert response.status_code==413
        assert list((service.storage.root/'staging').glob('upload-*.part'))==[]
        response=client.post(f'/api/v1/knowledge/bases/{uuid4()}/documents?filename=a.txt',content=b'abc')
        assert response.status_code==404


@pytest.mark.asyncio
async def test_service_stop_requeues_claim_and_next_owner_can_finish(tmp_path):
    import asyncio
    from great_agent.db.engine import create_engine_and_session_factory
    from great_agent.db.models import Base
    from great_agent.db.knowledge_repository import KnowledgeRepository
    from great_agent.knowledge.storage import KnowledgeStorage
    from great_agent.services.knowledge_service import KnowledgeService
    engine,sessions=create_engine_and_session_factory(tmp_path/'queue.db')
    Base.metadata.create_all(engine)
    repo=KnowledgeRepository(sessions)
    storage=KnowledgeStorage(tmp_path/'knowledge')
    base=repo.create_base('Restart')
    upload=repo.register_upload(base['id'],**storage.store_bytes(b'Clock bias corrections','clock.txt'),filename='clock.txt',parser_name='pdfplumber')
    first=KnowledgeService(repo,storage,poll_interval_seconds=0.01)
    entered=asyncio.Event()
    async def pause_before_parser(job):
        entered.set()
        await asyncio.Event().wait()
    first._parse=pause_before_parser
    await first.start()
    await asyncio.wait_for(entered.wait(),3)
    assert repo.get_job(upload['job']['id'])['status']=='parsing'
    await first.stop()
    assert repo.get_job(upload['job']['id'])['status']=='queued'
    second=KnowledgeService(repo,storage,poll_interval_seconds=0.01)
    await second.start()
    try:
        async with asyncio.timeout(20):
            while repo.get_job(upload['job']['id'])['status']!='completed':
                await asyncio.sleep(0.02)
        assert repo.search(base['id'],'clock')['items']
        assert second._process is None
        assert not list((storage.root/'staging').glob('**/parsed.json*'))
    finally:
        await second.stop()
        engine.dispose()


@pytest.mark.asyncio
async def test_parser_timeout_reaps_child_and_reports_terminal_failure(tmp_path):
    import asyncio
    from great_agent.db.engine import create_engine_and_session_factory
    from great_agent.db.models import Base
    from great_agent.db.knowledge_repository import KnowledgeRepository
    from great_agent.knowledge.storage import KnowledgeStorage
    from great_agent.services.knowledge_service import KnowledgeService
    engine,sessions=create_engine_and_session_factory(tmp_path/'timeout.db')
    Base.metadata.create_all(engine)
    repo=KnowledgeRepository(sessions); storage=KnowledgeStorage(tmp_path/'knowledge')
    base=repo.create_base('Timeout')
    upload=repo.register_upload(base['id'],**storage.store_bytes(b'Clock corrections','clock.txt'),filename='clock.txt',parser_name='pdfplumber')
    service=KnowledgeService(repo,storage,parser_timeout_seconds=0.0001,poll_interval_seconds=0.01)
    await service.start()
    try:
        async with asyncio.timeout(5):
            while repo.get_job(upload['job']['id'])['status']!='failed':
                await asyncio.sleep(0.01)
        assert repo.get_job(upload['job']['id'])['error_code']=='parse_timeout'
        assert service._process is None
        assert not list((storage.root/'staging').glob('**/*'))
        assert repo.get_document(upload['document']['id'])['active_version_id'] is None
    finally:
        await service.stop()
        engine.dispose()


@pytest.mark.asyncio
async def test_stop_during_failed_claim_does_not_lose_cancellation(tmp_path):
    import asyncio
    import threading
    from great_agent.db.engine import create_engine_and_session_factory
    from great_agent.db.models import Base
    from great_agent.db.knowledge_repository import KnowledgeRepository
    from great_agent.knowledge.storage import KnowledgeStorage
    from great_agent.services.knowledge_service import KnowledgeService
    engine,sessions=create_engine_and_session_factory(tmp_path/'queue.db'); Base.metadata.create_all(engine)
    repo=KnowledgeRepository(sessions)
    entered,release=threading.Event(),threading.Event()
    def failed_claim(owner):
        if entered.is_set():
            return None
        entered.set(); release.wait(3); raise RuntimeError('simulated claim database failure')
    repo.claim_job=failed_claim
    service=KnowledgeService(repo,KnowledgeStorage(tmp_path/'knowledge'),poll_interval_seconds=0.01)
    await service.start(); loop_task=service._task
    try:
        await asyncio.to_thread(entered.wait,2)
        stopping=asyncio.create_task(service.stop())
        await asyncio.sleep(0)
        release.set()
        done,_=await asyncio.wait({stopping},timeout=0.3)
        assert stopping in done,'stop lost cancellation when claim failed'
        await stopping
    finally:
        release.set()
        loop_task.cancel()
        await asyncio.gather(loop_task,return_exceptions=True)
        await service.stop()
        engine.dispose()


@pytest.mark.asyncio
async def test_start_cancellation_keeps_recovery_under_owner_lock(tmp_path):
    import asyncio
    import threading
    from great_agent.db.engine import create_engine_and_session_factory
    from great_agent.db.models import Base
    from great_agent.db.knowledge_repository import KnowledgeRepository
    from great_agent.knowledge.storage import KnowledgeStorage
    from great_agent.services.knowledge_service import KnowledgeService
    engine,sessions=create_engine_and_session_factory(tmp_path/'queue.db'); Base.metadata.create_all(engine)
    repo=KnowledgeRepository(sessions); entered,release=threading.Event(),threading.Event()
    original=repo.recover_jobs
    def delayed_recovery(owner):
        entered.set(); release.wait(3); return original(owner)
    repo.recover_jobs=delayed_recovery
    service=KnowledgeService(repo,KnowledgeStorage(tmp_path/'knowledge'))
    starting=asyncio.create_task(service.start())
    try:
        await asyncio.to_thread(entered.wait,2)
        starting.cancel()
        await asyncio.sleep(0.05)
        assert not starting.done(),'startup released owner while recovery thread still running'
    finally:
        release.set()
        await asyncio.gather(starting,return_exceptions=True)
        await service.stop(); engine.dispose()


@pytest.mark.asyncio
async def test_same_queue_cannot_start_two_owners_with_different_storage_roots(tmp_path):
    from great_agent.db.engine import create_engine_and_session_factory
    from great_agent.db.models import Base
    from great_agent.db.knowledge_repository import KnowledgeRepository
    from great_agent.knowledge.storage import KnowledgeStorage
    from great_agent.services.knowledge_service import KnowledgeService
    engine,sessions=create_engine_and_session_factory(tmp_path/'queue.db'); Base.metadata.create_all(engine)
    repo=KnowledgeRepository(sessions)
    a=KnowledgeService(repo,KnowledgeStorage(tmp_path/'a'))
    b=KnowledgeService(repo,KnowledgeStorage(tmp_path/'b'))
    await a.start()
    try:
        with pytest.raises(RuntimeError): await b.start()
    finally:
        await a.stop(); await b.stop(); engine.dispose()


def test_runtime_refuses_unknown_database_before_creating_any_tables(knowledge_app,tmp_path):
    import sqlite3
    from great_agent.cli import build_app
    path=tmp_path/'unknown.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE experiments (id TEXT PRIMARY KEY,status REAL)')
    settings=knowledge_app.state.settings.model_copy(deep=True)
    settings.database_path=path
    with pytest.raises(RuntimeError): build_app(settings)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()==[('experiments',)]
