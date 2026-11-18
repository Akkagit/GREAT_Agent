import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, browserConfig } from '../api/client'
import type { ExperimentSpec, Proposal } from '../api/types'
import { ExperimentWorkspace } from '../pages/ExperimentWorkspace'
import { selectOption, selectedOption } from './selectOption'
import { legacyMonitorSnapshot } from './monitorFixture'


const baseSpec:ExperimentSpec = {
  schema_version: '1.0',
  workflow: 'pppflt',
  scenario: {
    mode: 'static',
    frequency: 'DF',
    ambiguity: 'float',
    reset_hours: 0,
  },
  date_segments: [{ year: 2024, start_doy: 350, end_doy: 360 }],
  excluded_dates: [],
  station_group_id: 'default_15',
  stations: Array.from({ length: 15 }, (_, index) => `S${String(index).padStart(3, '0')}`),
  input_profile_id: 'local_default',
  solver_profile_id: 'great_local',
  template_profile_id: 'pppflt_base',
  concurrency: 1,
  outputs: ['flt', 'log'],
}

const apiMock = vi.hoisted(() => ({
  getExperiment: vi.fn(),
  ensureConversation: vi.fn(), getConversation: vi.fn(), sendConversationMessage: vi.fn(),
  previewProposal: vi.fn(),
  listProposals: vi.fn(), rejectProposal: vi.fn(),
  acceptRevision: vi.fn(),
  validateExperiment: vi.fn(),
  freezeExperiment: vi.fn(),
  listTasks: vi.fn(), prepareExecution: vi.fn(), confirmExecution: vi.fn(), getExecutionPreview: vi.fn(),
  getExperimentMonitor: vi.fn(), openExperimentEvents: vi.fn(),
}))

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>()
  return { ...original, api: apiMock }
})

const experiment = {
  id: 'exp-1',
  status: 'draft',
  current_revision: 1,
  current_revision_id: 'revision-1',
  spec: baseSpec,
}

const chatProposal:Proposal = {
  id: 'proposal-chat',
  source: 'chat',
  base_revision_id: 'revision-1', base_revision_number: 1,
  candidate_hash: 'hash-chat', status: 'pending', source_proposal_id: null,
  explanation: '切换到三频解算',
  candidate: {
    ...baseSpec,
    scenario: { ...baseSpec.scenario, frequency: 'TF' },
  },
  diff: [{ path: '/scenario/frequency', before: 'DF', after: 'TF' }],
}

function turn(proposal?:Proposal,text=proposal?.explanation ?? '双频组合可以消除一阶电离层延迟。') {
  return {conversation_id:'conversation-1',run_id:'run-1',request_id:proposal && 'request_id' in proposal ? proposal.request_id : 'request-1',status:'completed',timings:proposal && 'timings' in proposal ? proposal.timings : {},user_message:{id:crypto.randomUUID(),role:'user',status:'completed',blocks:[{type:'text',text:'问题'}]},assistant_message:{id:crypto.randomUUID(),role:'assistant',status:'completed',blocks:[{type:'text',text},...(proposal?[{type:'proposal',proposal}]:[])]}}
}
describe('ExperimentWorkspace', () => {
  it('retries failed initial history on send and merges its authoritative messages',async()=>{
    apiMock.ensureConversation.mockRejectedValueOnce(new Error('offline'))
    const old=turn(undefined,'恢复连接后的历史回答')
    const detail={id:'conversation-1',experiment_id:'exp-1',title:'对话',messages:[old.user_message,old.assistant_message]}
    apiMock.ensureConversation.mockResolvedValueOnce(detail)
    apiMock.sendConversationMessage.mockResolvedValueOnce(turn(undefined,'重试成功'))
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await screen.findByText('对话记录读取失败；发送时将重试连接。')
    fireEvent.change(screen.getByRole('textbox',{name:/message/i}),{target:{value:'再次咨询'}})
    fireEvent.click(screen.getByRole('button',{name:'发送'}))
    await screen.findByText('重试成功')
    expect(screen.getByText('恢复连接后的历史回答')).toBeInTheDocument()
    expect(apiMock.ensureConversation).toHaveBeenCalledTimes(2)
  })
  it('merges durable history in order when first send races initial hydration',async()=>{
    let initial!:(value:unknown)=>void
    const old=turn(undefined,'已有的理论回答')
    old.user_message.blocks=[{type:'text',text:'已有的问题'}]
    const detail={id:'conversation-1',experiment_id:'exp-1',title:'对话',messages:[old.user_message,old.assistant_message]}
    apiMock.ensureConversation.mockImplementationOnce(()=>new Promise(done=>{initial=done})).mockResolvedValueOnce(detail)
    const next=turn(undefined,'新的回答')
    next.user_message.blocks=[{type:'text',text:'新的问题'}]
    apiMock.sendConversationMessage.mockResolvedValueOnce(next)
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await screen.findByText('Revision 1')
    fireEvent.change(screen.getByRole('textbox',{name:/message/i}),{target:{value:'新的问题'}})
    fireEvent.click(screen.getByRole('button',{name:'发送'}))
    await screen.findByText('新的回答')
    await act(async()=>initial(detail))
    const chat=screen.getByRole('complementary',{name:'模型对话区'})
    expect(within(chat).getByText('已有的问题')).toBeInTheDocument()
    expect(within(chat).getByText('已有的理论回答')).toBeInTheDocument()
    expect([...chat.querySelectorAll('.chat-bubble p')].map(row=>row.textContent)).toEqual(['已有的问题','已有的理论回答','新的问题','新的回答'])
  })
  it.each(['failure','cancel'])('reconciles optimistic user after %s and cached scope remount',async outcome=>{
    let fail!:(error:Error)=>void
    apiMock.sendConversationMessage.mockImplementationOnce(()=>new Promise((_done,reject)=>{fail=reject}))
    let cached:import('../pages/ExperimentWorkspace').WorkbenchSession|undefined
    const remember=(value:import('../pages/ExperimentWorkspace').WorkbenchSession)=>{cached=value}
    const page=render(<ExperimentWorkspace experimentId="exp-1" onSessionChange={remember}/>)
    await waitFor(()=>expect(screen.getByRole('button',{name:'检查输入'})).toBeEnabled())
    fireEvent.change(screen.getByRole('textbox',{name:/message/i}),{target:{value:'已持久化的问题'}})
    fireEvent.click(screen.getByRole('button',{name:'发送'}))
    const requestId=apiMock.sendConversationMessage.mock.calls[0][2]
    if(outcome==='failure')await act(async()=>fail(new Error('response lost')))
    else fireEvent.click(screen.getByRole('button',{name:'取消回复'}))
    await waitFor(()=>expect(cached?.messages.some(row=>row.text==='已持久化的问题')).toBe(true))
    page.unmount()
    const saved=turn(undefined,'服务端保存的回复')
    saved.user_message={...saved.user_message,request_id:requestId,blocks:[{type:'text',text:'已持久化的问题'}]} as typeof saved.user_message
    saved.assistant_message={...saved.assistant_message,request_id:requestId} as typeof saved.assistant_message
    apiMock.ensureConversation.mockResolvedValue({id:'conversation-1',experiment_id:'exp-1',title:'对话',messages:[saved.user_message,saved.assistant_message]})
    render(<ExperimentWorkspace experimentId="exp-1" session={cached}/>)
    await screen.findByText('服务端保存的回复')
    expect(screen.getAllByText('已持久化的问题')).toHaveLength(1)
  })
  it('upgrades a recovered proposal placeholder to durable mixed explanation',async()=>{
    const saved=turn(chatProposal,'这是包含理论依据的完整服务端解释')
    apiMock.ensureConversation.mockResolvedValue({id:'conversation-1',experiment_id:'exp-1',title:'对话',messages:[saved.assistant_message]})
    render(<ExperimentWorkspace experimentId="exp-1" session={{revision:1,pendingId:'proposal-proposal-chat',messages:[{role:'assistant',text:'已恢复待确认的提案。',cardId:'proposal-proposal-chat'}],cards:{'proposal-proposal-chat':{candidate:chatProposal.candidate,proposal:chatProposal,status:'pending',edited:true,base_revision_id:'revision-1',base_revision_number:1}}}}/>)
    await screen.findByText('这是包含理论依据的完整服务端解释')
    expect(screen.queryByText('已恢复待确认的提案。')).not.toBeInTheDocument()
    expect(screen.getAllByRole('region',{name:'计划修改卡片'})).toHaveLength(1)
    expect(screen.getByText('卡片已调整；接受时将先重新校验，再接受新的提案。')).toBeInTheDocument()
  })
  it('preserves historical text when hydration arrives after a local draft',async()=>{
    let initial!:(value:unknown)=>void
    apiMock.ensureConversation.mockImplementationOnce(()=>new Promise(done=>{initial=done}))
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await screen.findByText('Revision 1')
    fireEvent.click(screen.getByRole('button',{name:'手动调整参数'}))
    await act(async()=>initial({id:'conversation-1',experiment_id:'exp-1',title:'对话',messages:[turn(chatProposal,'历史混合解释').assistant_message]}))
    expect(screen.getByText('历史混合解释')).toBeInTheDocument()
    expect(screen.getAllByRole('region',{name:'计划修改卡片'})).toHaveLength(1)
  })
  it('restores durable mixed history once and deduplicates pending recovery',async()=>{
    const saved=turn(chatProposal)
    apiMock.ensureConversation.mockResolvedValue({id:'conversation-1',experiment_id:'exp-1',title:'对话',messages:[saved.user_message,saved.assistant_message]})
    apiMock.listProposals.mockResolvedValue([chatProposal])
    const page=render(<ExperimentWorkspace experimentId="exp-1" />)
    await screen.findByRole('button',{name:'接受修改'})
    await waitFor(()=>expect(apiMock.listProposals).toHaveBeenCalledTimes(1))
    expect(screen.getAllByRole('region',{name:'计划修改卡片'})).toHaveLength(1)
    page.unmount()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await screen.findByRole('button',{name:'接受修改'})
    expect(screen.getAllByRole('region',{name:'计划修改卡片'})).toHaveLength(1)
    expect(screen.getByText('切换到三频解算')).toBeInTheDocument()
  })
  it('hydrates accepted server status over cached pending without restoring input authorization',async()=>{
    const saved=turn({...chatProposal,status:'accepted'})
    apiMock.ensureConversation.mockResolvedValue({id:'conversation-1',experiment_id:'exp-1',title:'对话',messages:[saved.assistant_message]})
    render(<ExperimentWorkspace experimentId="exp-1" session={{revision:1,pendingId:'proposal-proposal-chat',messages:[{id:saved.assistant_message.id,role:'assistant',text:'切换到三频解算',cardId:'proposal-proposal-chat'}],cards:{'proposal-proposal-chat':{candidate:chatProposal.candidate,proposal:chatProposal,status:'pending',edited:false,base_revision_id:'revision-1',base_revision_number:1}}}} />)
    await screen.findByText('已接受修改，尚未授权执行。')
    expect(screen.queryByRole('button',{name:'接受修改'})).not.toBeInTheDocument()
    await waitFor(()=>expect(screen.getByRole('button',{name:'检查输入'})).toBeEnabled())
    expect(screen.getByRole('button',{name:'生成 XML 并预览'})).toBeDisabled()
    expect(apiMock.validateExperiment).not.toHaveBeenCalled()
  })
  it('ignores late persisted history after a new local draft is opened',async()=>{
    let finish!:(value:unknown)=>void
    apiMock.ensureConversation.mockImplementationOnce(()=>new Promise(done=>{finish=done}))
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await screen.findByText('Revision 1')
    fireEvent.click(screen.getByRole('button',{name:'手动调整参数'}))
    const user=userEvent.setup()
    await selectOption(user,'频率','三频 TF')
    await act(async()=>finish({id:'conversation-1',experiment_id:'exp-1',title:'对话',messages:[turn(chatProposal).assistant_message]}))
    expect(screen.getAllByRole('region',{name:'计划修改卡片'})).toHaveLength(1)
    expect(await selectedOption('频率')).toHaveTextContent('三频 TF')
    expect(apiMock.previewProposal).not.toHaveBeenCalled()
  })
  it('ignores old scope history and turn responses after switching experiments',async()=>{
    let finish!:(value:ReturnType<typeof turn>)=>void
    apiMock.sendConversationMessage.mockImplementationOnce(()=>new Promise(done=>{finish=done}))
    const page=render(<ExperimentWorkspace experimentId="exp-1" />)
    await waitFor(()=>expect(screen.getByRole('button',{name:'检查输入'})).toBeEnabled())
    fireEvent.change(screen.getByRole('textbox',{name:/message/i}),{target:{value:'旧实验问题'}})
    fireEvent.click(screen.getByRole('button',{name:'发送'}))
    apiMock.getExperiment.mockResolvedValue({...experiment,id:'exp-2'})
    apiMock.ensureConversation.mockResolvedValue({id:'conversation-2',experiment_id:'exp-2',title:'对话',messages:[]})
    page.rerender(<ExperimentWorkspace experimentId="exp-2" />)
    await waitFor(()=>expect(screen.getByRole('button',{name:'检查输入'})).toBeEnabled())
    await act(async()=>finish(turn(chatProposal,'旧实验不应出现')))
    expect(screen.queryByText('旧实验不应出现')).not.toBeInTheDocument()
    expect(screen.queryByRole('region',{name:'计划修改卡片'})).not.toBeInTheDocument()
  })
  it('retains pending recovery that finishes during a text-only turn',async()=>{
    let recover!:(value:Proposal[])=>void,reply!:(value:ReturnType<typeof turn>)=>void
    apiMock.listProposals.mockImplementationOnce(()=>new Promise(done=>{recover=done}))
    apiMock.sendConversationMessage.mockImplementationOnce(()=>new Promise(done=>{reply=done}))
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await waitFor(()=>expect(apiMock.listProposals).toHaveBeenCalledTimes(1))
    fireEvent.change(screen.getByRole('textbox',{name:/message/i}),{target:{value:'解释双频'}})
    fireEvent.click(screen.getByRole('button',{name:'发送'}))
    await act(async()=>recover([chatProposal]))
    await act(async()=>reply(turn()))
    expect(await screen.findByRole('button',{name:'接受修改'})).toBeEnabled()
    expect(screen.getAllByRole('region',{name:'计划修改卡片'})).toHaveLength(1)
  })
  it('allows theory consultation without replacing pending proposals',async()=>{
    apiMock.listProposals.mockResolvedValue([chatProposal])
    apiMock.sendConversationMessage.mockResolvedValueOnce(turn())
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await screen.findByRole('button',{name:'接受修改'})
    fireEvent.change(screen.getByRole('textbox',{name:/message/i}),{target:{value:'双频有什么优势？'}})
    fireEvent.click(screen.getByRole('button',{name:'发送'}))
    await screen.findByText('双频组合可以消除一阶电离层延迟。')
    expect(screen.getAllByRole('region',{name:'计划修改卡片'})).toHaveLength(1)
    expect(screen.getByRole('button',{name:'接受修改'})).toBeEnabled()
    expect(apiMock.acceptRevision).not.toHaveBeenCalled()
  })
  it('keeps a validated input gate after a no-op consultation',async()=>{
    apiMock.validateExperiment.mockResolvedValue({revision_id:'revision-1',revision:1,ok:true,missing:[]})
    apiMock.sendConversationMessage.mockResolvedValueOnce(turn())
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await waitFor(()=>expect(screen.getByRole('button',{name:'检查输入'})).toBeEnabled())
    fireEvent.click(screen.getByRole('button',{name:'检查输入'}))
    await waitFor(()=>expect(screen.getByRole('button',{name:'生成 XML 并预览'})).toBeEnabled())
    fireEvent.change(screen.getByRole('textbox',{name:/message/i}),{target:{value:'保持现有计划并解释双频'}})
    fireEvent.click(screen.getByRole('button',{name:'发送'}))
    await screen.findByText('双频组合可以消除一阶电离层延迟。')
    expect(screen.getByRole('button',{name:'生成 XML 并预览'})).toBeEnabled()
    expect(apiMock.listProposals).toHaveBeenCalledTimes(1)
  })
  it('allows frozen experiment consultation while plan edits stay disabled',async()=>{
    apiMock.getExperiment.mockResolvedValue({...experiment,status:'ready'})
    apiMock.sendConversationMessage.mockResolvedValueOnce(turn())
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await screen.findByText('Revision 1')
    fireEvent.change(screen.getByRole('textbox',{name:/message/i}),{target:{value:'解释当前实验'}})
    await waitFor(()=>expect(screen.getByRole('button',{name:'发送'})).toBeEnabled())
    fireEvent.click(screen.getByRole('button',{name:'发送'}))
    await screen.findByText('双频组合可以消除一阶电离层延迟。')
    expect(screen.getByRole('button',{name:'手动调整参数'})).toBeDisabled()
  })

  async function acceptRecovered(){
    apiMock.listProposals.mockResolvedValueOnce([chatProposal])
    const page=render(<ExperimentWorkspace experimentId="exp-1" />)
    await screen.findByRole('button',{name:'接受修改'})
    fireEvent.click(screen.getByRole('button',{name:'接受修改'}))
    await screen.findByText('Revision 2')
    return page
  }
  const ready2={revision_id:'revision-2',revision:2,ok:true,missing:[]}

  it('shows the workspace after inline XML preparation and preserves the mobile chat draft',async()=>{
    const {executionPreview}=await import('./executionFixture')
    let finishCheck!:(value:typeof ready2)=>void
    apiMock.validateExperiment.mockImplementationOnce(()=>new Promise(done=>{finishCheck=done}))
    apiMock.prepareExecution.mockResolvedValue(executionPreview)
    apiMock.getExecutionPreview.mockResolvedValue(executionPreview)
    apiMock.listProposals.mockResolvedValueOnce([chatProposal])
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await screen.findByRole('button',{name:'接受修改'})
    // jsdom does not evaluate <=880px media queries; expose navigation and assert the visible-pane classes.
    screen.getByRole('navigation',{hidden:true}).style.display='flex'
    fireEvent.click(screen.getByRole('button',{name:'对话'}))
    const draft=screen.getByRole('textbox',{name:/message/i})
    fireEvent.change(draft,{target:{value:'保留的对话草稿'}})
    fireEvent.click(screen.getByRole('button',{name:'接受修改'}))
    await waitFor(()=>expect(apiMock.validateExperiment).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('button',{name:'对话'})).toHaveAttribute('aria-pressed','true')
    await act(async()=>{finishCheck(ready2)})
    expect(screen.getByRole('button',{name:'对话'})).toHaveAttribute('aria-pressed','true')
    const handoff=screen.getByRole('region',{name:'输入检查交接'})
    fireEvent.click(within(handoff).getByRole('button',{name:'生成 XML 并预览'}))
    await screen.findByRole('button',{name:'查看 XML 预览'})
    expect(screen.getByRole('button',{name:'工作区'})).toHaveAttribute('aria-pressed','true')
    expect(screen.getByRole('complementary',{name:'模型对话区'})).not.toHaveClass('mobile-active')
    expect(screen.getByRole('region',{name:'实验内容区'})).toHaveClass('mobile-active')
    fireEvent.click(screen.getByRole('button',{name:'对话'}))
    expect(screen.getByRole('textbox',{name:/message/i})).toBe(draft)
    expect(draft).toHaveValue('保留的对话草稿')
    fireEvent.click(within(handoff).getByRole('button',{name:'查看 XML 预览'}))
    expect(screen.getByRole('button',{name:'工作区'})).toHaveAttribute('aria-pressed','true')
    expect(apiMock.prepareExecution).toHaveBeenCalledTimes(1)
  })

  it('respects manual mobile navigation while inline XML preparation is pending',async()=>{
    const {executionPreview}=await import('./executionFixture')
    let finish!:(value:typeof executionPreview)=>void
    apiMock.validateExperiment.mockResolvedValue(ready2)
    apiMock.prepareExecution.mockImplementationOnce(()=>new Promise(done=>{finish=done}))
    apiMock.getExecutionPreview.mockResolvedValue(executionPreview)
    const onPrepared=vi.fn()
    apiMock.listProposals.mockResolvedValueOnce([chatProposal])
    render(<ExperimentWorkspace experimentId="exp-1" onPrepared={onPrepared} />)
    await screen.findByRole('button',{name:'接受修改'})
    screen.getByRole('navigation',{hidden:true}).style.display='flex'
    fireEvent.click(screen.getByRole('button',{name:'对话'}))
    fireEvent.click(screen.getByRole('button',{name:'接受修改'}))
    await screen.findByText('输入检查通过')
    fireEvent.click(within(screen.getByRole('region',{name:'输入检查交接'})).getByRole('button',{name:'生成 XML 并预览'}))
    fireEvent.click(screen.getByRole('button',{name:'实验'}))
    await act(async()=>{finish(executionPreview)})
    expect(screen.getByRole('button',{name:'实验'})).toHaveAttribute('aria-pressed','true')
    expect(screen.getByRole('tab',{name:'参数解析'})).toHaveAttribute('aria-selected','true')
    expect(onPrepared).not.toHaveBeenCalled()
    expect(screen.getByRole('button',{name:'查看 XML 预览'})).toBeInTheDocument()
  })

  it.each([
    ['schema','字段校验'],['invalid_json','无法解析'],['timeout','响应超时'],['provider','模型服务暂时不可用'],
  ])('shows safe %s proposal failure and request ID beside the composer without changing the plan',async(category,explanation)=>{
    const requestId='5326ab8e-ded0-4051-8fb5-80a3f13ad0dc'
    apiMock.sendConversationMessage.mockRejectedValueOnce(Object.assign(new ApiError('secret raw provider content','model_proposal_error',502),{category,requestId}))
    render(<ExperimentWorkspace experimentId="exp-1" />)
    const input=await screen.findByRole('textbox',{name:/message/i})
    fireEvent.change(input,{target:{value:'我想要去掉第一个测站'}})
    fireEvent.click(screen.getByRole('button',{name:'发送'}))
    const chat=screen.getByRole('complementary',{name:'模型对话区'})
    expect(await within(chat).findByText(new RegExp(explanation))).toBeInTheDocument()
    expect(within(chat).getByText(new RegExp(requestId))).toBeInTheDocument()
    expect(chat).toHaveTextContent('当前计划未改变')
    expect(document.body).not.toHaveTextContent('secret raw provider content')
    expect(document.body).not.toHaveTextContent('直接编辑计划卡片')
    expect(screen.getByText('Revision 1')).toBeInTheDocument()
    expect(apiMock.acceptRevision).not.toHaveBeenCalled()
    await waitFor(()=>expect(apiMock.listProposals).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('button',{name:'检查输入'})).toBeEnabled()
  })

  it.each(['network','untrusted'])('shows a safe connection fallback for %s errors',async kind=>{
    const caught=kind==='network'?new TypeError('RAW_SECRET_NETWORK'):Object.assign(new Error('RAW_SECRET_PROVIDER'),{category:'RAW_SECRET_CATEGORY',requestId:'RAW_SECRET_ID'})
    apiMock.sendConversationMessage.mockRejectedValueOnce(caught)
    render(<ExperimentWorkspace experimentId="exp-1" />)
    fireEvent.change(await screen.findByRole('textbox',{name:/message/i}),{target:{value:'删除首站'}})
    fireEvent.click(screen.getByRole('button',{name:'发送'}))
    const chat=screen.getByRole('complementary',{name:'模型对话区'})
    expect(await within(chat).findByText(/网络连接/)).toBeInTheDocument()
    expect(chat).not.toHaveTextContent('请求编号')
    expect(document.body).not.toHaveTextContent('RAW_SECRET')
    expect(document.body).not.toHaveTextContent('直接编辑计划卡片')
  })

  it('ignores a failed old chat after cancellation and a new request',async()=>{
    let fail!:(error:Error)=>void
    apiMock.sendConversationMessage.mockImplementationOnce(()=>new Promise((_done,reject)=>{fail=reject})).mockResolvedValueOnce(turn(chatProposal))
    render(<ExperimentWorkspace experimentId="exp-1" />)
    const input=await screen.findByRole('textbox',{name:/message/i})
    fireEvent.change(input,{target:{value:'旧请求'}});fireEvent.click(screen.getByRole('button',{name:'发送'}))
    fireEvent.click(screen.getByRole('button',{name:'取消回复'}))
    fireEvent.change(input,{target:{value:'新请求'}});fireEvent.click(screen.getByRole('button',{name:'发送'}))
    await screen.findByRole('button',{name:'接受修改'})
    await act(async()=>{fail(Object.assign(new Error('RAW_SECRET'),{category:'timeout',requestId:'5326ab8e-ded0-4051-8fb5-80a3f13ad0dc'}))})
    expect(screen.queryByText(/响应超时/)).not.toBeInTheDocument()
    expect(screen.queryByText(/5326ab8e/)).not.toBeInTheDocument()
    expect(screen.getByRole('button',{name:'接受修改'})).toBeEnabled()
  })

  it('automatically checks accepted identity exactly once and prepares only from manual card action',async()=>{
    const {executionPreview}=await import('./executionFixture')
    apiMock.validateExperiment.mockResolvedValue(ready2)
    apiMock.prepareExecution.mockResolvedValue(executionPreview)
    apiMock.getExecutionPreview.mockResolvedValue(executionPreview)
    await acceptRecovered()
    const handoff=await screen.findByRole('region',{name:'输入检查交接'})
    await within(handoff).findByText('输入检查通过')
    expect(apiMock.validateExperiment).toHaveBeenCalledExactlyOnceWith('exp-1',{expected_revision_id:'revision-2',expected_revision:2})
    expect(apiMock.prepareExecution).not.toHaveBeenCalled()
    const button=within(handoff).getByRole('button',{name:'生成 XML 并预览'})
    const footerButton=screen.getAllByRole('button',{name:'生成 XML 并预览'}).find(item=>!handoff.contains(item))!
    fireEvent.click(button)
    fireEvent.click(footerButton)
    expect(apiMock.prepareExecution).toHaveBeenCalledTimes(1)
    await screen.findByRole('button',{name:'查看 XML 预览'})
    expect(apiMock.confirmExecution).not.toHaveBeenCalled()
  })

  it('shows missing file detail and retries from the accepted card',async()=>{
    apiMock.validateExperiment.mockResolvedValueOnce({...ready2,ok:false,missing:[{kind:'observation',year:2024,doy:350,station:'S000',path:'F:/input/missing.obs',state:'missing'}]}).mockResolvedValueOnce(ready2)
    await acceptRecovered()
    const handoff=await screen.findByRole('region',{name:'输入检查交接'})
    expect(await within(handoff).findByText(/F:\/input\/missing.obs/)).toBeInTheDocument()
    expect(handoff).toHaveTextContent('2024')
    expect(handoff).toHaveTextContent('observation')
    expect(handoff).toHaveTextContent('S000')
    expect(handoff).toHaveTextContent('missing')
    fireEvent.click(within(handoff).getByRole('button',{name:'重新检查输入'}))
    await within(handoff).findByText('输入检查通过')
    expect(apiMock.validateExperiment).toHaveBeenCalledTimes(2)
  })

  it('keeps acceptance after a check network failure and allows retry',async()=>{
    apiMock.validateExperiment.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(ready2)
    await acceptRecovered()
    const handoff=await screen.findByRole('region',{name:'输入检查交接'})
    await within(handoff).findByText(/输入检查未完成/)
    expect(screen.getByText('已接受修改，尚未授权执行。')).toBeInTheDocument()
    expect(screen.queryByText(/接受修改失败/)).not.toBeInTheDocument()
    fireEvent.click(within(handoff).getByRole('button',{name:'重试检查'}))
    await within(handoff).findByText('输入检查通过')
    expect(apiMock.acceptRevision).toHaveBeenCalledTimes(1)
  })

  it('rejects successful check results for a different revision identity',async()=>{
    apiMock.validateExperiment.mockResolvedValue({...ready2,revision_id:'other'})
    await acceptRecovered()
    const handoff=await screen.findByRole('region',{name:'输入检查交接'})
    await within(handoff).findByText(/检查已失效/)
    expect(screen.getByRole('button',{name:'生成 XML 并预览'})).toBeDisabled()
  })

  it.each(['unmount','experiment switch'])('ignores late automatic checking after %s',async mode=>{
    let finish!:(value:typeof ready2)=>void
    apiMock.validateExperiment.mockImplementationOnce(()=>new Promise(done=>{finish=done}))
    const page=await acceptRecovered()
    await waitFor(()=>expect(apiMock.validateExperiment).toHaveBeenCalledTimes(1))
    if(mode==='unmount')page.unmount()
    else {
      apiMock.getExperiment.mockResolvedValue({...experiment,id:'exp-2'})
      page.rerender(<ExperimentWorkspace experimentId="exp-2" />)
      await waitFor(()=>expect(apiMock.getExperiment).toHaveBeenCalledWith('exp-2'))
      await waitFor(()=>expect(screen.getByRole('button',{name:'检查输入'})).toBeEnabled())
    }
    await act(async()=>{finish(ready2)})
    expect(screen.queryByText('输入检查通过')).not.toBeInTheDocument()
    if(mode==='experiment switch')expect(screen.getByRole('button',{name:'生成 XML 并预览'})).toBeDisabled()
  })

  it('invalidates passing check when a new draft is offered',async()=>{
    apiMock.validateExperiment.mockResolvedValue(ready2)
    await acceptRecovered()
    await screen.findByText('输入检查通过')
    fireEvent.click(screen.getByRole('button',{name:'手动调整参数'}))
    expect(screen.queryByText('输入检查通过')).not.toBeInTheDocument()
    expect(screen.getByRole('button',{name:'生成 XML 并预览'})).toBeDisabled()
  })

  it('reloads authoritative frozen status after XML preparation fails',async()=>{
    apiMock.validateExperiment.mockResolvedValue(ready2)
    apiMock.prepareExecution.mockRejectedValueOnce(new Error('snapshot failed'))
    await acceptRecovered()
    apiMock.getExperiment.mockResolvedValue({...experiment,status:'ready',current_revision:2,current_revision_id:'revision-2'})
    const handoff=await screen.findByRole('region',{name:'输入检查交接'})
    await within(handoff).findByText('输入检查通过')
    fireEvent.click(within(handoff).getByRole('button',{name:'生成 XML 并预览'}))
    await waitFor(()=>expect(apiMock.getExperiment).toHaveBeenCalledTimes(2))
    await waitFor(()=>expect(screen.getByRole('textbox',{name:/message/i})).toBeEnabled())
    expect(screen.getByText('已接受修改，尚未授权执行。')).toBeInTheDocument()
    expect(screen.getByRole('button',{name:'生成 XML 并预览'})).toBeDisabled()
  })

  it('shares the synchronous check lock across repeated footer actions',async()=>{
    apiMock.validateExperiment.mockImplementation(()=>new Promise(()=>{}))
    render(<ExperimentWorkspace experimentId="exp-1" />)
    const button=await screen.findByRole('button',{name:'检查输入'})
    await waitFor(()=>expect(button).toBeEnabled())
    fireEvent.click(button);fireEvent.click(button)
    expect(apiMock.validateExperiment).toHaveBeenCalledExactlyOnceWith('exp-1',{expected_revision_id:'revision-1',expected_revision:1})
  })

  afterEach(() => vi.useRealTimers())
  beforeEach(() => {
    vi.resetAllMocks()
    apiMock.getExperiment.mockResolvedValue(experiment)
    apiMock.ensureConversation.mockResolvedValue({id:'conversation-1',experiment_id:'exp-1',title:'对话',messages:[]})
    apiMock.sendConversationMessage.mockImplementation(async(_id,message,requestId)=>{
      const result=turn(chatProposal)
      return {...result,user_message:{...result.user_message,request_id:requestId,blocks:[{type:'text',text:message}]},assistant_message:{...result.assistant_message,request_id:requestId}}
    })
    apiMock.previewProposal.mockResolvedValue(chatProposal)
    apiMock.acceptRevision.mockResolvedValue({
      id: 'revision-2',
      number: 2,
      spec: chatProposal.candidate,
      is_frozen: false,
    })
    apiMock.validateExperiment.mockResolvedValue({
      revision_id:'revision-1', revision:1, ok: false,
      missing: [{ kind: 'observation', station: 'S000', year: 2024, doy: 350 }],
    })
    apiMock.freezeExperiment.mockResolvedValue({ is_frozen: true })
    apiMock.listTasks.mockResolvedValue([])
    apiMock.getExperimentMonitor.mockImplementation(id => legacyMonitorSnapshot(id,apiMock.listTasks))
    apiMock.openExperimentEvents.mockReturnValue(()=>{})
    apiMock.listProposals.mockResolvedValue([])
    apiMock.rejectProposal.mockResolvedValue({ ...chatProposal, status: 'rejected' })
  })

  it('previews chat changes before accepting a revision', async () => {
    const user = userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)

    await screen.findByText('Revision 1')
    await user.type(screen.getByRole('textbox', { name: /message/i }), '改成三频')
    await user.click(screen.getByRole('button', { name: /发送/i }))

    expect(await screen.findByText('frequency')).toBeInTheDocument()
    expect(screen.getByText('DF → TF')).toBeInTheDocument()
    expect(apiMock.acceptRevision).not.toHaveBeenCalled()
  })

  it('submits card edits through the same proposal flow', async () => {
    const user = userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)

    await user.click(await screen.findByRole('button', { name: '手动调整参数' }))
    await selectOption(user, '频率', '三频 TF')
    await user.click(screen.getByRole('button', { name: /预览修改/i }))

    expect(apiMock.previewProposal).toHaveBeenCalledWith(
      'exp-1',
      expect.objectContaining({
        source: 'card',
        base_revision_id: 'revision-1',
        candidate: expect.objectContaining({
          scenario: expect.objectContaining({ frequency: 'TF' }),
        }),
      }),
    )
  })

  it('keeps freeze disabled while validation fails', async () => {
    const user = userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)

    await user.click(await screen.findByRole('button', { name: /检查输入/i }))

    expect(await screen.findByText(/^缺少.*S000/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /生成 XML 并预览/i })).toBeDisabled()
  })

  it('keeps a cleared numeric field invalid until an explicit valid value is entered', async () => {
    const user = userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await user.click(await screen.findByRole('button', { name: '手动调整参数' }))
    const concurrency = screen.getByRole('spinbutton', { name: '并发数' })
    await user.clear(concurrency)
    expect(concurrency).toHaveValue('')
    expect(screen.getByRole('button', { name: '接受修改' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '预览修改' })).toBeDisabled()
    expect(apiMock.previewProposal).not.toHaveBeenCalled()
    await user.type(concurrency, '2')
    expect(screen.getByRole('button', { name: '接受修改' })).toBeEnabled()
  })

  it('never exposes an API key in browser configuration', () => {
    const serialized = JSON.stringify(browserConfig).toLowerCase()

    expect(serialized).not.toContain('api_key')
    expect(serialized).not.toContain('apikey')
    expect(serialized).not.toContain('secret')
  })

  it('prepares XML only after validation and never confirms execution itself', async () => {
    const user = userEvent.setup(), onPrepared = vi.fn()
    const { executionPreview } = await import('./executionFixture')
    apiMock.validateExperiment.mockResolvedValue({ revision_id:'revision-1', revision:1, ok: true, missing: [] })
    apiMock.prepareExecution.mockResolvedValue(executionPreview)
    apiMock.getExecutionPreview.mockResolvedValue(executionPreview)
    render(<ExperimentWorkspace experimentId="exp-1" onPrepared={onPrepared} />)
    const prepare = await screen.findByRole('button', { name: '生成 XML 并预览' })
    expect(prepare).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '检查输入' }))
    await user.click(prepare)
    expect(apiMock.prepareExecution).toHaveBeenCalledWith('exp-1', { expected_revision: 1 })
    expect(onPrepared).toHaveBeenCalledWith(executionPreview)
    expect(apiMock.confirmExecution).not.toHaveBeenCalled()
  })

  it('blocks input validation and preparation for unsaved card edits', async () => {
    const user = userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await user.click(await screen.findByRole('button', { name: '手动调整参数' }))
    await selectOption(user, '频率', '三频 TF')
    expect(screen.getByRole('button', { name: '检查输入' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '生成 XML 并预览' })).toBeDisabled()
  })

  it.each(['unmount', 'view change'])('does not navigate after preparation completes following %s', async (navigation) => {
    const { executionPreview } = await import('./executionFixture')
    let finish!: (value: typeof executionPreview) => void
    apiMock.validateExperiment.mockResolvedValue({ revision_id:'revision-1', revision:1, ok: true, missing: [] })
    apiMock.prepareExecution.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const user = userEvent.setup(), onPrepared = vi.fn()
    const page = render(<ExperimentWorkspace experimentId="exp-1" onPrepared={onPrepared} />)
    await user.click(await screen.findByRole('button', { name:'检查输入' }))
    await user.click(screen.getByRole('button', { name:'生成 XML 并预览' }))
    if (navigation === 'unmount') page.unmount()
    else await user.click(screen.getByRole('tab', { name:'图片预览' }))
    await act(async () => { finish(executionPreview) })
    expect(onPrepared).not.toHaveBeenCalled()
    expect(apiMock.confirmExecution).not.toHaveBeenCalled()
    if (navigation === 'view change') expect(screen.getByRole('tab', { name:'图片预览' })).toHaveAttribute('aria-selected', 'true')
  })

  it('replaces the loading state with an actionable load error', async () => {
    apiMock.getExperiment.mockRejectedValueOnce(new Error('offline'))

    render(<ExperimentWorkspace experimentId="exp-1" />)

    expect(await screen.findByRole('alert')).toHaveTextContent('确认本地服务正在运行')
    expect(screen.queryByText('正在读取实验计划…')).not.toBeInTheDocument()
  })

  it('reloads the latest plan after a stale revision conflict', async () => {
    const user = userEvent.setup()
    apiMock.acceptRevision.mockRejectedValueOnce(
      Object.assign(new Error('revision changed'), { code: 'revision_conflict' }),
    )
    apiMock.getExperiment
      .mockResolvedValueOnce(experiment)
      .mockResolvedValueOnce({ ...experiment, current_revision: 2 })
    render(<ExperimentWorkspace experimentId="exp-1" />)

    await user.type(await screen.findByRole('textbox', { name: /message/i }), '改成三频')
    await user.click(screen.getByRole('button', { name: /发送/i }))
    await user.click(await screen.findByRole('button', { name: /接受修改/i }))

    expect(await screen.findByText(/计划已被更新/)).toBeInTheDocument()
    await waitFor(() => expect(apiMock.getExperiment).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('Revision 2')).toBeInTheDocument()
  })

  it('shows increasing elapsed time and enables cancelling only model generation', async () => {
    apiMock.sendConversationMessage.mockImplementationOnce(() => new Promise(() => {}))
    render(<ExperimentWorkspace experimentId="exp-1" />)
    const input = await screen.findByRole('textbox', { name: /message/i })
    vi.useFakeTimers()
    fireEvent.change(input, { target: { value: '改成三频' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await act(async () => { vi.advanceTimersByTime(3000) })
    expect(screen.getByRole('status')).toHaveTextContent('已等待 3 秒')
    expect(screen.getByRole('button', { name: '取消回复' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '检查输入' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '取消回复' }))
    expect(screen.getByRole('status')).toHaveTextContent('已取消')
    expect(screen.getByRole('button', { name: '检查输入' })).toBeDisabled()
    await act(async () => {})
    expect(screen.getByRole('button', { name: '检查输入' })).toBeEnabled()
    expect(apiMock.acceptRevision).not.toHaveBeenCalled()
  })

  it('cancels the signal and ignores an old response after immediately resending', async () => {
    let firstResolve!: (value: ReturnType<typeof turn>) => void
    let secondResolve!: (value: ReturnType<typeof turn>) => void
    apiMock.sendConversationMessage
      .mockImplementationOnce(() => new Promise((resolve) => { firstResolve = resolve }))
      .mockImplementationOnce(() => new Promise((resolve) => { secondResolve = resolve }))
    const user = userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    const input = await screen.findByRole('textbox', { name: /message/i })
    await user.type(input, '旧请求')
    await user.click(screen.getByRole('button', { name: '发送' }))
    const signal = apiMock.sendConversationMessage.mock.calls[0][3] as AbortSignal
    await user.click(screen.getByRole('button', { name: '取消回复' }))
    expect(signal.aborted).toBe(true)
    await user.type(input, '新请求')
    await user.click(screen.getByRole('button', { name: '发送' }))
    await act(async () => { firstResolve(turn({ ...chatProposal, explanation: '不应显示的旧回复' })) })
    expect(screen.queryByText('不应显示的旧回复')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '取消回复' })).toBeEnabled()
    await act(async () => { secondResolve(turn({ ...chatProposal, explanation: '当前请求已完成' })) })
    expect(screen.getAllByText('当前请求已完成').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: '取消回复' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '接受修改' })).toBeEnabled()
  })

  it('aborts a generating request on navigation away', async () => {
    apiMock.sendConversationMessage.mockImplementationOnce(() => new Promise(() => {}))
    const user = userEvent.setup()
    const view = render(<ExperimentWorkspace experimentId="exp-1" />)
    await user.type(await screen.findByRole('textbox', { name: /message/i }), '改成三频')
    await user.click(screen.getByRole('button', { name: '发送' }))
    const signal = apiMock.sendConversationMessage.mock.calls[0][3] as AbortSignal
    view.unmount()
    expect(signal.aborted).toBe(true)
  })

  it('shows completed stage timings and a correlation ID without accepting the proposal', async () => {
    apiMock.sendConversationMessage.mockResolvedValueOnce(turn({ ...chatProposal, request_id: 'request-safe-id', timings: {
      plan_load_ms: 10, model_request_ms: 1250, validation_ms: 20, total_ms: 1280, api_total_ms: 1300,
    } }))
    const user = userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await user.type(await screen.findByRole('textbox', { name: /message/i }), '改成三频')
    await user.click(screen.getByRole('button', { name: '发送' }))
    expect(await screen.findByText('模型请求（含网络与生成）')).toBeInTheDocument()
    expect(screen.getByText('1.250 秒')).toBeInTheDocument()
    expect(screen.getByText(/request-safe-id/)).toBeInTheDocument()
    expect(apiMock.acceptRevision).not.toHaveBeenCalled()
  })

  it('leaves controls usable after model failure instead of reporting a cancellation', async () => {
    apiMock.sendConversationMessage.mockRejectedValueOnce(new Error('provider unavailable'))
    const user = userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await user.type(await screen.findByRole('textbox', { name: /message/i }), '改成三频')
    await user.click(screen.getByRole('button', { name: '发送' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('模型提案失败')
    expect(screen.getByRole('status')).toHaveTextContent('回复失败')
    expect(screen.getByRole('button', { name: '检查输入' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: '取消回复' })).not.toBeInTheDocument()
  })

  it('keeps a modification card inside the conversation and retains it while center views change', async () => {
    const user = userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await user.type(await screen.findByRole('textbox', { name: /message/i }), '改成三频')
    await user.click(screen.getByRole('button', { name: '发送' }))
    const chat = screen.getByRole('complementary', { name: '模型对话区' })
    expect(await within(chat).findByRole('button', { name: '接受修改' })).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'XML 预览' }))
    expect(within(chat).getByRole('button', { name: '接受修改' })).toBeInTheDocument()
    expect(within(chat).getByText('改成三频')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: '参数解析' }))
    expect(apiMock.getExperiment).toHaveBeenCalledTimes(1)
    expect(apiMock.acceptRevision).not.toHaveBeenCalled()
  })

  it('keeps the center workspace fixed and scrolls only the active tab panel', async () => {
    render(<ExperimentWorkspace experimentId="exp-1" />)

    await screen.findByText('Revision 1')
    const center = screen.getByRole('region', { name: '实验内容区' })
    const tabPanel = screen.getByRole('tabpanel')

    expect(center).toHaveStyle({ overflow: 'hidden' })
    expect(tabPanel).toHaveStyle({ overflowX: 'hidden', overflowY: 'auto' })
  })

  it('revalidates edits to a model card before accepting its new server proposal ID', async () => {
    apiMock.previewProposal.mockResolvedValueOnce({ ...chatProposal, id:'edited-card-proposal' })
    const user = userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await user.type(await screen.findByRole('textbox', { name: /message/i }), '改成三频')
    await user.click(screen.getByRole('button', { name: '发送' }))
    await selectOption(user, '模糊度', '固定解')
    await user.click(screen.getByRole('button', { name:'接受修改' }))
    await waitFor(() => expect(apiMock.acceptRevision).toHaveBeenCalledWith('exp-1', { proposal_id:'edited-card-proposal', expected_revision:1 }))
    expect(apiMock.previewProposal).toHaveBeenCalledWith('exp-1', expect.objectContaining({ source:'card', base_revision_id:'revision-1', source_proposal_id:'proposal-chat', candidate:expect.objectContaining({ scenario:expect.objectContaining({ frequency:'TF',ambiguity:'fixed' }) }) }))
    expect(apiMock.confirmExecution).not.toHaveBeenCalled()
  })

  it('does not accept or execute when an edited candidate fails server validation', async () => {
    apiMock.previewProposal.mockRejectedValueOnce(new Error('invalid date'))
    const user = userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await user.click(await screen.findByRole('button', { name:'手动调整参数' }))
    await selectOption(user, '频率', '三频 TF')
    await user.click(screen.getByRole('button', { name:'接受修改' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('校验')
    expect(apiMock.acceptRevision).not.toHaveBeenCalled()
    expect(apiMock.confirmExecution).not.toHaveBeenCalled()
  })

  it('preserves all date segments, excluded dates and input profiles when editing an inline model card', async () => {
    apiMock.sendConversationMessage.mockResolvedValueOnce(turn({ ...chatProposal, candidate: { ...chatProposal.candidate,
      date_segments:[{year:2024,start_doy:350,end_doy:360},{year:2025,start_doy:1,end_doy:2}],
      excluded_dates:[{year:2024,doy:355}],input_profile_id:'retained-input',outputs:['flt','log'],
    } }))
    const user=userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await user.type(await screen.findByRole('textbox',{name:/message/i}),'调整组合')
    await user.click(screen.getByRole('button',{name:'发送'}))
    await selectOption(user, '频率', '双频 DF')
    await user.click(screen.getByRole('button',{name:'接受修改'}))
    expect(apiMock.previewProposal).toHaveBeenCalledWith('exp-1',expect.objectContaining({candidate:expect.objectContaining({
      date_segments:[{year:2024,start_doy:350,end_doy:360},{year:2025,start_doy:1,end_doy:2}],
      excluded_dates:[{year:2024,doy:355}],input_profile_id:'retained-input',outputs:['flt','log'],
    })}))
  })

  it('restores the newest persisted pending proposal after a refresh', async () => {
    apiMock.listProposals.mockResolvedValueOnce([chatProposal, { ...chatProposal, id:'older', explanation:'旧提案' }])
    const user=userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    expect(await selectedOption('频率')).toHaveTextContent('三频 TF')
    expect(apiMock.listProposals).toHaveBeenCalledWith('exp-1', 'pending')
    expect(screen.queryByText('旧提案')).not.toBeInTheDocument()
    expect(screen.getByRole('button',{name:'检查输入'})).toBeDisabled()
    await user.click(screen.getByRole('button',{name:'接受修改'}))
    await waitFor(()=>expect(apiMock.acceptRevision).toHaveBeenCalledWith('exp-1',{proposal_id:'proposal-chat',expected_revision:1}))
  })

  it('retains a persisted proposal original baseline when the current plan has advanced', async () => {
    apiMock.getExperiment.mockResolvedValue({ ...experiment, current_revision:2,current_revision_id:'revision-2' })
    apiMock.listProposals.mockResolvedValue([chatProposal])
    apiMock.acceptRevision.mockRejectedValueOnce({code:'revision_conflict'})
    const user=userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await selectOption(user, '模糊度', '固定解')
    await user.click(screen.getByRole('button',{name:'预览修改'}))
    expect(apiMock.previewProposal).toHaveBeenCalledWith('exp-1',expect.objectContaining({base_revision_id:'revision-1',source_proposal_id:'proposal-chat'}))
    await user.click(screen.getByRole('button',{name:'接受修改'}))
    await waitFor(()=>expect(apiMock.acceptRevision).toHaveBeenCalledWith('exp-1',{proposal_id:'proposal-chat',expected_revision:1}))
    expect(await screen.findByText('Revision 2')).toBeInTheDocument()
    expect(screen.getByText(/计划已被更新/)).toBeInTheDocument()
  })

  it('uses the server proposal baseline for acceptance even when chat returns an older revision', async () => {
    apiMock.getExperiment.mockResolvedValue({ ...experiment,current_revision:2,current_revision_id:'revision-2' })
    const user=userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await user.type(await screen.findByRole('textbox',{name:/message/i}),'旧版本提案')
    await user.click(screen.getByRole('button',{name:'发送'}))
    await user.click(await screen.findByRole('button',{name:'接受修改'}))
    expect(apiMock.acceptRevision).toHaveBeenCalledWith('exp-1',{proposal_id:'proposal-chat',expected_revision:1})
  })

  it('does not let a delayed recovery replace a newly offered manual draft', async () => {
    let resolve!: (value: typeof chatProposal[])=>void
    apiMock.listProposals.mockImplementationOnce(()=>new Promise(done=>{resolve=done}))
    const user=userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await user.click(await screen.findByRole('button',{name:'手动调整参数'}))
    await selectOption(user, '频率', '三频 TF')
    await act(async()=>{resolve([{...chatProposal,explanation:'迟到恢复'}])})
    expect(screen.queryByText('迟到恢复')).not.toBeInTheDocument()
    expect(await selectedOption('频率')).toHaveTextContent('三频 TF')
    await user.click(screen.getByRole('button',{name:'预览修改'}))
    expect(apiMock.previewProposal).toHaveBeenCalledWith('exp-1',expect.objectContaining({base_revision_id:'revision-1'}))
    expect(apiMock.previewProposal.mock.calls[0][1]).not.toHaveProperty('source_proposal_id')
  })

  it('persists rejection and keeps a proposal pending when persistence fails', async () => {
    apiMock.listProposals.mockResolvedValue([chatProposal])
    apiMock.rejectProposal.mockRejectedValueOnce(new Error('offline'))
    const user=userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await user.click(await screen.findByRole('button',{name:'放弃提案'}))
    expect(await screen.findByRole('alert')).toHaveTextContent('放弃')
    expect(screen.getByRole('button',{name:'接受修改'})).toBeEnabled()
    expect(screen.getByRole('button',{name:'检查输入'})).toBeDisabled()
    expect(screen.queryByText('已放弃这份提案，计划未改变。')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button',{name:'放弃提案'}))
    expect(await screen.findByText('已放弃这份提案，计划未改变。')).toBeInTheDocument()
    expect(apiMock.rejectProposal).toHaveBeenCalledWith('exp-1','proposal-chat')
    expect(screen.getByRole('button',{name:'检查输入'})).toBeEnabled()
  })

  it('discards an unsaved manual draft locally without registering or rejecting a server proposal', async () => {
    const user=userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await user.click(await screen.findByRole('button',{name:'手动调整参数'}))
    await user.click(screen.getByRole('button',{name:'放弃提案'}))
    expect(screen.getByText('已放弃这份提案，计划未改变。')).toBeInTheDocument()
    expect(apiMock.rejectProposal).not.toHaveBeenCalled()
    expect(apiMock.previewProposal).not.toHaveBeenCalled()
  })

  it('binds the next manual draft to the revision UUID returned by acceptance', async () => {
    const user=userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await user.type(await screen.findByRole('textbox',{name:/message/i}),'改成三频')
    await user.click(screen.getByRole('button',{name:'发送'}))
    await user.click(await screen.findByRole('button',{name:'接受修改'}))
    await screen.findByText('Revision 2')
    await user.click(screen.getByRole('button',{name:'手动调整参数'}))
    await user.click(screen.getByRole('button',{name:'预览修改'}))
    expect(apiMock.previewProposal).toHaveBeenCalledWith('exp-1',expect.objectContaining({base_revision_id:'revision-2'}))
  })

  it('keeps session drafts instead of replacing them with server recovery', async () => {
    const { executionPreview }=await import('./executionFixture')
    render(<ExperimentWorkspace experimentId="exp-1" session={{revision:1,pendingId:'local',messages:[{role:'assistant',text:'未保存的卡片',cardId:'local'}],cards:{local:{candidate:executionPreview.spec,proposal:null,status:'pending',edited:true,base_revision_id:'revision-1',base_revision_number:1}}}} />)
    expect(await screen.findByLabelText('起始 DOY')).toHaveValue('339')
    expect(apiMock.listProposals).not.toHaveBeenCalled()
    expect(screen.getByRole('button',{name:'检查输入'})).toBeDisabled()
  })

  it('ignores a previous experiment recovery after switching workspaces', async () => {
    let resolve!: (value: typeof chatProposal[])=>void
    apiMock.listProposals.mockImplementationOnce(()=>new Promise(done=>{resolve=done})).mockResolvedValueOnce([])
    const page=render(<ExperimentWorkspace key="exp-1" experimentId="exp-1" />)
    await screen.findByText('Revision 1')
    await waitFor(()=>expect(apiMock.listProposals).toHaveBeenCalledTimes(1))
    page.rerender(<ExperimentWorkspace key="exp-2" experimentId="exp-2" />)
    await waitFor(()=>expect(apiMock.listProposals).toHaveBeenCalledWith('exp-2','pending'))
    await act(async()=>{resolve([{...chatProposal,explanation:'其他实验的旧提案'}])})
    expect(screen.queryByText('其他实验的旧提案')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('频率')).not.toBeInTheDocument()
    expect(screen.getByRole('button',{name:'检查输入'})).toBeEnabled()
  })

  it('keeps execution gates closed if persistent proposal recovery fails', async () => {
    apiMock.listProposals.mockRejectedValueOnce(new Error('offline'))
    render(<ExperimentWorkspace experimentId="exp-1" />)
    expect(await screen.findByRole('alert')).toHaveTextContent('无法恢复')
    expect(screen.getByRole('button',{name:'检查输入'})).toBeDisabled()
    expect(screen.getByRole('button',{name:'生成 XML 并预览'})).toBeDisabled()
    expect(screen.getByRole('button',{name:'手动调整参数'})).toBeEnabled()
  })

  it.each(['failure','cancellation'])('recovers server pending after chat %s interrupts initial recovery', async outcome => {
    let recoverInitial!: (value:typeof chatProposal[])=>void
    let recoverAgain!: (value:typeof chatProposal[])=>void
    let failChat!: (error:Error)=>void
    apiMock.listProposals.mockImplementationOnce(()=>new Promise(done=>{recoverInitial=done}))
      .mockImplementationOnce(()=>new Promise(done=>{recoverAgain=done}))
    apiMock.sendConversationMessage.mockImplementationOnce(()=>new Promise((_done,reject)=>{failChat=reject}))
    const user=userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await user.type(await screen.findByRole('textbox',{name:/message/i}),'尝试修改')
    await user.click(screen.getByRole('button',{name:'发送'}))
    if(outcome==='failure')await act(async()=>{failChat(new Error('offline'))})
    else await user.click(screen.getByRole('button',{name:'取消回复'}))
    expect(screen.getByRole('button',{name:'检查输入'})).toBeDisabled()
    expect(screen.getByRole('button',{name:'生成 XML 并预览'})).toBeDisabled()
    await waitFor(()=>expect(apiMock.listProposals).toHaveBeenCalledTimes(2))
    await act(async()=>{recoverInitial([])})
    expect(screen.getByRole('button',{name:'检查输入'})).toBeDisabled()
    await act(async()=>{recoverAgain([{...chatProposal,explanation:'重新恢复的待确认修改'}])})
    expect(await selectedOption('频率')).toHaveTextContent('三频 TF')
    expect(screen.getByText('重新恢复的待确认修改')).toBeInTheDocument()
    expect(screen.getByRole('button',{name:'检查输入'})).toBeDisabled()
    expect(apiMock.validateExperiment).not.toHaveBeenCalled()
  })

  it('restores persisted pending after abandoning a local replacement draft', async () => {
    let recoverAgain!: (value:typeof chatProposal[])=>void
    apiMock.listProposals.mockResolvedValueOnce([chatProposal])
      .mockImplementationOnce(()=>new Promise(done=>{recoverAgain=done}))
    const user=userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await screen.findByLabelText('频率')
    await user.click(screen.getByRole('button',{name:'手动调整参数'}))
    await selectOption(user, '频率', '双频 DF')
    await user.click(screen.getByRole('button',{name:'放弃提案'}))
    expect(screen.getByRole('button',{name:'检查输入'})).toBeDisabled()
    await waitFor(()=>expect(apiMock.listProposals).toHaveBeenCalledTimes(2))
    await act(async()=>{recoverAgain([chatProposal])})
    expect(await selectedOption('频率')).toHaveTextContent('三频 TF')
    expect(screen.getByRole('button',{name:'检查输入'})).toBeDisabled()
    expect(apiMock.rejectProposal).not.toHaveBeenCalled()
  })

  it('recovers current server pending when a stale session draft is invalidated', async () => {
    const { executionPreview }=await import('./executionFixture')
    let recover!: (value:typeof chatProposal[])=>void
    apiMock.getExperiment.mockResolvedValue({...experiment,current_revision:2,current_revision_id:'revision-2'})
    apiMock.listProposals.mockImplementationOnce(()=>new Promise(done=>{recover=done}))
    render(<ExperimentWorkspace experimentId="exp-1" session={{revision:1,pendingId:'old',messages:[{role:'assistant',text:'旧版本本地草稿',cardId:'old'}],cards:{old:{candidate:executionPreview.spec,proposal:null,status:'pending',edited:true,base_revision_id:'revision-1',base_revision_number:1}}}} />)
    await screen.findByText('Revision 2')
    expect(screen.getByRole('button',{name:'检查输入'})).toBeDisabled()
    await waitFor(()=>expect(apiMock.listProposals).toHaveBeenCalledWith('exp-1','pending'))
    await act(async()=>{recover([{...chatProposal,base_revision_id:'revision-2',base_revision_number:2}])})
    expect(await selectedOption('频率')).toHaveTextContent('三频 TF')
    expect(screen.getByRole('button',{name:'检查输入'})).toBeDisabled()
  })

  it('recovers an unseen server pending when execution preparation reports pending_proposal', async () => {
    apiMock.listProposals.mockResolvedValueOnce([]).mockResolvedValueOnce([chatProposal])
    apiMock.validateExperiment.mockResolvedValueOnce({revision_id:'revision-1',revision:1,ok:true,missing:[]})
    apiMock.prepareExecution.mockRejectedValueOnce({code:'pending_proposal'})
    const user=userEvent.setup()
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await user.click(await screen.findByRole('button',{name:'检查输入'}))
    await user.click(screen.getByRole('button',{name:'生成 XML 并预览'}))
    expect(await selectedOption('频率')).toHaveTextContent('三频 TF')
    expect(screen.getByRole('button',{name:'检查输入'})).toBeDisabled()
    expect(screen.getByRole('button',{name:'生成 XML 并预览'})).toBeDisabled()
    expect(apiMock.confirmExecution).not.toHaveBeenCalled()
  })
})
