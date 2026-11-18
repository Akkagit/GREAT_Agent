"""Material isolation checks against real files and SQLite source identities."""
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import (Base, DailyTaskModel, ExperimentModel,
    PlanRevisionModel, ResultFileModel, TaskAttemptModel)


@pytest.fixture
def material_context(tmp_path):
    from great_agent.research.materials import ResearchMaterials
    from great_agent.research.repository import ResearchRepository  # noqa: F401
    engine, sessions = create_engine_and_session_factory(tmp_path / 'test.db')
    Base.metadata.create_all(engine)
    root = tmp_path / 'source'
    root.mkdir()
    runs = tmp_path / 'runs'
    runs.mkdir()
    knowledge = tmp_path / 'knowledge'
    knowledge.mkdir()
    settings = SimpleNamespace(project_root=root, run_root=runs,
        research=SimpleNamespace(code_roots={'project': root}),
        knowledge=SimpleNamespace(root=knowledge, enabled=True),
        model=SimpleNamespace(api_key='', name='mock'))
    with sessions.begin() as session:
        session.add(ExperimentModel(id='exp', status='completed'))
        session.flush()
        session.add(PlanRevisionModel(id='rev-old', experiment_id='exp', number=1,
            source='user', spec_json=json.dumps({'title':'Historical experiment'})))
        session.add(PlanRevisionModel(id='rev-new', experiment_id='exp', number=2,
            source='user', spec_json='{}'))
        session.flush()
        experiment=session.get(ExperimentModel,'exp')
        experiment.current_revision_id='rev-new'
        session.add(DailyTaskModel(id='task',experiment_id='exp',revision_id='rev-old',
            year=2026,doy=310,status='succeeded'))
        session.flush()
        session.add(TaskAttemptModel(id='attempt',task_id='task',number=1,status='succeeded'))
        session.add(TaskAttemptModel(id='new-attempt',task_id='task',number=2,status='succeeded'))
    materials = ResearchMaterials(sessions, settings)
    yield SimpleNamespace(materials=materials,sessions=sessions,settings=settings,
        root=root,runs=runs,knowledge=knowledge)
    engine.dispose()


def add_artifact(ctx, name, content, *, attempt='attempt', kind='analysis'):
    path=ctx.runs / name
    path.write_text(content,encoding='utf-8')
    with ctx.sessions.begin() as session:
        session.add(ResultFileModel(id=name,task_id='task',attempt_id=attempt,
            kind=kind,path=str(path),size_bytes=path.stat().st_size))
    return path


def experiment_selection():
    return {'kind':'experiment','experiment_id':'exp','task_id':'task',
            'attempt_id':'attempt','role':'baseline'}


def test_exact_historical_revision_attempt_and_immutable_artifacts(material_context):
    ctx=material_context
    path=add_artifact(ctx,'analysis.json','{"stations": [], "policy": {"hold_seconds":600}}')
    add_artifact(ctx,'new.json','{"secretly_new":true}',attempt='new-attempt')
    snapshot=ctx.materials.capture([experiment_selection()])
    experiment=snapshot['materials'][0]
    assert experiment['locator']['revision_id']=='rev-old'
    assert [a['locator']['artifact_id'] for a in experiment['artifacts']]==['analysis.json']
    original=experiment['artifacts'][0]['content']
    path.write_text('changed',encoding='utf-8')
    ctx.materials.validate(snapshot)
    assert experiment['artifacts'][0]['content']==original
    assert 'content' not in json.dumps(ctx.materials.summary(snapshot))
    path.unlink()
    ctx.materials.validate(snapshot)  # historical cached text remains separately available
    assert not ctx.materials.source_status(experiment['artifacts'][0])['original_available']


def test_code_snapshot_is_content_identified_and_cannot_claim_build_verification(material_context):
    ctx=material_context
    (ctx.root/'solver.py').write_text('def reset():\n    return 1\n',encoding='utf-8')
    selection={'kind':'code','source_id':'project','paths':['solver.py'],'provenance':'unknown'}
    snapshot=ctx.materials.capture([selection])
    code=snapshot['materials'][0]
    original=(ctx.root/'solver.py').read_bytes().decode('utf-8')
    (ctx.root/'solver.py').write_text('changed',encoding='utf-8')
    ctx.materials.validate(snapshot)
    assert code['content']==original
    assert code['sha256']==sha256(code['content'].encode()).hexdigest()
    assert 'code_execution_link_unknown' in code['quality_flags']
    with pytest.raises(ValueError,match='provenance'):
        ctx.materials.capture([{**selection,'provenance':'build_manifest_verified'}])


@pytest.mark.parametrize('path',['../outside.py','C:/secret.py','.env','config/app.toml','.git/config','key.pem'])
def test_code_rejects_traversal_secrets_and_arbitrary_paths(material_context,path):
    ctx=material_context
    target=ctx.root/path
    if ':' not in path and '..' not in path:
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text('secret',encoding='utf-8')
    with pytest.raises(ValueError):
        ctx.materials.capture([{'kind':'code','source_id':'project','paths':[path]}])


def test_code_rejects_symlink_and_secret_content(material_context):
    ctx=material_context
    (ctx.root/'credentials.py').write_text('API_KEY="sk-test-secret-value"',encoding='utf-8')
    with pytest.raises(ValueError):
        ctx.materials.capture([{'kind':'code','source_id':'project','paths':['credentials.py']}])
    target=ctx.root/'safe.py'
    target.write_text('print(1)',encoding='utf-8')
    try:
        (ctx.root/'linked.py').symlink_to(target)
    except OSError:
        pytest.skip('host does not permit symlink creation')
    with pytest.raises(ValueError):
        ctx.materials.capture([{'kind':'code','source_id':'project','paths':['linked.py']}])


def add_literature(ctx):
    from great_agent.db.knowledge_repository import KnowledgeRepository
    from great_agent.knowledge.storage import KnowledgeStorage
    from test_knowledge_repository import publish, parsed
    repo=KnowledgeRepository(ctx.sessions)
    base=repo.create_base('Literature')
    blob=KnowledgeStorage(ctx.knowledge).store_bytes(b'original paper', 'paper.txt')
    accepted=repo.register_upload(base['id'],blob['sha256'],blob['size_bytes'],
        blob['media_type'],blob['original_relpath'],'paper.txt','baseline')
    first=publish(repo,accepted,payload=parsed('Historical reset equation'))
    old=first['active_version_id']
    job=repo.queue_reparse(first['id'],'baseline','reparse')
    publish(repo,{'document':first,'job':job},payload=parsed('New text'))
    return repo, first['id'], old


def test_fixed_literature_version_preserves_original_page_and_quality(material_context):
    from great_agent.db.knowledge_models import KnowledgeDocumentModel
    ctx=material_context
    repo, doc, old=add_literature(ctx)
    snapshot=ctx.materials.capture([{'kind':'literature','document_id':doc,'version_id':old}])
    material=snapshot['materials'][0]
    assert material['locator']['version_id']==old
    assert material['chunks'][0]['text']=='Historical reset equation'
    assert material['chunks'][0]['locations'][0]['page_label']=='i'
    assert 'reading_order_heuristic' in material['chunks'][0]['quality_flags']
    ctx.materials.validate(snapshot)  # a newer active pointer is not revocation
    with ctx.sessions.begin() as session:
        session.get(KnowledgeDocumentModel,doc).enabled=False
    with pytest.raises(ValueError,match='unavailable|disabled'):
        ctx.materials.validate(snapshot)


def test_snapshot_manifest_tampering_and_unknown_attempt_are_rejected(material_context):
    ctx=material_context
    with pytest.raises(ValueError):
        ctx.materials.capture([{**experiment_selection(),'attempt_id':'other'}])
    (ctx.root/'safe.py').write_text('safe',encoding='utf-8')
    snapshot=ctx.materials.capture([{'kind':'code','source_id':'project','paths':['safe.py']}])
    snapshot['materials'][0]['content']='forged'
    with pytest.raises(ValueError,match='integrity|manifest|changed'):
        ctx.materials.validate(snapshot)


def test_oversized_logs_are_frozen_prefixes_and_other_files_fail_closed(material_context):
    ctx=material_context
    log=add_artifact(ctx,'solver.log','x'*(1024*1024+50),kind='log')
    snapshot=ctx.materials.capture([experiment_selection()])
    captured=snapshot['materials'][0]['artifacts'][0]
    assert captured['partial'] and captured['byte_end']==1024*1024
    assert captured['sha256']==sha256(log.read_bytes()[:captured['byte_end']]).hexdigest()
    assert 'partial_log_prefix' in captured['quality_flags']
    add_artifact(ctx,'large.json','x'*(1024*1024+1))
    with pytest.raises(ValueError,match='limit'):
        ctx.materials.capture([experiment_selection()])


def test_hardlinks_and_json_credentials_are_rejected(material_context):
    import os
    ctx=material_context
    target=ctx.root/'safe.py'
    target.write_text('print(1)',encoding='utf-8')
    os.link(target,ctx.root/'hard.py')
    with pytest.raises(ValueError,match='link'):
        ctx.materials.capture([{'kind':'code','source_id':'project','paths':['hard.py']}])
    (ctx.root/'settings.json').write_text('{"api_key": "sk-sensitive-value"}',encoding='utf-8')
    with pytest.raises(ValueError,match='credential'):
        ctx.materials.capture([{'kind':'code','source_id':'project','paths':['settings.json']}])


def test_git_commit_is_metadata_while_dirty_content_remains_authoritative(material_context):
    ctx=material_context
    git=ctx.root/'.git'
    (git/'refs'/'heads').mkdir(parents=True)
    (git/'HEAD').write_text('ref: refs/heads/main\n',encoding='ascii')
    (git/'refs'/'heads'/'main').write_text('a'*40+'\n',encoding='ascii')
    (ctx.root/'safe.py').write_text('dirty content',encoding='utf-8')
    captured=ctx.materials.capture([{'kind':'code','source_id':'project','paths':['safe.py']}])
    assert captured['materials'][0]['locator']['commit']=='a'*40
    assert captured['materials'][0]['sha256']==sha256(b'dirty content').hexdigest()


def test_growing_log_and_multibyte_prefix_never_claim_whole_file_hash(material_context,monkeypatch):
    from pathlib import Path
    from great_agent.research.materials import _read_stable
    ctx=material_context
    path=ctx.runs/'growing.log'
    path.write_bytes(b'initial')
    original_stat=Path.stat
    seen=0
    def growing_stat(selected,*args,**kwargs):
        nonlocal seen
        if selected==path:
            seen+=1
            if seen==2:
                with path.open('ab') as stream:
                    stream.write(b' appended')
        return original_stat(selected,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',growing_stat)
    captured=_read_stable(path,prefix=True)
    assert captured['content']=='initial'
    assert captured['partial'] and captured['byte_end']==7
    monkeypatch.setattr(Path,'stat',original_stat)
    path.write_bytes(b'x'*(1024*1024-1)+'文'.encode('utf-8'))
    captured=_read_stable(path,prefix=True)
    assert captured['partial'] and captured['byte_end']==1024*1024-1
    assert captured['sha256']==sha256(path.read_bytes()[:captured['byte_end']]).hexdigest()


@pytest.mark.parametrize('selection',[
    {'kind':[]}, {'kind':'experiment'},
    {'kind':'literature','document_id':[],'version_id':'v'},
    {'kind':'code','source_id':[],'paths':['safe.py']},
])
def test_malformed_selection_has_safe_validation_error(material_context,selection):
    with pytest.raises(ValueError):
        material_context.materials.capture([selection])


def persist_snapshot(ctx,snapshot):
    from great_agent.research.repository import ResearchRepository
    repository=ResearchRepository(ctx.sessions)
    session=repository.create_session(materials=snapshot['selection'],snapshot=snapshot)
    return repository.create_run(session['id'],request_id='snapshot-source',
        expected_version=session['version'],message='Capture source identity')['id']


def historical_code_selection(run_id,material):
    return {'kind':'code','source_id':material['locator']['source_id'],
        'paths':[material['locator']['path']],'snapshot_run_id':run_id,
        'snapshot_material_id':material['id']}


def test_explicit_old_and_new_code_versions_of_one_path_coexist(material_context):
    ctx=material_context
    source=ctx.root/'solver.py'
    source.write_bytes(b'def reset():\n    return 1\n')
    live={'kind':'code','source_id':'project','paths':['solver.py']}
    old_snapshot=ctx.materials.capture([live])
    old=old_snapshot['materials'][0]
    run_id=persist_snapshot(ctx,old_snapshot)
    source.write_bytes(b'def reset():\n    return 2\n')
    historical=historical_code_selection(run_id,old)
    snapshot=ctx.materials.capture([historical,live])
    before,after=snapshot['materials']
    assert before==old
    assert before['id']!=after['id']
    assert after['sha256'] in after['id']
    assert before['content']=='def reset():\n    return 1\n'
    assert after['content']=='def reset():\n    return 2\n'
    assert snapshot['selection'][0]==historical
    assert not ctx.materials.source_status(before)['original_matches_snapshot']
    source.unlink()
    restored=ctx.materials.capture([historical])
    assert restored['materials'][0]==old


@pytest.mark.parametrize('change',[
    {'paths':['other.py']}, {'paths':['solver.py','other.py']},
    {'snapshot_material_id':'not-in-snapshot'}, {'source_id':'other'},
    {'snapshot_run_id':'not-a-real-run'}, {'snapshot_run_id':None},
    {'snapshot_run_id':[]}, {'snapshot_material_id':None},
    {'snapshot':{'content':'browser supplied source'}},
])
def test_historical_code_rejects_mismatched_or_untrusted_selectors(material_context,change):
    ctx=material_context
    (ctx.root/'solver.py').write_bytes(b'return_value = 1')
    snapshot=ctx.materials.capture([{'kind':'code','source_id':'project','paths':['solver.py']}])
    run_id=persist_snapshot(ctx,snapshot)
    selected=historical_code_selection(run_id,snapshot['materials'][0])
    with pytest.raises(ValueError):
        ctx.materials.capture([{**selected,**change}])


def test_historical_code_requires_both_selectors_and_rejects_duplicate_versions(material_context):
    ctx=material_context
    (ctx.root/'solver.py').write_bytes(b'return_value = 1')
    live={'kind':'code','source_id':'project','paths':['solver.py']}
    snapshot=ctx.materials.capture([live])
    run_id=persist_snapshot(ctx,snapshot)
    selected=historical_code_selection(run_id,snapshot['materials'][0])
    for key in ('snapshot_run_id','snapshot_material_id'):
        incomplete={k:v for k,v in selected.items() if k!=key}
        with pytest.raises(ValueError):
            ctx.materials.capture([incomplete])
    with pytest.raises(ValueError,match='duplicate'):
        ctx.materials.capture([selected,live])
    with pytest.raises(ValueError,match='duplicate'):
        ctx.materials.capture([selected,selected])


def test_historical_code_revalidates_manifest_and_registered_root(material_context):
    from copy import deepcopy
    ctx=material_context
    (ctx.root/'solver.py').write_bytes(b'return_value = 1')
    snapshot=ctx.materials.capture([{'kind':'code','source_id':'project','paths':['solver.py']}])
    run_id=persist_snapshot(ctx,snapshot)
    selected=historical_code_selection(run_id,snapshot['materials'][0])
    damaged=deepcopy(snapshot)
    damaged['materials'][0]['content']='forged stored content'
    bad_run=persist_snapshot(ctx,damaged)
    with pytest.raises(ValueError,match='integrity|manifest'):
        ctx.materials.capture([{**selected,'snapshot_run_id':bad_run}])
    ctx.settings.research.code_roots={}
    with pytest.raises(ValueError,match='registered'):
        ctx.materials.capture([selected])


def test_historical_selector_cannot_upgrade_code_provenance(material_context):
    ctx=material_context
    (ctx.root/'solver.py').write_bytes(b'return_value = 1')
    snapshot=ctx.materials.capture([{'kind':'code','source_id':'project','paths':['solver.py']}])
    run_id=persist_snapshot(ctx,snapshot)
    selection=historical_code_selection(run_id,snapshot['materials'][0])
    with pytest.raises(ValueError,match='provenance'):
        ctx.materials.capture([{**selection,'provenance':'user_declared'}])
