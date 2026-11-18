import json

import pytest

from test_research_materials import (material_context, add_artifact, add_literature,
    experiment_selection, persist_snapshot, historical_code_selection)  # noqa: F401


def toolset(ctx,selection,**kwargs):
    from great_agent.research.tools import ResearchToolset
    return ResearchToolset(ctx.materials,ctx.materials.capture(selection),**kwargs)


async def test_read_code_frozen_excerpt_diff_and_search(material_context):
    ctx=material_context
    (ctx.root/'a.py').write_text('def reset():\n    return 1\n',encoding='utf-8')
    (ctx.root/'b.py').write_text('def reset():\n    return 2\n',encoding='utf-8')
    tools=toolset(ctx,[{'kind':'code','source_id':'project','paths':['a.py','b.py']}])
    (ctx.root/'a.py').write_text('NEW',encoding='utf-8')
    found=await tools.call('search_code',{'query':'reset'})
    assert len(found['items'])==2
    before,after=tools.snapshot['materials']
    excerpt=await tools.call('read_code_excerpt',{'material_id':before['id'],'start_line':1,'end_line':2})
    assert 'return 1' in excerpt['evidence']['text']
    assert excerpt['evidence']['locator']['start_line']==1
    assert excerpt['evidence']['alias'].startswith('C')
    diff=await tools.call('read_code_diff',{'before_material_id':before['id'],'after_material_id':after['id']})
    assert '-    return 1' in diff['evidence']['text']
    assert '+    return 2' in diff['evidence']['text']


def report(value, *, unit='m', hold=600, reference='same-reference'):
    return json.dumps({'schema_version':'1.1','reference_method':'df_float_final_epoch',
        'policy':{'hold_seconds':hold,'interval_seconds':30},
        'statistics':{'scope':'all epochs'},'units':{'rms_3d_all_epochs_m':unit},
        'stations':[{'station':'ABCD','rms_3d_all_epochs_m':value,
                     'reference_sha256':reference,'reference_sow':12345}]})


async def test_existing_metric_comparison_checks_policy_reference_units_and_missing(material_context):
    ctx=material_context
    add_artifact(ctx,'a.json',report(2))
    add_artifact(ctx,'b.json',report(1))
    tools=toolset(ctx,[experiment_selection()])
    artifacts=tools.snapshot['materials'][0]['artifacts']
    args={'baseline_material_id':artifacts[0]['id'],'candidate_material_id':artifacts[1]['id'],
          'metric':'rms_3d_all_epochs_m'}
    result=await tools.call('compare_existing_metrics',args)
    assert result['compatible']
    assert result['rows'][0]['delta']==-1
    assert result['rows'][0]['unit']=='m'
    assert result['algorithm_version']=='existing_metrics_v1'
    assert result['evidence']['locator']['input_evidence_ids']
    for content in (report(1,unit='cm'),report(1,hold=300),report(1,reference='different')):
        # each comparison captures a new immutable material revision
        path=ctx.runs/'b.json'
        path.write_text(content,encoding='utf-8')
        tools=toolset(ctx,[experiment_selection()])
        result=await tools.call('compare_existing_metrics',args)
        assert not result['compatible'] and not result['rows']
    path.write_text(report(None),encoding='utf-8')
    tools=toolset(ctx,[experiment_selection()])
    result=await tools.call('compare_existing_metrics',args)
    assert result['rows'][0]['delta'] is None
    assert result['rows'][0]['missing']


async def test_zero_baseline_never_defines_relative_improvement(material_context):
    ctx=material_context
    add_artifact(ctx,'a.json',report(0))
    add_artifact(ctx,'b.json',report(1))
    tools=toolset(ctx,[experiment_selection()])
    a,b=tools.snapshot['materials'][0]['artifacts']
    result=await tools.call('compare_existing_metrics',{'baseline_material_id':a['id'],
        'candidate_material_id':b['id'],'metric':'rms_3d_all_epochs_m'})
    assert result['rows'][0]['relative_change'] is None


async def test_fixed_literature_search_and_read_and_access_revocation(material_context):
    from great_agent.db.knowledge_models import KnowledgeDocumentModel
    ctx=material_context
    _,doc,old=add_literature(ctx)
    tools=toolset(ctx,[{'kind':'literature','document_id':doc,'version_id':old}])
    result=await tools.call('search_literature',{'query':'reset'})
    assert result['items'][0]['evidence']['locator']['version_id']==old
    assert 'Historical reset' in result['items'][0]['evidence']['text']
    with ctx.sessions.begin() as session:
        session.get(KnowledgeDocumentModel,doc).enabled=False
    with pytest.raises(ValueError):
        await tools.call('search_literature',{'query':'reset'})


async def test_prior_evidence_reloads_authoritative_source_and_remaps_alias(material_context):
    ctx=material_context
    (ctx.root/'a.py').write_text('reset = 1\n',encoding='utf-8')
    selection=[{'kind':'code','source_id':'project','paths':['a.py']}]
    old=toolset(ctx,selection)
    material=old.snapshot['materials'][0]
    evidence=(await old.call('read_code_excerpt',{'material_id':material['id']}))['evidence']
    evidence['alias']='C99'
    new=toolset(ctx,selection,prior_evidence=[evidence])
    loaded=await new.call('load_prior_evidence',{'evidence_id':evidence['id']})
    assert loaded['evidence']['id']==evidence['id']
    assert loaded['evidence']['alias']=='C1'
    forged={**evidence,'text':'invented'}
    bad=toolset(ctx,selection,prior_evidence=[forged])
    with pytest.raises(ValueError):
        await bad.call('load_prior_evidence',{'evidence_id':forged['id']})
    ctx.settings.research.code_roots={}
    # Empty configured roots revokes project access rather than restoring a default.
    with pytest.raises(ValueError):
        await new.call('load_prior_evidence',{'evidence_id':evidence['id']})


async def test_tools_expose_only_bounded_read_schemas_and_reject_extra_arguments(material_context):
    ctx=material_context
    tools=toolset(ctx,[])
    names={d['function']['name'] for d in tools.definitions()}
    assert names=={'list_research_materials','read_experiment_summary','compare_existing_metrics',
        'read_artifact_excerpt','read_log_excerpt','search_code','read_code_excerpt','read_code_diff',
        'search_literature','read_literature_evidence','load_prior_evidence'}
    for definition in tools.definitions():
        assert definition['function']['parameters']['additionalProperties'] is False
    for name,args in [('shell',{'command':'whoami'}),('list_research_materials',{'path':'C:/secret'})]:
        with pytest.raises(ValueError):
            await tools.call(name,args)


async def test_prior_forged_identity_does_not_register_evidence(material_context):
    ctx=material_context
    (ctx.root/'safe.py').write_text('reset = 1',encoding='utf-8')
    selection=[{'kind':'code','source_id':'project','paths':['safe.py']}]
    previous=toolset(ctx,selection)
    material=previous.snapshot['materials'][0]
    evidence=(await previous.call('read_code_excerpt',{'material_id':material['id']}))['evidence']
    forged={**evidence,'id':'evidence:forged'}
    current=toolset(ctx,selection,prior_evidence=[forged])
    with pytest.raises(ValueError):
        await current.call('load_prior_evidence',{'evidence_id':forged['id']})
    assert current.evidence==[]


async def test_numeric_inputs_remain_exact_even_after_large_json_prefix_and_reload(material_context):
    ctx=material_context
    original=json.loads(report(2))
    original={'unrelated':'x'*25000,**original}
    add_artifact(ctx,'a.json',json.dumps(original))
    add_artifact(ctx,'b.json',report(1))
    selected=[experiment_selection()]
    previous=toolset(ctx,selected)
    a,b=previous.snapshot['materials'][0]['artifacts']
    result=await previous.call('compare_existing_metrics',{'baseline_material_id':a['id'],
        'candidate_material_id':b['id'],'metric':'rms_3d_all_epochs_m'})
    source=next(e for e in previous.evidence if e['id']==result['evidence']['locator']['input_evidence_ids'][0])
    assert 'rms_3d_all_epochs_m' in source['text']
    assert source['locator']['json_pointers']
    current=toolset(ctx,selected,prior_evidence=previous.evidence)
    loaded=await current.call('load_prior_evidence',{'evidence_id':result['evidence']['id']})
    assert set(loaded['evidence']['locator']['input_evidence_ids']) <= {e['id'] for e in current.evidence}


async def test_literature_borrows_available_dense_search_without_closing_shared_resource(material_context):
    from types import SimpleNamespace
    ctx=material_context
    _,doc,version=add_literature(ctx)
    snapshot=ctx.materials.capture([{'kind':'literature','document_id':doc,'version_id':version}])
    chunk=snapshot['materials'][0]['chunks'][0]
    class Dense:
        calls=0
        closed=False
        async def search(self,fixed,query,limit=20):
            self.calls+=1
            assert fixed['document_versions'][0]['version_id']==version
            return [{'parent_chunk_id':chunk['id'],'document_id':doc,'version_id':version},
                {'parent_chunk_id':'foreign','document_id':'elsewhere','version_id':'foreign'}]
        async def close(self):
            self.closed=True
    dense=Dense()
    ctx.materials.retriever=SimpleNamespace(dense=dense)
    from great_agent.research.tools import ResearchToolset
    tools=ResearchToolset(ctx.materials,snapshot)
    result=await tools.call('search_literature',{'query':'semantic term absent from source'})
    assert result['mode']=='hybrid' and len(result['items'])==1
    assert dense.calls==1 and not dense.closed


async def test_unavailable_dense_is_explicit_lexical_degradation(material_context):
    from types import SimpleNamespace
    from great_agent.knowledge.embedding import EmbeddingUnavailable
    ctx=material_context
    _,doc,version=add_literature(ctx)
    class Dense:
        async def search(self,*args,**kwargs):
            raise EmbeddingUnavailable('selected semantic index is not ready')
        async def close(self):
            raise AssertionError('shared dense resource must stay open')
    ctx.materials.retriever=SimpleNamespace(dense=Dense())
    tools=toolset(ctx,[{'kind':'literature','document_id':doc,'version_id':version}])
    result=await tools.call('search_literature',{'query':'reset'})
    assert result['mode']=='lexical_degraded'
    assert 'semantic' in result['warning']


async def test_long_source_line_is_bounded_and_prior_reload_keeps_exact_chars(material_context):
    ctx=material_context
    (ctx.root/'large.py').write_text('x'*25000,encoding='utf-8')
    selected=[{'kind':'code','source_id':'project','paths':['large.py']}]
    previous=toolset(ctx,selected)
    material=previous.snapshot['materials'][0]
    result=await previous.call('read_code_excerpt',{'material_id':material['id']})
    assert result['truncated'] and len(result['evidence']['text'])<=20000
    current=toolset(ctx,selected,prior_evidence=previous.evidence)
    loaded=await current.call('load_prior_evidence',{'evidence_id':result['evidence']['id']})
    assert loaded['evidence']['text']==result['evidence']['text']


async def test_prior_same_content_survives_git_head_change_and_preserves_immutable_metadata(material_context):
    ctx=material_context
    git=ctx.root/'.git'
    git.mkdir()
    (git/'HEAD').write_text('a'*40,encoding='ascii')
    (ctx.root/'safe.py').write_text('safe = 1',encoding='utf-8')
    selection=[{'kind':'code','source_id':'project','paths':['safe.py']}]
    previous=toolset(ctx,selection)
    evidence=(await previous.call('read_code_excerpt',{'material_id':previous.snapshot['materials'][0]['id']}))['evidence']
    (git/'HEAD').write_text('b'*40,encoding='ascii')
    current=toolset(ctx,selection,prior_evidence=previous.evidence)
    result=await current.call('load_prior_evidence',{'evidence_id':evidence['id']})
    assert {k:v for k,v in result['evidence'].items() if k!='alias'}=={k:v for k,v in evidence.items() if k!='alias'}


async def test_same_path_historical_diff_and_prior_alias_require_explicit_version_selection(material_context):
    ctx=material_context
    source=ctx.root/'solver.py'
    source.write_bytes(b'def reset():\n    return 1\n')
    live={'kind':'code','source_id':'project','paths':['solver.py']}
    previous=toolset(ctx,[live])
    old=previous.snapshot['materials'][0]
    evidence=(await previous.call('read_code_excerpt',{'material_id':old['id']}))['evidence']
    evidence['alias']='C99'
    run_id=persist_snapshot(ctx,previous.snapshot)
    historical=historical_code_selection(run_id,old)
    source.write_bytes(b'def reset():\n    return 2\n')
    current=toolset(ctx,[live,historical],prior_evidence=[evidence])
    new,selected_old=current.snapshot['materials']
    assert new['id']!=selected_old['id']
    loaded=await current.call('load_prior_evidence',{'evidence_id':evidence['id']})
    assert loaded['evidence']['alias']=='C1'
    assert loaded['evidence']['id']==evidence['id']
    diff=await current.call('read_code_diff',{'before_material_id':selected_old['id'],'after_material_id':new['id']})
    assert '-    return 1' in diff['evidence']['text']
    assert '+    return 2' in diff['evidence']['text']
    found=await current.call('search_code',{'query':'reset'})
    assert {item['material_id'] for item in found['items']}=={new['id'],selected_old['id']}
    only_new=toolset(ctx,[live],prior_evidence=[evidence])
    with pytest.raises(ValueError):
        await only_new.call('load_prior_evidence',{'evidence_id':evidence['id']})
