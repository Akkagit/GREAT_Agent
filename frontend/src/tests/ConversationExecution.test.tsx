import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type { ConversationTurn, TaskSummary } from '../api/types'
import { ExperimentWorkspace } from '../pages/ExperimentWorkspace'
import { executionPreview } from './executionFixture'
import { legacyMonitorSnapshot, monitorCursor } from './monitorFixture'

const apiMock=vi.hoisted(()=>({
  getExperiment:vi.fn(),listTasks:vi.fn(),listProposals:vi.fn(),
  ensureConversation:vi.fn(),sendConversationMessage:vi.fn(),
  getExecutionPreview:vi.fn(),confirmExecution:vi.fn(),openTaskEvents:vi.fn(),
  validateExperiment:vi.fn(),prepareExecution:vi.fn(),
  getExperimentMonitor:vi.fn(),openExperimentEvents:vi.fn(),
}))
vi.mock('../api/client',async importOriginal=>({...await importOriginal<typeof import('../api/client')>(),api:apiMock}))

const experiment={id:'exp-1',status:'ready',current_revision:3,current_revision_id:'revision-3',spec:executionPreview.spec}
const task:TaskSummary={id:'task-1',experiment_id:'exp-1',year:2024,doy:339,status:'pending'}

function reply(execution=false,scope={experiment_id:'exp-1',preview_id:'preview-1'}):ConversationTurn{
  return {
    conversation_id:'conversation-1',run_id:'run-1',request_id:'request-1',status:'completed',timings:{},
    user_message:{id:'user-1',role:'user',status:'completed',blocks:[{type:'text',text:'xml我看了没啥问题，现在你可以执行了'}]},
    assistant_message:{id:'assistant-1',role:'assistant',status:'completed',blocks:[
      {type:'text',text:execution?'已确认执行当前 XML。':'请继续核对 XML。'},
      ...(execution?[{type:'execution' as const,...scope,task_ids:['task-1']}]:[]),
    ]},
  }
}

async function openReview(props:Partial<React.ComponentProps<typeof ExperimentWorkspace>>={}){
  const page=render(<ExperimentWorkspace experimentId="exp-1" previewId="preview-1" {...props} />)
  await screen.findByText('Revision 3')
  fireEvent.click(screen.getByRole('tab',{name:'XML 预览'}))
  await screen.findByRole('button',{name:'确认并开始解算'})
  return page
}

function send(message='xml我看了没啥问题，现在你可以执行了'){
  fireEvent.change(screen.getByRole('textbox',{name:/message/i}),{target:{value:message}})
  fireEvent.click(screen.getByRole('button',{name:'发送'}))
}

describe('experiment chat execution',()=>{
  beforeEach(()=>{
    vi.resetAllMocks()
    apiMock.getExperiment.mockResolvedValue(experiment)
    apiMock.listTasks.mockResolvedValue([])
    apiMock.getExperimentMonitor.mockImplementation(id => legacyMonitorSnapshot(id,apiMock.listTasks))
    apiMock.openExperimentEvents.mockReturnValue(()=>{})
    apiMock.listProposals.mockResolvedValue([])
    apiMock.ensureConversation.mockResolvedValue({id:'conversation-1',experiment_id:'exp-1',title:'对话',messages:[]})
    apiMock.getExecutionPreview.mockResolvedValue(executionPreview)
    apiMock.openTaskEvents.mockReturnValue(()=>{})
    apiMock.sendConversationMessage.mockResolvedValue(reply())
  })

  it('sends the visible review preview without interpreting the message or executing locally',async()=>{
    const onRun=vi.fn()
    await openReview({onRun})
    send('解释这个 XML')
    await screen.findByText('请继续核对 XML。')
    expect(apiMock.sendConversationMessage).toHaveBeenCalledWith('conversation-1','解释这个 XML',expect.any(String),expect.any(AbortSignal),'preview-1')
    expect(screen.getByRole('tab',{name:'XML 预览'})).toHaveAttribute('aria-selected','true')
    expect(apiMock.confirmExecution).not.toHaveBeenCalled()
    expect(onRun).not.toHaveBeenCalled()
  })

  it('enters monitoring and refreshes tasks only after a current server execution result',async()=>{
    let finish!:(value:ConversationTurn)=>void
    apiMock.sendConversationMessage.mockImplementationOnce(()=>new Promise(done=>{finish=done}))
    const onRun=vi.fn(),onChanged=vi.fn()
    await openReview({onRun,onChanged})
    send()
    await waitFor(()=>expect(apiMock.sendConversationMessage).toHaveBeenCalledOnce())
    expect(onRun).not.toHaveBeenCalled()
    expect(apiMock.confirmExecution).not.toHaveBeenCalled()
    apiMock.listTasks.mockResolvedValue([task])
    await act(async()=>finish(reply(true)))
    await screen.findByRole('heading',{name:'运行仪表盘'})
    expect(onRun).toHaveBeenCalledOnce()
    expect(onChanged).toHaveBeenCalledOnce()
    expect(apiMock.getExperiment).toHaveBeenCalledTimes(2)
    expect(apiMock.listTasks.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(apiMock.openExperimentEvents).toHaveBeenCalledWith('exp-1',monitorCursor('exp-1'),expect.any(Function),expect.any(Function),expect.any(AbortSignal),expect.any(Function))
    expect(apiMock.openTaskEvents).not.toHaveBeenCalled()
    expect(screen.getByRole('tab',{name:'实验进度'})).toHaveAttribute('aria-selected','true')
  })

  it.each(['参数解析','图片预览','实验进度'])('omits preview execution context outside review in %s',async tab=>{
    render(<ExperimentWorkspace experimentId="exp-1" previewId="preview-1" />)
    await screen.findByText('Revision 3')
    fireEvent.click(screen.getByRole('tab',{name:tab}))
    send()
    await screen.findByText('请继续核对 XML。')
    expect(apiMock.sendConversationMessage.mock.calls[0][4]).toBeUndefined()
  })

  it('omits preview execution context when review has no preview',async()=>{
    render(<ExperimentWorkspace experimentId="exp-1" view="review" />)
    await screen.findByText('尚未生成执行 XML')
    send()
    await screen.findByText('请继续核对 XML。')
    expect(apiMock.sendConversationMessage.mock.calls[0][4]).toBeUndefined()
  })

  it('restores historical execution messages without replaying execution or navigation',async()=>{
    const old=reply(true),onRun=vi.fn(),onChanged=vi.fn()
    apiMock.ensureConversation.mockResolvedValue({id:'conversation-1',experiment_id:'exp-1',title:'对话',messages:[old.user_message,old.assistant_message]})
    await openReview({onRun,onChanged})
    await screen.findByText('已确认执行当前 XML。')
    expect(onRun).not.toHaveBeenCalled()
    expect(onChanged).not.toHaveBeenCalled()
    expect(apiMock.confirmExecution).not.toHaveBeenCalled()
    expect(apiMock.sendConversationMessage).not.toHaveBeenCalled()
    expect(screen.getByRole('tab',{name:'XML 预览'})).toHaveAttribute('aria-selected','true')
  })

  it('refreshes a confirmed execution without overriding later tab navigation',async()=>{
    let finish!:(value:ConversationTurn)=>void
    apiMock.sendConversationMessage.mockImplementationOnce(()=>new Promise(done=>{finish=done}))
    const onRun=vi.fn(),onChanged=vi.fn()
    await openReview({onRun,onChanged})
    send()
    await waitFor(()=>expect(apiMock.sendConversationMessage).toHaveBeenCalledOnce())
    fireEvent.click(screen.getByRole('tab',{name:'参数解析'}))
    apiMock.listTasks.mockResolvedValue([task])
    await act(async()=>finish(reply(true)))
    expect(onRun).not.toHaveBeenCalled()
    expect(onChanged).toHaveBeenCalledOnce()
    await waitFor(()=>expect(apiMock.getExperiment).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('tab',{name:'参数解析'})).toHaveAttribute('aria-selected','true')
    expect(screen.getByRole('button',{name:'检查输入'})).toBeDisabled()
  })

  it.each(['view','preview'] as const)('respects a controlled %s change while authorization is pending',async changed=>{
    let finish!:(value:ConversationTurn)=>void
    apiMock.sendConversationMessage.mockImplementationOnce(()=>new Promise(done=>{finish=done}))
    const onRun=vi.fn(),onChanged=vi.fn()
    const page=await openReview({view:'review',onRun,onChanged})
    send()
    await waitFor(()=>expect(apiMock.sendConversationMessage).toHaveBeenCalledOnce())
    const view=changed==='view'?'plan':'review',previewId=changed==='preview'?'new-preview':'preview-1'
    page.rerender(<ExperimentWorkspace experimentId="exp-1" view={view} previewId={previewId} onRun={onRun} onChanged={onChanged} />)
    await act(async()=>finish(reply(true)))
    expect(onRun).not.toHaveBeenCalled()
    expect(onChanged).toHaveBeenCalledOnce()
    expect(screen.getByRole('tab',{name:changed==='view'?'参数解析':'XML 预览'})).toHaveAttribute('aria-selected','true')
  })

  it('refreshes after canceling a submitted authorization and ignores its late response',async()=>{
    let finish!:(value:ConversationTurn)=>void
    apiMock.sendConversationMessage.mockImplementationOnce(()=>new Promise(done=>{finish=done}))
    const onRun=vi.fn(),onChanged=vi.fn()
    await openReview({onRun,onChanged})
    send()
    await waitFor(()=>expect(apiMock.sendConversationMessage).toHaveBeenCalledOnce())
    const signal=apiMock.sendConversationMessage.mock.calls[0][3] as AbortSignal
    fireEvent.click(screen.getByRole('button',{name:'取消回复'}))
    expect(signal.aborted).toBe(true)
    await waitFor(()=>expect(apiMock.getExperiment).toHaveBeenCalledTimes(2))
    expect(onChanged).toHaveBeenCalledOnce()
    await act(async()=>finish(reply(true)))
    expect(onRun).not.toHaveBeenCalled()
    expect(screen.queryByText('已确认执行当前 XML。')).not.toBeInTheDocument()
    expect(screen.getByRole('tab',{name:'XML 预览'})).toHaveAttribute('aria-selected','true')
  })

  it('retains a locally prepared preview after canceling a review turn that has not started tasks',async()=>{
    apiMock.validateExperiment.mockResolvedValue({revision_id:'revision-3',revision:3,ok:true,missing:[]})
    apiMock.prepareExecution.mockResolvedValue(executionPreview)
    apiMock.sendConversationMessage.mockImplementationOnce(()=>new Promise(()=>{}))
    render(<ExperimentWorkspace experimentId="exp-1" />)
    await waitFor(()=>expect(screen.getByRole('button',{name:'检查输入'})).toBeEnabled())
    fireEvent.click(screen.getByRole('button',{name:'检查输入'}))
    await waitFor(()=>expect(screen.getByRole('button',{name:'生成 XML 并预览'})).toBeEnabled())
    fireEvent.click(screen.getByRole('button',{name:'生成 XML 并预览'}))
    await screen.findByRole('button',{name:'确认并开始解算'})
    send('解释这个 XML')
    await waitFor(()=>expect(apiMock.sendConversationMessage).toHaveBeenCalledOnce())
    fireEvent.click(screen.getByRole('button',{name:'取消回复'}))
    await waitFor(()=>expect(apiMock.getExperiment).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('button',{name:'确认并开始解算'})).toBeInTheDocument()
    expect(screen.queryByText('尚未生成执行 XML')).not.toBeInTheDocument()
  })

  it('shows the public execution failure and refreshes status without entering monitoring',async()=>{
    apiMock.sendConversationMessage.mockRejectedValueOnce(new ApiError('当前 XML 预览已过期，请重新生成。','execution_preview_expired',409))
    const onRun=vi.fn()
    await openReview({onRun})
    send()
    const chat=screen.getByRole('complementary',{name:'模型对话区'})
    await within(chat).findByText('当前 XML 预览已过期，请重新生成。')
    expect(onRun).not.toHaveBeenCalled()
    await waitFor(()=>expect(apiMock.getExperiment).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('tab',{name:'XML 预览'})).toHaveAttribute('aria-selected','true')
  })

  it('reconciles execution status after a connection failure without exposing transport diagnostics',async()=>{
    apiMock.sendConversationMessage.mockRejectedValueOnce(new TypeError('RAW_SECRET_NETWORK'))
    const onRun=vi.fn()
    await openReview({onRun})
    send()
    await waitFor(()=>expect(apiMock.getExperiment).toHaveBeenCalledTimes(2))
    expect(onRun).not.toHaveBeenCalled()
    const chat=screen.getByRole('complementary',{name:'模型对话区'})
    expect(chat).not.toHaveTextContent('RAW_SECRET_NETWORK')
    expect(chat).not.toHaveTextContent('当前计划未改变')
    expect(screen.getByRole('button',{name:'确认并开始解算'})).toBeInTheDocument()
  })

  it.each([
    {experiment_id:'another-experiment',preview_id:'preview-1'},
    {experiment_id:'exp-1',preview_id:'another-preview'},
  ])('ignores an execution result outside the submitted preview scope %#',async scope=>{
    apiMock.sendConversationMessage.mockResolvedValueOnce(reply(true,scope))
    const onRun=vi.fn(),onChanged=vi.fn()
    await openReview({onRun,onChanged})
    send()
    await screen.findByText('已确认执行当前 XML。')
    expect(onRun).not.toHaveBeenCalled()
    expect(onChanged).not.toHaveBeenCalled()
    expect(screen.getByRole('tab',{name:'XML 预览'})).toHaveAttribute('aria-selected','true')
  })
})
