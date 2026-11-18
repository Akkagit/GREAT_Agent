import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from '../App'
import type { ResearchSession, ResearchRunInput, ResearchRun, ResearchReport } from '../api/research'
import { selectOption } from './selectOption'

vi.mock('../api/client', async original => ({ ...await original<typeof import('../api/client')>(), api: { listExperiments: async () => [], ensureConversation: async () => ({ id: 'global', messages: [] }) } }))
const catalog = { experiments: [{ id: 'e1', title: '收敛对比', tasks: [{ id: 't1', year: 2024, doy: 350, attempts: [{ id: 'a1', number: 1, status: 'succeeded' }, { id: 'a2', number: 2, status: 'succeeded' }] }] }], sources: [{ id: 'project', name: '项目源码' }], documents: [{ id: 'd1', title: 'Reset 论文', base_id: 'base', version_id: 'v1' }], profiles: [{ id: 'default', name: '默认模型', configured: true }] }
const initial = (): ResearchSession => ({ id: 's1', title: '收敛课题', goal: '解释收敛差异', version: 1, material_revision: 1, state_revision: 0, settings: { model_profile_id: 'default', mode: 'quick' }, materials: [], material_summary: [], active_run_id: null, messages: [], items: [], reports: [] })
const answer = { id: 'm1', run_id: 'r0', role: 'assistant', status: 'completed', text: '窗口在缺测后重置。', blocks: [{ id: 'b1', type: 'observation', text: '窗口在缺测后重置。', evidence_ids: ['E1'] }], sequence: 1 }
let session: ResearchSession, posts: ResearchRunInput[], reports: ResearchReport[], created: Record<string, unknown>[], patches: Record<string, unknown>[], transportFails: boolean, conflict: boolean
let run: ResearchRun
let retries: { run_id: string; request_id: string }[], retryTransportFails: boolean, staleRetry: boolean
function response(value: unknown, status = 200) { return Promise.resolve(new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })) }
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear(); window.history.replaceState({}, '', '/?workspace=research&research_session=s1')
  session = initial(); posts = []; reports = []; created = []; patches = []; transportFails = false; conflict = false
  run = { id: 'r1', session_id: 's1', request_id: '', status: 'queued', stage: 'queued', message: '', mode: 'quick', model_profile_id: 'default' }
  retries = []; retryTransportFails = false; staleRetry = false
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace('/api/v1/research', ''), body = init?.body ? JSON.parse(String(init.body)) : undefined
    if (path === '/catalog') return response(catalog)
    if (path === '/sessions' && init?.method === 'POST') { created.push(body); session = { ...initial(), ...body }; return response(session, 201) }
    if (path === '/sessions') return response({ items: [session] })
    if (path === '/sessions/s1') return response(session)
    if (path === '/sessions/s2') return response({ ...initial(), id: 's2', title: '另一课题' })
    if (path === '/sessions/s1/materials') { patches.push(body); if (conflict) return response({ error: { code: 'version_conflict', message: '材料版本冲突' } }, 409); session = { ...session, version: session.version + 1, materials: body.materials }; return response(session) }
    if (path === '/sessions/s1/settings') { session = { ...session, version: session.version + 1, settings: { model_profile_id: body.model_profile_id, mode: body.mode } }; return response(session) }
    if (path === '/sessions/s1/runs') {
      posts.push(body)
      if (transportFails) return Promise.reject(new TypeError('连接中断'))
      run = { ...run, request_id: body.request_id, message: body.message }; session = { ...session, active_run_id: run.id, messages: [...session.messages, { id: `sent-user-${posts.length}`, run_id: run.id, role: 'user', text: body.message, status: 'completed', blocks: [], sequence: session.messages.length + 1 }] }
      return response(run, 202)
    }
    if (path === '/runs/r1') return response(run)
    if (path === '/runs/r2') return response(run)
    if (path === '/runs/r1/retry') {
      retries.push({ run_id: 'r1', request_id: body.request_id })
      if (retryTransportFails) return Promise.reject(new TypeError('重试连接中断'))
      if (staleRetry) return response({ error: { code: 'stale_retry', message: '研究状态已变化，请以原问题继续追问。' } }, 409)
      run = { ...run, id: 'r2', retry_of: 'r1', status: 'queued', stage: 'queued', request_id: body.request_id }
      session = { ...session, active_run_id: 'r2', messages: [...session.messages, { id: 'retry-user', run_id: 'r2', role: 'user', status: 'completed', text: run.message, blocks: [], sequence: 2 }] }
      return response(run, 202)
    }
    if (path === '/runs/r0') return response({ ...run, id: 'r0', status: 'completed', stage: 'completed' })
    if (path === '/runs/r1/cancel') { run = { ...run, status: 'cancelled' }; session = { ...session, active_run_id: null }; return response(run) }
    if (path === '/runs/r0/evidence/E1') return response({ id: 'ev1', alias: 'E1', kind: 'log', title: '历史日志', text: '2024-350 reset at missing epoch', locator: { task_id: 't1', attempt_id: 'a1', line_start: 12, line_end: 15 }, sha256: 'abc123', quality_flags: ['partial_snapshot'], available: true })
    if (path === '/runs/r1/evidence/E1') return response({ id: 'ev2', alias: 'E1', kind: 'metric', title: '另一轮指标', text: '第二轮已有统计', locator: { task_id: 't1', attempt_id: 'a2' }, sha256: 'hash2', quality_flags: [], available: true })
    if (path === '/sessions/s1/reports' && init?.method === 'POST') { const report = { id: 'report1', session_id: 's1', title: '收敛课题报告', content: '已保存的收敛分析正文', run_ids: ['r0'], version: 1 }; reports = [...reports, report]; return response(report, 201) }
    if (path === '/sessions/s1/reports') return response({ items: reports })
    throw new Error(`Unexpected request ${init?.method} ${url}`)
  }))
})
afterEach(() => vi.unstubAllGlobals())

it('creates a research topic with exact material identities without sending a model question', async () => {
  window.history.replaceState({}, '', '/?workspace=research')
  const user = userEvent.setup(); render(<App />)
  await user.type(await screen.findByRole('textbox', { name: '课题标题' }), 'Reset 分析')
  await user.click(screen.getByRole('checkbox', { name: '收敛对比 · 2024/350 · #1' }))
  await user.click(screen.getByRole('checkbox', { name: 'Reset 论文 · 版本 v1' }))
  await user.type(screen.getByRole('textbox', { name: '代码路径' }), 'backend/reset.py')
  await user.click(screen.getByRole('button', { name: '添加代码范围' }))
  await user.click(screen.getByRole('button', { name: '创建课题' }))
  expect(await screen.findByRole('heading', { name: 'Reset 分析' })).toBeInTheDocument()
  expect(created[0]).toMatchObject({ title: 'Reset 分析', materials: [{ kind: 'experiment', experiment_id: 'e1', task_id: 't1', attempt_id: 'a1', role: 'baseline' }, { kind: 'literature', document_id: 'd1', version_id: 'v1' }, { kind: 'code', source_id: 'project', paths: ['backend/reset.py'], provenance: 'unknown' }] })
  expect(posts).toHaveLength(0)
  expect(window.location.search).toContain('research_session=s1')
})

it('targets a precise prior answer and selects evidence using its originating run', async () => {
  session.messages = [answer]; session.items = [{ id: 'h1', kind: 'hypothesis', text: '可能与 reset 有关', status: 'proposed', support: ['E1'], opposition: [], origin_run_id: 'r0' }]
  const user = userEvent.setup(); render(<App />)
  await user.click(await screen.findByRole('button', { name: '查看证据 E1' }))
  expect(await screen.findByText('2024-350 reset at missing epoch')).toBeInTheDocument()
  expect(screen.getByText('a1')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '追问回答 b1' }))
  await user.type(screen.getByRole('textbox', { name: '科研问题' }), '请核对缺测窗口')
  await user.click(screen.getByRole('button', { name: '发送问题' }))
  await waitFor(() => expect(posts).toHaveLength(1))
  expect(posts[0]).toMatchObject({ expected_version: 1, message: '请核对缺测窗口', followup_target: { run_id: 'r0', message_id: 'm1', block_id: 'b1' } })
  expect(screen.getByRole('button', { name: '发送问题' })).toBeDisabled()
  await user.type(screen.getByRole('textbox', { name: '科研问题' }), '下一条草稿')
  expect(screen.getByRole('textbox', { name: '科研问题' })).toHaveValue('下一条草稿')
})

it('restores an active run after leaving and reloading without implicit cancellation', async () => {
  session.active_run_id = 'r1'; run.status = 'running'; run.stage = 'reading_materials'
  const user = userEvent.setup(), page = render(<App />)
  expect(await screen.findByRole('button', { name: '停止分析' })).toBeInTheDocument()
  await user.type(screen.getByRole('textbox', { name: '科研问题' }), '保留的下一轮草稿')
  await user.click(screen.getByRole('tab', { name: '实验工作台' }))
  await user.click(screen.getByRole('tab', { name: '科研分析' }))
  expect(screen.getByRole('textbox', { name: '科研问题' })).toHaveValue('保留的下一轮草稿')
  page.unmount(); render(<App />)
  expect(await screen.findByRole('button', { name: '停止分析' })).toBeInTheDocument()
  expect(screen.getByRole('textbox', { name: '科研问题' })).toHaveValue('保留的下一轮草稿')
  expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith('/cancel'))).toBe(false)
})

it('recovers an ambiguous acceptance with the same request payload while preserving an edited draft', async () => {
  transportFails = true
  const user = userEvent.setup(); render(<App />)
  await user.type(await screen.findByRole('textbox', { name: '科研问题' }), '原始问题')
  await user.click(screen.getByRole('button', { name: '发送问题' }))
  expect(await screen.findByRole('button', { name: '恢复提交' })).toBeInTheDocument()
  const draft = screen.getByRole('textbox', { name: '科研问题' }); expect(draft).toHaveValue('原始问题')
  await user.clear(draft); await user.type(draft, '未发送的新草稿'); transportFails = false
  await user.click(screen.getByRole('button', { name: '恢复提交' }))
  await waitFor(() => expect(posts).toHaveLength(2))
  expect(posts[1]).toEqual(posts[0]); expect(draft).toHaveValue('未发送的新草稿')
})

it('retains unsaved material selection and question draft after a version conflict', async () => {
  const user = userEvent.setup(); render(<App />)
  await user.type(await screen.findByRole('textbox', { name: '科研问题' }), '保留的问题')
  await user.click(screen.getByRole('checkbox', { name: 'Reset 论文 · 版本 v1' })); conflict = true
  await user.click(screen.getByRole('button', { name: '更新材料' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('材料版本冲突')
  expect(screen.getByRole('checkbox', { name: 'Reset 论文 · 版本 v1' })).toBeChecked()
  expect(screen.getByRole('textbox', { name: '科研问题' })).toHaveValue('保留的问题')
  conflict = false; await user.click(screen.getByRole('button', { name: '更新材料' }))
  await waitFor(() => expect(patches).toHaveLength(2))
  expect(patches[1].materials).toEqual([{ kind: 'literature', document_id: 'd1', version_id: 'v1' }])
})

it('saves an immutable report and restores it after refresh', async () => {
  session.messages = [answer]
  const user = userEvent.setup(), page = render(<App />)
  await user.click(await screen.findByRole('button', { name: '保存分析报告' }))
  expect(await screen.findByText('已保存的收敛分析正文')).toBeInTheDocument()
  page.unmount(); render(<App />)
  await user.click(await screen.findByRole('tab', { name: /报告/ }))
  expect(await screen.findByText('已保存的收敛分析正文')).toBeInTheDocument()
})

it('does not let a late question response replace another selected topic', async () => {
  let accept: ((value: Response) => void) | undefined
  const original = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith('/sessions/s1/runs') ? new Promise(resolve => { accept = resolve }) : original(url, init))
  const user = userEvent.setup(); render(<App />)
  await user.type(await screen.findByRole('textbox', { name: '科研问题' }), '延迟问题')
  await user.click(screen.getByRole('button', { name: '发送问题' }))
  await act(async () => { window.history.pushState({}, '', '/?workspace=research&research_session=s2'); fireEvent.popState(window) })
  expect(await screen.findByRole('heading', { name: '另一课题' })).toBeInTheDocument()
  await act(async () => accept?.(new Response(JSON.stringify(run), { status: 202 })))
  expect(screen.getByRole('heading', { name: '另一课题' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '停止分析' })).not.toBeInTheDocument()
})

it('refreshes selected code snapshots even when their registered paths are unchanged', async () => {
  session.materials = [{ kind: 'code', source_id: 'project', paths: ['backend/reset.py'], provenance: 'unknown' }]
  const user = userEvent.setup(); render(<App />)
  const refresh = await screen.findByRole('button', { name: '更新材料' })
  await waitFor(() => expect(refresh).toBeEnabled()); await user.click(refresh)
  await waitFor(() => expect(patches).toHaveLength(1))
  expect(patches[0]).toEqual({ expected_version: 1, materials: [{ kind: 'code', source_id: 'project', paths: ['backend/reset.py'], provenance: 'unknown' }] })
})

it('keeps pending questions only in tab storage and restores the original request after refresh', async () => {
  transportFails = true
  const user = userEvent.setup(), page = render(<App />)
  await user.type(await screen.findByRole('textbox', { name: '科研问题' }), '有范围的私有研究问题')
  await user.click(screen.getByRole('button', { name: '发送问题' }))
  await screen.findByRole('button', { name: '恢复提交' }); page.unmount()
  expect(JSON.stringify(localStorage)).not.toContain('有范围的私有研究问题')
  render(<App />); transportFails = false
  await user.click(await screen.findByRole('button', { name: '恢复提交' }))
  await waitFor(() => expect(posts).toHaveLength(2)); expect(posts[1]).toEqual(posts[0])
})

it('saves deep mode for the next run and stops only through the explicit stop action', async () => {
  const user = userEvent.setup(); render(<App />)
  await screen.findByRole('textbox', { name: '科研问题' })
  await selectOption(user, '分析深度', '深入 · 8 分钟预算')
  await user.type(screen.getByRole('textbox', { name: '科研问题' }), '深入检查')
  expect(screen.getByRole('button', { name: '发送问题' })).toBeDisabled()
  await user.click(screen.getByRole('button', { name: '保存下轮配置' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '发送问题' })).toBeEnabled())
  await user.click(screen.getByRole('button', { name: '发送问题' }))
  await user.click(await screen.findByRole('button', { name: '停止分析' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: '停止分析' })).not.toBeInTheDocument())
  expect(posts[0].expected_version).toBe(2)
  expect(session.settings.mode).toBe('deep')
})

it('does not let an older polling response undo an acknowledged material revision', async () => {
  session.active_run_id = 'r1'
  let resolvePoll: ((value: Response) => void) | undefined, gets = 0
  const original = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation((url, init) => {
    if (String(url).endsWith('/sessions/s1') && ++gets === 2) {
      const beforeUpdate = structuredClone(session)
      return new Promise(resolve => { resolvePoll = () => resolve(new Response(JSON.stringify(beforeUpdate))) })
    }
    return original(url, init)
  })
  const user = userEvent.setup(); render(<App />)
  await waitFor(() => expect(resolvePoll).toBeDefined())
  await user.click(screen.getByRole('checkbox', { name: 'Reset 论文 · 版本 v1' }))
  await user.click(screen.getByRole('button', { name: '更新材料' }))
  await waitFor(() => expect(session.version).toBe(2))
  await act(async () => resolvePoll?.(new Response()))
  await user.click(screen.getByRole('checkbox', { name: '收敛对比 · 2024/350 · #1' }))
  await user.click(screen.getByRole('button', { name: '更新材料' }))
  await waitFor(() => expect(patches).toHaveLength(2)); expect(patches[1].expected_version).toBe(2)
})

it('keeps material edits made while an update is in flight across a refresh', async () => {
  let accept: (() => void) | undefined
  const original = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation((url, init) => {
    if (String(url).endsWith('/sessions/s1/materials')) return new Promise(resolve => { accept = () => { void original(url, init).then(resolve) } })
    return original(url, init)
  })
  const user = userEvent.setup(), page = render(<App />)
  await screen.findByRole('textbox', { name: '科研问题' })
  await user.click(screen.getByRole('checkbox', { name: 'Reset 论文 · 版本 v1' }))
  await user.click(screen.getByRole('button', { name: '更新材料' }))
  await user.click(screen.getByRole('checkbox', { name: '收敛对比 · 2024/350 · #1' }))
  await act(async () => accept?.()); page.unmount(); render(<App />)
  expect(await screen.findByRole('checkbox', { name: '收敛对比 · 2024/350 · #1' })).toBeChecked()
})

it('opens a hypothesis support reference with its original run instead of treating a stable evidence ID as an alias', async () => {
  session.items = [{ id: 'h1', kind: 'hypothesis', text: 'reset 假设', status: 'challenged', support: ['stable-evidence-uuid'], opposition: [], support_refs: [{ evidence_id: 'stable-evidence-uuid', run_id: 'r0', alias: 'E1' }], origin_run_id: 'r0' }]
  const user = userEvent.setup(); render(<App />)
  await user.click(await screen.findByRole('button', { name: '支持证据 E1' }))
  expect(await screen.findByText('2024-350 reset at missing epoch')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '追问假设 h1' }))
  await user.type(screen.getByRole('textbox', { name: '科研问题' }), '反例是否推翻它')
  await user.click(screen.getByRole('button', { name: '发送问题' }))
  await waitFor(() => expect(posts).toHaveLength(1)); expect(posts[0].followup_target).toEqual({ run_id: 'r0', item_id: 'h1' })
})

it('restores the final failed run status after refresh even when the session has no active run', async () => {
  session.messages = [{ id: 'u1', run_id: 'r1', role: 'user', status: 'completed', text: '分析这个缺测窗口', blocks: [], sequence: 1 }]
  run = { ...run, status: 'failed', stage: 'failed', error_code: 'material_changed' }
  render(<App />)
  expect(await screen.findByText('material_changed')).toBeInTheDocument()
  expect(screen.getByRole('textbox', { name: '科研问题' })).toBeEnabled()
  expect(screen.queryByRole('button', { name: '停止分析' })).not.toBeInTheDocument()
})

it('keeps a completed partial answer visibly qualified by its evidence limitations', async () => {
  session.messages = [{ ...answer, run_id: 'r1' }]
  run = { ...run, status: 'completed', stage: 'completed', result: { answer: '已有局部结论', answer_blocks: [], answer_status: 'partial', limitations: ['历史二进制与当前源码的关联尚未核对'] } }
  render(<App />)
  expect(await screen.findByRole('region', { name: '本轮限制' })).toHaveTextContent('历史二进制与当前源码的关联尚未核对')
  expect(screen.getByText('局部结论')).toBeInTheDocument()
})

it.each(['failed', 'cancelled', 'interrupted', 'timed_out'])('explicitly retries a %s run while preserving the next-question draft', async status => {
  session.messages = [{ id: 'u1', run_id: 'r1', role: 'user', status: 'completed', text: '原始缺测问题', blocks: [], sequence: 1 }]
  run = { ...run, message: '原始缺测问题', status, stage: status }
  const user = userEvent.setup(); render(<App />)
  await user.type(await screen.findByRole('textbox', { name: '科研问题' }), '下一轮的新草稿')
  await user.click(await screen.findByRole('button', { name: '重试本轮分析' }))
  await screen.findByRole('button', { name: '停止分析' })
  expect(retries).toHaveLength(1); expect(retries[0].request_id).toMatch(/^[0-9a-f-]{36}$/)
  expect(run.retry_of).toBe('r1'); expect(posts).toHaveLength(0)
  expect(screen.getByRole('textbox', { name: '科研问题' })).toHaveValue('下一轮的新草稿')
})

it('recovers an uncertain retry with the same original run and request ID after refresh', async () => {
  session.messages = [{ id: 'u1', run_id: 'r1', role: 'user', status: 'completed', text: '原始问题', blocks: [], sequence: 1 }]
  run = { ...run, message: '原始问题', status: 'failed', stage: 'failed' }; retryTransportFails = true
  const user = userEvent.setup(), page = render(<App />)
  await user.click(await screen.findByRole('button', { name: '重试本轮分析' }))
  await screen.findByRole('button', { name: '恢复重试' })
  await user.type(screen.getByRole('textbox', { name: '科研问题' }), '新草稿保留'); page.unmount(); retryTransportFails = false
  render(<App />); await user.click(await screen.findByRole('button', { name: '恢复重试' }))
  await waitFor(() => expect(retries).toHaveLength(2)); expect(retries[1]).toEqual(retries[0])
  expect(screen.getByRole('textbox', { name: '科研问题' })).toHaveValue('新草稿保留')
})

it('offers a stale retry question as a new current-state followup without replacing a draft or auto-sending', async () => {
  session.messages = [{ id: 'u1', run_id: 'r1', role: 'user', status: 'completed', text: '原始问题', blocks: [], sequence: 1 }]
  run = { ...run, message: '原始问题', status: 'failed', stage: 'failed' }; staleRetry = true
  const user = userEvent.setup(); render(<App />)
  await user.type(await screen.findByRole('textbox', { name: '科研问题' }), '保留的补充约束')
  await user.click(await screen.findByRole('button', { name: '重试本轮分析' }))
  await user.click(await screen.findByRole('button', { name: '以原问题继续追问' }))
  const draft = screen.getByRole('textbox', { name: '科研问题' })
  expect(draft).toHaveValue('保留的补充约束\n\n原始问题'); expect(posts).toHaveLength(0)
  await user.click(screen.getByRole('button', { name: '发送问题' }))
  await waitFor(() => expect(posts).toHaveLength(1)); expect(posts[0].followup_target).toBeUndefined(); expect(posts[0].expected_version).toBe(1)
})

it('keeps historical partial and needs-input qualifications on their messages after a later completed turn', async () => {
  session.messages = [{ ...answer, id: 'partial-message', run_id: 'r-partial', text: '局部答复', blocks: [] }, { ...answer, id: 'needs-message', run_id: 'r-needs', text: '待补材料答复', blocks: [] }, { ...answer, id: 'latest-message', run_id: 'r1', text: '最新答复', blocks: [] }]
  session.runs = [
    { ...run, id: 'r-partial', status: 'completed', stage: 'completed', result: { answer: '局部答复', answer_blocks: [], answer_status: 'partial', limitations: ['缺少历史构建关联'] } },
    { ...run, id: 'r-needs', status: 'completed', stage: 'completed', result: { answer: '待补材料答复', answer_blocks: [], answer_status: 'needs_input', limitations: ['缺少同站同日对照'] } },
    { ...run, id: 'r1', status: 'completed', stage: 'completed', result: { answer: '最新答复', answer_blocks: [], answer_status: 'answered', limitations: [] } },
  ]
  run = session.runs[2]
  render(<App />)
  const partial = await screen.findByRole('article', { name: '科研消息 partial-message' })
  expect(within(partial).getByText('局部结论')).toBeInTheDocument(); expect(within(partial).getByText('缺少历史构建关联')).toBeInTheDocument()
  const needs = screen.getByRole('article', { name: '科研消息 needs-message' })
  expect(within(needs).getByText('等待补充材料')).toBeInTheDocument(); expect(within(needs).getByText('缺少同站同日对照')).toBeInTheDocument()
})

it('renders structured suggestion details as unexecuted text without relying on prose duplication', async () => {
  session.items = [{ id: 'suggest-1', kind: 'suggestion', text: '核对窗口重置分支', status: 'proposed', support: [], opposition: [], target: 'reset.cpp:144 / commit abc', rationale: '日志与静态条件存在对应疑点', change: '--- old\n+++ proposed\n- clear();\n+ preserve();', impact: '可能改变重新收敛窗口', validation: '人工核对同站同日历史统计', origin_run_id: 'r0' }]
  render(<App />)
  expect(await screen.findByText('reset.cpp:144 / commit abc')).toBeInTheDocument()
  expect(screen.getByText('日志与静态条件存在对应疑点')).toBeInTheDocument()
  expect(screen.getByLabelText('未应用建议差异')).toHaveTextContent('preserve();')
  expect(screen.getByText('可能改变重新收敛窗口')).toBeInTheDocument(); expect(screen.getByText('人工核对同站同日历史统计')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /应用建议|执行建议|运行代码/ })).not.toBeInTheDocument()
})

it('does not erase a newly saved selected report when the older initial report list arrives late', async () => {
  session.messages = [answer]
  let finishInitial: (() => void) | undefined
  const original = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation((url, init) => {
    if (String(url).endsWith('/sessions/s1/reports') && init?.method === 'GET') return new Promise(resolve => { finishInitial = () => resolve(new Response(JSON.stringify({ items: [] }))) })
    return original(url, init)
  })
  const user = userEvent.setup(); render(<App />)
  await user.click(await screen.findByRole('button', { name: '保存分析报告' }))
  expect(await screen.findByText('已保存的收敛分析正文')).toBeInTheDocument()
  await act(async () => finishInitial?.())
  expect(screen.getByText('已保存的收敛分析正文')).toBeInTheDocument()
})

it('opens report evidence by snapshot run identity when two report runs reuse E1', async () => {
  reports = [{ id: 'report-snapshot', session_id: 's1', title: '确定性生成的报告', content: '报告正文包含固定轮次的观察和限制', run_ids: ['r0', 'r1'], snapshot: { evidence: [
    { id: 'ev1', run_id: 'r0', alias: 'E1', kind: 'log', title: '第一轮日志', text: '原文一', locator: {}, sha256: 'hash1', quality_flags: [], available: true },
    { id: 'ev2', run_id: 'r1', alias: 'E1', kind: 'metric', title: '第二轮指标', text: '原文二', locator: {}, sha256: 'hash2', quality_flags: [], available: true },
  ] } }]
  const user = userEvent.setup(); render(<App />)
  await user.click(await screen.findByRole('tab', { name: /报告 \(1\)/ }))
  await user.click(screen.getByRole('button', { name: '查看报告证据 r1 E1' }))
  expect(await screen.findByText('第二轮已有统计')).toBeInTheDocument()
})

it('adds historical code and literature versions alongside their current selections without starting analysis', async () => {
  session.materials = [{ kind: 'code', source_id: 'project', paths: ['src/reset.cpp'], provenance: 'unknown' }, { kind: 'literature', document_id: 'd1', version_id: 'v1' }]
  session.runs = [{ ...run, id: 'r-old', status: 'completed', stage: 'completed', snapshot_summary: [
    { id: 'old-code-material', kind: 'code', title: 'src/reset.cpp', locator: { source_id: 'project', path: 'src/reset.cpp', provenance: 'unknown' }, sha256: 'old-code-sha' },
    { id: 'old-literature', kind: 'literature', title: 'Reset 论文旧版', locator: { document_id: 'd1', version_id: 'v0' }, sha256: 'old-paper-sha' },
  ] }]
  const user = userEvent.setup(); render(<App />)
  await user.click(await screen.findByText('添加历史材料版本'))
  await user.click(screen.getByRole('button', { name: '添加历史代码 r-old src/reset.cpp' }))
  await user.click(screen.getByRole('button', { name: '添加历史文献 r-old Reset 论文旧版' }))
  await user.click(screen.getByRole('button', { name: '更新材料' }))
  await waitFor(() => expect(patches).toHaveLength(1))
  expect(patches[0].materials).toEqual([
    { kind: 'code', source_id: 'project', paths: ['src/reset.cpp'], provenance: 'unknown' },
    { kind: 'literature', document_id: 'd1', version_id: 'v1' },
    { kind: 'code', source_id: 'project', paths: ['src/reset.cpp'], provenance: 'unknown', snapshot_run_id: 'r-old', snapshot_material_id: 'old-code-material' },
    { kind: 'literature', document_id: 'd1', version_id: 'v0' },
  ])
  expect(posts).toHaveLength(0)
})

it('recognizes an already accepted retry after its HTTP response is lost without issuing a second retry', async () => {
  session.messages = [{ id: 'u1', run_id: 'r1', role: 'user', status: 'completed', text: '原始问题', blocks: [], sequence: 1 }]
  run = { ...run, message: '原始问题', status: 'failed', stage: 'failed' }
  const original = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith('/runs/r1/retry') ? original(url, init).then(() => { throw new TypeError('已接受后连接中断') }) : original(url, init))
  const user = userEvent.setup(); render(<App />)
  await user.type(await screen.findByRole('textbox', { name: '科研问题' }), '保留的下一轮问题')
  await user.click(await screen.findByRole('button', { name: '重试本轮分析' }))
  await screen.findByRole('button', { name: '停止分析' })
  await waitFor(() => expect(screen.queryByRole('button', { name: '恢复重试' })).not.toBeInTheDocument())
  expect(retries).toHaveLength(1); expect(screen.getByRole('textbox', { name: '科研问题' })).toHaveValue('保留的下一轮问题')
})

it('renders immutable structured suggestion fields from a historical run even when current items changed', async () => {
  session.messages = [{ ...answer, id: 'old-suggestion', run_id: 'r-old', text: '请人工核查', blocks: [] }, { ...answer, id: 'new-answer', run_id: 'r1', text: '新轮次回答', blocks: [] }]
  session.runs = [{ ...run, id: 'r-old', status: 'completed', stage: 'completed', result: { answer: '请人工核查', answer_blocks: [], answer_status: 'partial', suggestions: [{ text: '核查原轮重置建议', target: 'historical.cpp:18', rationale: '原轮静态依据', change: '- reset_all();\n+ reset_selected();', impact: '原轮影响范围', validation: '原轮人工复核条件' }] } }]
  render(<App />)
  const article = await screen.findByRole('article', { name: '科研消息 old-suggestion' })
  expect(within(article).getByText('historical.cpp:18')).toBeInTheDocument(); expect(within(article).getByText('原轮静态依据')).toBeInTheDocument()
  expect(within(article).getByLabelText('未应用建议差异')).toHaveTextContent('reset_selected();')
  expect(within(article).getByText('原轮人工复核条件')).toBeInTheDocument()
})

it('does not reconstruct a redacted report from evidence snapshot text after source revocation', async () => {
  reports = [{ id: 'redacted-report', session_id: 's1', title: '来源已撤销的报告', content: '', run_ids: ['r0'], snapshot: { evidence: [{ id: 'old-evidence', run_id: 'r0', alias: 'E1', kind: 'log', title: '已不可访问的日志', text: '不得回显的历史缓存', locator: {}, sha256: 'oldhash', quality_flags: [], available: false }] } }]
  const user = userEvent.setup(); render(<App />)
  await user.click(await screen.findByRole('tab', { name: /报告 \(1\)/ }))
  expect(screen.getByText('来源当前不可用')).toBeInTheDocument()
  expect(screen.queryByText('不得回显的历史缓存')).not.toBeInTheDocument(); expect(screen.queryByRole('button', { name: '查看报告证据 r0 E1' })).not.toBeInTheDocument()
})

it('treats repository-sorted material fields as the acknowledged selection and clears its staged copy', async () => {
  const original = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation((url, init) => {
    if (String(url).endsWith('/sessions/s1/materials')) return original(url, init).then(async response => {
      const saved = await response.json()
      saved.materials = saved.materials.map((material: Record<string, unknown>) => Object.fromEntries(Object.entries(material).sort(([left], [right]) => left.localeCompare(right))))
      session = saved
      return new Response(JSON.stringify(saved))
    })
    return original(url, init)
  })
  const user = userEvent.setup(); render(<App />)
  await user.type(await screen.findByRole('textbox', { name: '科研问题' }), '按已保存材料分析')
  await user.type(screen.getByRole('textbox', { name: '代码路径' }), 'algorithm.py\nhelpers.py')
  await user.click(screen.getByRole('button', { name: '添加代码范围' }))
  await user.click(screen.getByRole('button', { name: '更新材料' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '发送问题' })).toBeEnabled())
  expect(sessionStorage.getItem('great-agent-research-materials-v1:s1')).toBeNull()
  expect(session.materials).toEqual([{ kind: 'code', source_id: 'project', paths: ['algorithm.py', 'helpers.py'], provenance: 'unknown' }])
})

it('removes a checked literature selection after the server reordered its property order', async () => {
  session.materials = [{ document_id: 'd1', kind: 'literature', version_id: 'v1' }]
  const user = userEvent.setup(); render(<App />)
  const document = await screen.findByRole('checkbox', { name: 'Reset 论文 · 版本 v1' })
  await waitFor(() => expect(document).toBeChecked()); await user.click(document)
  expect(document).not.toBeChecked()
})

it.each(['path order', 'historical identity'])('retains a material change when its %s differs', async difference => {
  session.materials = [{ kind: 'code', source_id: 'project', paths: ['algorithm.py', 'helpers.py'], provenance: 'unknown', ...(difference === 'historical identity' ? { snapshot_run_id: 'r-old', snapshot_material_id: 'old-material' } : {}) }]
  const user = userEvent.setup(); render(<App />)
  await user.type(await screen.findByRole('textbox', { name: '科研问题' }), '请分析')
  await user.click(screen.getByRole('button', { name: '移除材料 1' }))
  await user.type(screen.getByRole('textbox', { name: '代码路径' }), difference === 'path order' ? 'helpers.py\nalgorithm.py' : 'algorithm.py\nhelpers.py')
  await user.click(screen.getByRole('button', { name: '添加代码范围' }))
  expect(screen.getByRole('button', { name: '发送问题' })).toBeDisabled()
})
