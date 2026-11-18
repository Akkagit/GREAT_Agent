import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, api, browserConfig } from '../api/client'
import { executionPreview } from './executionFixture'

afterEach(() => vi.unstubAllGlobals())

it('aborting a chat request reaches the HTTP transport', async () => {
  vi.stubGlobal('fetch', (_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
    init.signal?.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')))
  }))
  const controller = new AbortController()
  const pending = api.proposeChat('experiment', 'change date', controller.signal)
  const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' })
  controller.abort()
  await rejected
})

it('sends explicit proposal lineage and uses persisted list, detail and reject endpoints', async () => {
  const transport=vi.fn().mockResolvedValue({ok:true,json:async()=>({})})
  vi.stubGlobal('fetch',transport)
  await api.previewProposal('experiment',{source:'card',candidate:executionPreview.spec,base_revision_id:'original-revision',source_proposal_id:'original-proposal'})
  expect(transport).toHaveBeenLastCalledWith(`${browserConfig.apiBaseUrl}/experiments/experiment/proposals`,expect.objectContaining({method:'POST',body:JSON.stringify({source:'card',candidate:executionPreview.spec,base_revision_id:'original-revision',source_proposal_id:'original-proposal'})}))
  await api.listProposals('experiment','pending')
  expect(transport).toHaveBeenLastCalledWith(`${browserConfig.apiBaseUrl}/experiments/experiment/proposals?status=pending`,expect.any(Object))
  await api.getProposal('experiment','proposal')
  expect(transport).toHaveBeenLastCalledWith(`${browserConfig.apiBaseUrl}/experiments/experiment/proposals/proposal`,expect.any(Object))
  await api.rejectProposal('experiment','proposal')
  expect(transport).toHaveBeenLastCalledWith(`${browserConfig.apiBaseUrl}/experiments/experiment/proposals/proposal/reject`,expect.objectContaining({method:'POST'}))
})

it('binds input validation HTTP requests to the expected revision identity',async()=>{
  const transport=vi.fn().mockResolvedValue({ok:true,json:async()=>({revision_id:'r',revision:2,ok:true,missing:[]})})
  vi.stubGlobal('fetch',transport)
  await api.validateExperiment('experiment',{expected_revision_id:'r',expected_revision:2})
  expect(transport).toHaveBeenLastCalledWith(`${browserConfig.apiBaseUrl}/experiments/experiment/validate`,expect.objectContaining({method:'POST',body:JSON.stringify({expected_revision_id:'r',expected_revision:2})}))
})

it.each(['schema','invalid_json','timeout','provider'])('retains validated proposal error category %s and a UUID request ID',async category=>{
  const requestId='5326ab8e-ded0-4051-8fb5-80a3f13ad0dc'
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:false,status:502,json:async()=>({error:{code:'model_proposal_error',message:'固定公开提示',category,request_id:requestId}})}))
  await expect(api.proposeChat('experiment','去掉第一个测站')).rejects.toMatchObject({name:'ApiError',code:'model_proposal_error',status:502,category,requestId})
})

it.each([
  {category:'<script>secret</script>',request_id:'Bearer private-token'},
  {category:{schema:true},request_id:'5326ab8e-ded0-4051-8fb5-80a3f13ad0dc'+ 'x'.repeat(1000)},
  {category:null,request_id:null},
])('drops untrusted proposal diagnostics %#',async diagnostics=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:false,status:502,json:async()=>({error:{code:'model_proposal_error',message:'arbitrary provider text',...diagnostics}})}))
  const caught=await api.proposeChat('experiment','change').catch(error=>error)
  expect(caught).toBeInstanceOf(ApiError)
  expect(caught.category).toBeUndefined()
  expect(caught.requestId).toBeUndefined()
})


it('uses the frozen durable conversation contract for global and experiment scopes',async()=>{
  const transport=vi.fn().mockResolvedValue({ok:true,json:async()=>({})})
  vi.stubGlobal('fetch',transport)
  await api.ensureConversation(null)
  expect(transport).toHaveBeenLastCalledWith(`${browserConfig.apiBaseUrl}/conversations`,expect.objectContaining({method:'POST',body:JSON.stringify({experiment_id:null})}))
  await api.ensureConversation('experiment')
  expect(transport).toHaveBeenLastCalledWith(`${browserConfig.apiBaseUrl}/conversations`,expect.objectContaining({body:JSON.stringify({experiment_id:'experiment'})}))
  await api.getConversation('conversation')
  expect(transport).toHaveBeenLastCalledWith(`${browserConfig.apiBaseUrl}/conversations/conversation`,expect.any(Object))
  const controller=new AbortController(),requestId=crypto.randomUUID()
  await api.sendConversationMessage('conversation','theory',requestId,controller.signal)
  expect(transport).toHaveBeenLastCalledWith(`${browserConfig.apiBaseUrl}/conversations/conversation/messages`,expect.objectContaining({method:'POST',body:JSON.stringify({message:'theory',client_request_id:requestId}),signal:controller.signal}))
})

it('binds a conversation turn to the explicitly supplied execution preview',async()=>{
  const transport=vi.fn().mockResolvedValue({ok:true,json:async()=>({})})
  vi.stubGlobal('fetch',transport)
  const controller=new AbortController()
  await api.sendConversationMessage('conversation','XML 已复核，可以执行','request-1',controller.signal,'cda66bd3-2ffc-4ad5-9cda-76ae44bfba7a')
  expect(transport).toHaveBeenLastCalledWith(`${browserConfig.apiBaseUrl}/conversations/conversation/messages`,expect.objectContaining({
    method:'POST',signal:controller.signal,
    body:JSON.stringify({message:'XML 已复核，可以执行',client_request_id:'request-1',execution_preview_id:'cda66bd3-2ffc-4ad5-9cda-76ae44bfba7a'}),
  }))
})
