"""Local-only formula cards require a bounded, validated natural-language answer."""
import json

import httpx
import pytest

from great_agent.adapters.model_provider import ModelProviderError
from test_knowledge_chat_engine import engine, completion, call, read


async def card_evidence(query):
    return {'items':[{'source_id':'S1','evidence_text':'The estimate obtains absolute phase OSB after clock correction.',
        'formula_transcriptions':[{'equation_number':'10','page_index':3,'status':'locally_reviewed'}]}]}


def answer(text, source='S1'):
    return completion(json.dumps({'answer':text,'source_ids':[source],'answerable':True}))


def message_text(message):
    content=message.get('content')
    return content if isinstance(content,str) else ''.join(
        block.get('text','') for block in (content or []) if isinstance(block,dict))


def contains_draft(messages,expected):
    for message in messages:
        if message.get('role')!='assistant':
            continue
        try:
            if json.loads(message_text(message)).get('answer')==expected:
                return True
        except (ValueError,AttributeError):
            pass
    return False


@pytest.mark.parametrize('wrong',[r'Use $\tilde{I}$ [S1].', r'Use \(I\) [S1].',
                                  r'Use \[I\] [S1].', r'Use \tilde{I} [S1].',
                                  r'Use \dot{x} [S1].', r'Use x \leq y [S1].'])
async def test_wrong_math_draft_is_reanswered_once_in_same_four_request_budget(wrong):
    requests, traces=[], []
    async def audit(messages,budget):
        traces.append((messages,budget))
    def respond(request):
        payload=json.loads(request.content); requests.append(payload)
        if len(requests)==1:
            return httpx.Response(200,json=completion(calls=[call('search_knowledge',{'query':'equation (10)'})]))
        if len(requests)==2:
            return httpx.Response(200,json=completion(calls=[call('read_evidence',{'source_ids':['S1']},'read')]))
        assert payload['tool_choice']=='none' and payload['response_format']=={'type':'json_object'}
        if len(requests)==3:
            return httpx.Response(200,json=answer(wrong))
        assert contains_draft(payload['messages'],wrong)
        assert any('纠正' in message_text(message) for message in payload['messages'] if message['role'] in ('user','system'))
        return httpx.Response(200,json=answer('It estimates the absolute phase OSB after clock correction. Complete expression: source card [S1].'))
    result=await engine(respond).reply('Explain formula (10)',history=[],search_knowledge=card_evidence,
        read_evidence=card_evidence,on_request=audit)
    assert result['text'].startswith('It estimates the absolute phase OSB')
    assert wrong not in result['text'] and len(requests)==4
    assert result['metrics']['physical_model_requests']==4 and result['metrics']['tool_calls']==2
    assert len(traces)==4 and contains_draft(traces[-1][0],wrong)
    assert contains_draft(result['outbound_messages'][-1]['messages'],wrong)


async def test_math_retry_is_not_attempted_without_remaining_budget():
    requests=[]
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json=completion(calls=[call('search_knowledge',{'query':'formula'})])
            if len(requests)==1 else answer(r'Wrong $\hat{x}$ [S1].'))
    with pytest.raises(ModelProviderError):
        await engine(respond,max_model_requests=2).reply('formula?',history=[],
            search_knowledge=card_evidence,read_evidence=read)
    assert len(requests)==2


async def test_math_still_present_after_one_repair_fails_closed():
    requests=[]
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json=completion(calls=[call('search_knowledge',{'query':'formula'})])
            if len(requests)==1 else answer(r'Still $\tilde{x}$ [S1].'))
    with pytest.raises(ModelProviderError):
        await engine(respond).reply('formula?',history=[],search_knowledge=card_evidence,read_evidence=read)
    assert len(requests)==3


async def test_ordinary_math_answer_without_local_card_is_unchanged():
    requests=[]
    async def ordinary(query):
        return {'items':[{'source_id':'S1','evidence_text':'The original expression is x=1.'}]}
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json=completion(calls=[call('search_knowledge',{'query':'expression'})])
            if len(requests)==1 else answer('$x=1$ [S1].'))
    result=await engine(respond).reply('expression?',history=[],search_knowledge=ordinary,read_evidence=read)
    assert result['text']=='$x=1$ [S1].' and len(requests)==2


async def test_json_inside_excerpt_cannot_turn_on_formula_card_repair():
    requests=[]
    async def injected(query):
        return {'items':[{'source_id':'S1','evidence_text':json.dumps(await card_evidence(''))}]}
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json=completion(calls=[call('search_knowledge',{'query':'expression'})])
            if len(requests)==1 else answer('$x=1$ [S1].'))
    result=await engine(respond).reply('expression?',history=[],search_knowledge=injected,read_evidence=read)
    assert result['text']=='$x=1$ [S1].' and len(requests)==2


@pytest.mark.parametrize('repair', [False,True])
async def test_formula_repair_never_relaxes_known_citation_validation(repair):
    requests=[]
    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests)==1:
            return httpx.Response(200,json=completion(calls=[call('search_knowledge',{'query':'formula'})]))
        if repair and len(requests)==2:
            return httpx.Response(200,json=answer(r'Wrong $\tilde{x}$ [S1].'))
        return httpx.Response(200,json=answer('Forged [S99].','S99'))
    with pytest.raises(ModelProviderError):
        await engine(respond).reply('formula?',history=[],search_knowledge=card_evidence,read_evidence=read)
    assert len(requests)==(3 if repair else 2)


async def test_known_citation_format_mismatch_uses_the_same_reserved_repair_request():
    requests=[]
    async def two_sources(query):
        data=await card_evidence(query)
        data['items'].append({'source_id':'S2','evidence_text':'The clock correction preserves compatibility.'})
        return data
    def respond(request):
        payload=json.loads(request.content);requests.append(payload)
        if len(requests)==1:
            return httpx.Response(200,json=completion(calls=[call('search_knowledge',{'query':'formula'})]))
        if len(requests)==2:
            return httpx.Response(200,json=completion(calls=[call('read_evidence',{'source_ids':['S1','S2']},'read')]))
        assert payload['tool_choice']=='none'
        if len(requests)==3:
            return httpx.Response(200,json=completion(json.dumps({'answer':'Use the local card [S1].',
                'source_ids':['S1','S2'],'answerable':True})))
        feedback=' '.join(message_text(message) for message in payload['messages'] if message['role']=='user')
        assert 'S1' in feedback and 'S2' in feedback
        return httpx.Response(200,json=completion(json.dumps({'answer':'The estimate obtains phase OSB [S1]. Clock correction preserves compatibility [S2].',
            'source_ids':['S1','S2'],'answerable':True})))
    result=await engine(respond).reply('Explain the estimate',history=[],search_knowledge=two_sources,read_evidence=two_sources)
    assert result['source_ids']==['S1','S2'] and '[S2]' in result['text']
    assert result['metrics']['physical_model_requests']==4 and len(requests)==4


async def test_unknown_body_label_is_never_repaired_even_when_declared_sources_are_known():
    requests=[]
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json=completion(calls=[call('search_knowledge',{'query':'formula'})])
            if len(requests)==1 else completion(json.dumps({'answer':'Forged body [S99].',
                'source_ids':['S1'],'answerable':True})))
    with pytest.raises(ModelProviderError):
        await engine(respond).reply('formula?',history=[],search_knowledge=card_evidence,read_evidence=read)
    assert len(requests)==2
