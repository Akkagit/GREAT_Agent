import json
import httpx
import pytest
from test_knowledge_chat_engine import completion,call,evidence,engine
from great_agent.adapters.model_provider import ModelProviderError

@pytest.mark.asyncio
async def test_latex_answer_is_json_decoded_once_and_formula_instructions_sent():
    requests=[]
    text='## 核心结论\n**公式可由证据核验。**\n\n## 具体公式\n$$\\frac{x_i^2}{\\lambda_i}=b_i\\tag{3}$$\n符号说明见原文[S1]。'
    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests)==1:return httpx.Response(200,json=completion(calls=[call('search_knowledge',{'query':'equation (3)'})]))
        return httpx.Response(200,json=completion(json.dumps({'answer':text,'source_ids':['S1'],'answerable':True})))
    result=await engine(respond).reply('具体公式是什么？',history=[],search_knowledge=evidence,read_evidence=evidence)
    assert result['text']==text
    assert '具体公式' in str(requests[0]['messages']) and '符号' in str(requests[0]['messages'])

@pytest.mark.asyncio
async def test_optional_formula_reservation_keeps_total_physical_budget():
    requests=[]
    def respond(request):
        requests.append(request)
        return httpx.Response(200,json=completion(calls=[call('search_knowledge',{'query':'equation (3)'},str(len(requests)))]))
    with pytest.raises(ModelProviderError):
        await engine(respond).reply('formula?',history=[],search_knowledge=evidence,read_evidence=evidence,reserved_model_requests=1)
    assert len(requests)==3

@pytest.mark.asyncio
async def test_last_physical_request_is_reserved_for_a_grounded_final_answer():
    requests=[]
    traces=[]
    def respond(request):
        body=json.loads(request.content)
        requests.append(body)
        if body.get('tool_choice')=='none':
            return httpx.Response(200,json=completion(json.dumps({'answer':'**结论**：按分钟评估[S1]。','source_ids':['S1'],'answerable':True})))
        return httpx.Response(200,json=completion(calls=[call('search_knowledge',{'query':'PPP convergence'},str(len(requests)))]))
    async def trace(messages,budget):traces.append(messages)
    result=await engine(respond).reply('如何评估？',history=[],search_knowledge=evidence,read_evidence=evidence,on_request=trace)
    assert result['answerable'] is True
    assert len(requests)==4 and requests[-1]['tool_choice']=='none'
    assert requests[-1]['response_format']=={'type':'json_object'}
    assert all(r.get('tool_choice')!='none' for r in requests[:-1])
    assert traces[-1]==requests[-1]['messages']
    assert '最终JSON' in requests[-1]['messages'][-1]['content']

@pytest.mark.asyncio
async def test_local_formula_card_has_explicit_model_presentation_contract():
    requests=[]
    async def local_formula(query):
        return {'items':[{'source_id':'S1','evidence_text':'An observation equation is reconstructed.',
            'formula_transcriptions':[{'equation_number':'7','page_index':2,'status':'locally_reviewed'}]}]}
    def respond(request):
        body=json.loads(request.content);requests.append(body)
        if len(requests)==1:return httpx.Response(200,json=completion(calls=[call('search_knowledge',{'query':'equation 7'})]))
        return httpx.Response(200,json=completion(json.dumps({'answer':'原文公式卡[S1]。','source_ids':['S1'],'answerable':True})))
    await engine(respond).reply('说明公式7',history=[],search_knowledge=local_formula,read_evidence=local_formula)
    contracts=[m['content'] for m in requests[-1]['messages'] if m['role']=='system']
    assert any('不输出LaTeX' in text and '原文公式卡[S1]' in text for text in contracts)
