import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../App'
import entryHtml from '../../index.html?raw'
import type { ReactNode } from 'react'
import type { ExecutionPreview } from '../api/types'

const workspaceMock = vi.hoisted(() => ({ callbacks: [] as Array<{id:string;onPrepared:(value:ExecutionPreview)=>void;onRun:()=>void}> }))

const apiMock = vi.hoisted(() => ({
  ensureConversation: vi.fn(), sendConversationMessage: vi.fn(),
  createExperiment: vi.fn(),
  listExperiments: vi.fn(),
}))

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>()
  return { ...original, api: { ...original.api, ...apiMock } }
})

vi.mock('../pages/ExperimentWorkspace', () => ({
  ExperimentWorkspace: ({ experimentId, view, history, onViewChange, onPrepared, onRun }: { experimentId: string; view: string; history: ReactNode; onViewChange: (view: string) => void;onPrepared:(value:ExecutionPreview)=>void;onRun:()=>void }) => {
    workspaceMock.callbacks.push({id:experimentId,onPrepared,onRun})
    return <>{history}<div>{view==='run'?'运行看板':'实验工作区'} {experimentId}</div><button onClick={()=>onViewChange('plan')}>参数解析</button></>
  },
}))

vi.mock('../pages/RunDashboard', () => ({
  RunDashboard: ({ experimentId }: { experimentId: string }) => (
    <div>运行看板 {experimentId}</div>
  ),
}))

vi.mock('../pages/KnowledgeWorkspace', () => ({
  KnowledgeWorkspace: ({ baseId, chatId, onLocationChange }: { baseId?: string; chatId?: string; onLocationChange: (baseId?: string, chatId?: string) => void }) => <section aria-label="知识页面测试"><p>知识页 {baseId} {chatId}</p><input aria-label="知识页草稿" defaultValue="" /><button onClick={() => onLocationChange('base-selected', 'chat-selected')}>选择知识会话</button></section>,
}))

describe('App entry', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    workspaceMock.callbacks=[]
    window.history.replaceState({}, '', '/')
    apiMock.createExperiment.mockResolvedValue({
      id: 'exp-created',
      status: 'draft',
      current_revision: 1,
    })
    apiMock.listExperiments.mockResolvedValue([])
    apiMock.ensureConversation.mockResolvedValue({id:'global-conversation',experiment_id:null,title:'GNSS',messages:[]})
  })

  it('supports durable global GNSS consultation without creating an experiment',async()=>{
    apiMock.sendConversationMessage.mockResolvedValue({conversation_id:'global-conversation',run_id:'run',request_id:'request',status:'completed',timings:{},user_message:{id:'u',role:'user',status:'completed',blocks:[{type:'text',text:'解释 PPP'}]},assistant_message:{id:'a',role:'assistant',status:'completed',blocks:[{type:'text',text:'PPP 使用精密轨道和钟差。'}]}})
    const user=userEvent.setup()
    render(<App />)
    await user.type(await screen.findByRole('textbox',{name:/message/i}),'解释 PPP')
    await user.click(screen.getByRole('button',{name:'发送'}))
    expect(await screen.findByText('PPP 使用精密轨道和钟差。')).toBeInTheDocument()
    expect(apiMock.ensureConversation).toHaveBeenCalledWith(null)
    expect(apiMock.createExperiment).not.toHaveBeenCalled()
  })

  it('opens the durable research workspace as a third independent route', async () => {
    window.history.replaceState({}, '', '/?workspace=research&research_session=research-1')
    render(<App />)
    expect(screen.getByRole('tab', { name: '科研分析' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel', { name: '科研分析' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '建立首个 PPP-FLT 实验' })).not.toBeInTheDocument()
  })

  it('creates the first experiment instead of opening a fake local id', async () => {
    const user = userEvent.setup()
    render(<App />)

    expect(screen.getByRole('heading', { name: '建立首个 PPP-FLT 实验' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '创建实验并进入规划台' }))

    expect(await screen.findByText('实验工作区 exp-created')).toBeInTheDocument()
    expect(window.location.search).toBe('?experiment=exp-created')
  })

  it('opens a conversation-created draft through the real app navigation', async () => {
    const { executionPreview } = await import('./executionFixture')
    apiMock.ensureConversation.mockResolvedValue({id:'global-conversation',experiment_id:null,title:'GNSS',messages:[{
      id:'created',role:'assistant',status:'completed',blocks:[{type:'experiment_created',experiment_id:'chat-created',status:'draft',current_revision:1,spec:executionPreview.spec}],
    }]})
    const user=userEvent.setup()
    render(<App />)
    await user.click(await screen.findByRole('button',{name:'进入实验'}))
    expect(await screen.findByText('实验工作区 chat-created')).toBeInTheDocument()
    expect(window.location.search).toBe('?experiment=chat-created')
    expect(apiMock.createExperiment).not.toHaveBeenCalled()
  })

  it('shows startup guidance when the built page is opened as a file', () => {
    const page = new DOMParser().parseFromString(entryHtml, 'text/html')
    const fallbackScript = [...page.scripts].find((script) =>
      script.textContent.includes('file-entry-notice'),
    )
    expect(fallbackScript).toBeDefined()
    Function('window', 'document', fallbackScript!.textContent)(
      { location: { protocol: 'file:' } },
      page,
    )

    const notice = page.querySelector<HTMLElement>('[role="alert"]')
    expect(notice).not.toBeNull()
    expect(notice!.hasAttribute('hidden')).toBe(false)
    expect(notice!.textContent).toContain('http://127.0.0.1:8765')
  })

  it('opens a real historical experiment in monitoring without creating or running it', async () => {
    const { executionPreview } = await import('./executionFixture')
    apiMock.listExperiments.mockResolvedValue([{ id: 'historic-350', status: 'ready', current_revision: 1, spec: { ...executionPreview.spec, date_segments: [{ year: 2024, start_doy: 350, end_doy: 350 }] }, task_count: 1, succeeded_count: 1, run_status: 'succeeded' }])
    const user = userEvent.setup()
    render(<App />)
    await user.click(await screen.findByRole('button', { name: /查看实验.*350/ }))
    expect(await screen.findByText('运行看板 historic-350')).toBeInTheDocument()
    expect(apiMock.createExperiment).not.toHaveBeenCalled()
    expect(window.location.search).toContain('view=run')
    await user.click(screen.getByRole('button', { name: '参数解析' }))
    expect(await screen.findByText('实验工作区 historic-350')).toBeInTheDocument()
  })

  it('ignores completion callbacks from the old A route after navigating A to B to A', async () => {
    const { executionPreview }=await import('./executionFixture')
    window.history.replaceState({},'', '/?experiment=exp-A')
    apiMock.listExperiments.mockResolvedValue(['exp-A','exp-B'].map(id=>({id,status:'draft',current_revision:1,spec:executionPreview.spec,task_count:0,succeeded_count:0,run_status:'not_started'})))
    const user=userEvent.setup()
    render(<App />)
    const old=workspaceMock.callbacks.find(item=>item.id==='exp-A')!
    await user.click(await screen.findByRole('button',{name:/查看实验.*exp-B$/}))
    await user.click(await screen.findByRole('button',{name:/查看实验.*exp-A$/}))
    const location=window.location.search
    await act(async()=>{old.onPrepared({...executionPreview,experiment_id:'exp-A'})})
    expect(window.location.search).toBe(location)
    await act(async()=>{old.onRun()})
    expect(window.location.search).toBe(location)
    expect(apiMock.createExperiment).not.toHaveBeenCalled()
  })

  it('opens the knowledge workspace as a full page and keeps experiment URL state while switching tabs', async () => {
    window.history.replaceState({}, '', '/?experiment=exp-A&view=review&preview=preview-A')
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('tab', { name: '文献知识库' }))
    expect(screen.getByRole('region', { name: '知识页面测试' })).toBeVisible()
    expect(screen.getByText('实验工作区 exp-A')).not.toBeVisible()
    let params = new URLSearchParams(window.location.search)
    expect(params.get('workspace')).toBe('knowledge')
    expect(params.get('experiment')).toBe('exp-A')
    expect(params.get('view')).toBe('review')
    expect(params.get('preview')).toBe('preview-A')
    await user.type(screen.getByRole('textbox', { name: '知识页草稿' }), '保留文献问题')
    await user.click(screen.getByRole('button', { name: '选择知识会话' }))
    params = new URLSearchParams(window.location.search)
    expect(params.get('knowledge_base')).toBe('base-selected')
    expect(params.get('knowledge_chat')).toBe('chat-selected')
    await user.click(screen.getByRole('tab', { name: '实验工作台' }))
    expect(screen.getByText('实验工作区 exp-A')).toBeVisible()
    expect(window.location.search).toBe('?experiment=exp-A&view=review&preview=preview-A')
    await user.click(screen.getByRole('tab', { name: '文献知识库' }))
    expect(screen.getByRole('textbox', { name: '知识页草稿' })).toHaveValue('保留文献问题')
    expect(new URLSearchParams(window.location.search).get('knowledge_chat')).toBe('chat-selected')
    expect(apiMock.createExperiment).not.toHaveBeenCalled()
  })

  it('restores direct knowledge links and preserves the global consultation draft across page tabs', async () => {
    window.history.replaceState({}, '', '/?workspace=knowledge&knowledge_base=base-link&knowledge_chat=chat-link')
    const user = userEvent.setup()
    render(<App />)
    expect(screen.getByText('知识页 base-link chat-link')).toBeVisible()
    await user.click(screen.getByRole('tab', { name: '实验工作台' }))
    const input = await screen.findByRole('textbox', { name: /message/i })
    await user.type(input, '保留实验咨询草稿')
    await user.click(screen.getByRole('tab', { name: '文献知识库' }))
    await user.click(screen.getByRole('tab', { name: '实验工作台' }))
    expect(screen.getByRole('textbox', { name: /message/i })).toHaveValue('保留实验咨询草稿')
    expect(apiMock.createExperiment).not.toHaveBeenCalled()
  })

  it('responds to browser workspace history and ignores experiment completion after leaving its tab', async () => {
    const { executionPreview } = await import('./executionFixture')
    window.history.replaceState({}, '', '/?experiment=exp-A&view=review&preview=preview-A')
    const user = userEvent.setup()
    render(<App />)
    const old = workspaceMock.callbacks.find(item => item.id === 'exp-A')!
    await user.click(screen.getByRole('tab', { name: '文献知识库' }))
    const location = window.location.search
    await act(async () => { old.onPrepared({ ...executionPreview, experiment_id: 'exp-A' }); old.onRun() })
    expect(window.location.search).toBe(location)
    window.history.replaceState({}, '', '/?experiment=exp-A&view=review&preview=preview-A')
    fireEvent.popState(window)
    expect(screen.getByRole('tab', { name: '实验工作台' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('实验工作区 exp-A')).toBeVisible()
  })
})
