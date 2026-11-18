import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { KnowledgeWorkspace } from '../pages/KnowledgeWorkspace'
import { selectOption } from './selectOption'

const document = { id: 'doc-a', base_id: 'base-a', filename: 'study.md', title: 'PPP 研究', enabled: true, active_version_id: 'version-a', status: 'completed' }
const binding = { base_id: 'base-a', scope_mode: 'selected', document_ids: ['doc-a'], model_profile_id: 'default', binding_revision: 1 }
const citation = { type: 'citation', source_id: 'S1', document_id: 'doc-a', version_id: 'version-a', chunk_id: 'chunk-a', title: 'PPP 研究', filename: 'study.md', evidence_text: '原始证据 3.4 min', source_sha256: 'hash', locations: [], quality_flags: [], available: true }
const emptyChat = { id: 'chat-a', title: '文献对话', binding, messages: [], active_run_id: null }
const answer = { id: 'answer-a', role: 'assistant', status: 'completed', blocks: [{ type: 'text', text: '依据文献为 **3.4 min** [S1]。' }, citation] }
let configured = true
let calls: { path: string; method: string; body: Record<string, unknown> }[]
let override: (path: string, method: string, init: RequestInit) => Response | Promise<Response> | undefined
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }) }
function deferred<T>() { let resolve!: (value: T) => void; return { promise: new Promise<T>(done => { resolve = done }), resolve: (value: T) => resolve(value) } }
beforeEach(() => {
  configured = true; calls = []; override = () => undefined
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), 'http://localhost'); const path = (url.pathname + url.search).replace('/api/v1/knowledge', ''); const method = init.method ?? 'GET'
    calls.push({ path, method, body: init.body && typeof init.body === 'string' ? JSON.parse(init.body) : {} })
    const result = override(path, method, init); if (result) return await result
    if (path === '/bases') return json({ items: [{ id: 'base-a', name: 'GNSS 文献' }, { id: 'base-b', name: '其他库' }] })
    if (path === '/bases/base-a/documents') return json({ items: [document] })
    if (path === '/bases/base-b/documents') return json({ items: [] })
    if (path === '/model-profiles') return json({ items: [{ id: 'default', name: 'GLM', configured, location: 'cloud', provider: 'glm' }] })
    if (path.startsWith('/conversations?')) return json({ items: [] })
    if (path === '/conversations' && method === 'POST') return json(emptyChat)
    if (path === '/conversations/chat-a' && method === 'GET') return json(emptyChat)
    if (path === '/conversations/chat-a/messages' && method === 'POST') return json({ conversation_id: 'chat-a', run_id: 'run-a', request_id: JSON.parse(String(init.body)).request_id, status: 'completed', user_message: { id: 'user-a', role: 'user', status: 'completed', blocks: [{ type: 'text', text: '问题' }] }, assistant_message: answer })
    if (path === '/conversations/chat-a/messages/answer-a/citations/S1') return json(citation)
    throw new Error(`Unexpected ${method} ${path}`)
  }))
})
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

it('requires explicit selected documents, sends a bound real turn and opens its persisted citation', async () => {
  const user = userEvent.setup(); const navigate = vi.fn()
  render(<KnowledgeWorkspace baseId="base-a" onLocationChange={navigate} />)
  await screen.findByRole('checkbox', { name: '选择 PPP 研究' })
  expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()
  expect(screen.getByText('请勾选至少一篇可检索文献。')).toBeInTheDocument()
  await user.click(screen.getByRole('checkbox', { name: '选择 PPP 研究' }))
  await user.type(screen.getByRole('textbox', { name: '文献问题' }), '收敛时间？')
  await user.click(screen.getByRole('button', { name: '发送' }))
  expect(await screen.findByText('3.4 min', { selector: 'strong' })).toBeInTheDocument()
  expect(calls.find(call => call.path === '/conversations' && call.method === 'POST')?.body).toMatchObject({ base_id: 'base-a', scope_mode: 'selected', document_ids: ['doc-a'] })
  expect(navigate).toHaveBeenCalledWith('base-a', 'chat-a')
  await user.click(screen.getByRole('button', { name: /S1.*PPP 研究/ }))
  const source = screen.getByRole('complementary', { name: '来源核对' })
  expect(await within(source).findByText('原始证据 3.4 min')).toBeInTheDocument()
  expect(within(source).getByText('版本 version-a')).toBeInTheDocument()
  expect(screen.getByText(/问题和所选证据会发送至云端模型/)).toBeInTheDocument()
})

it('shows real active-version page and chunk counts in the library and parsing view', async () => {
  override = path => path === '/bases/base-a/documents' ? json({ items: [{ ...document, page_count: 16, chunk_count: 91 }] }) : undefined
  const user = userEvent.setup()
  render(<KnowledgeWorkspace baseId="base-a" onLocationChange={() => {}} />)
  const library = screen.getByRole('complementary', { name: '文献范围' })
  expect(await within(library).findByText(/16 页 · 91 个切片/)).toBeInTheDocument()
  await user.click(screen.getByRole('tab', { name: '文档与解析' }))
  expect(within(screen.getByRole('region', { name: '文献管理' })).getByText('16 页 · 91 个切片')).toBeInTheDocument()
})

it('restores immutable citations and reports unavailable sources without opening a newer original', async () => {
  override = (path) => path === '/conversations/chat-a' ? json({ ...emptyChat, messages: [answer] }) : path.endsWith('/citations/S1') ? json({ ...citation, available: false, reason: '文献已停用' }) : undefined
  const user = userEvent.setup()
  render(<KnowledgeWorkspace baseId="base-a" chatId="chat-a" onLocationChange={() => {}} />)
  await user.click(await screen.findByRole('button', { name: /S1.*PPP 研究/ }))
  const source = screen.getByRole('complementary', { name: '来源核对' })
  expect(await within(source).findByText('文献已停用')).toBeInTheDocument()
  expect(within(source).queryByRole('link', { name: /原文/ })).not.toBeInTheDocument()
})

it('lists each physical citation page once while retaining all evidence locations', async () => {
  const repeatedCitation = { ...citation, locations: [{ page_index: 1, page_label: '2', bbox: [10, 20, 50, 60] }, { page_index: 1, page_label: '2', bbox: [60, 20, 90, 60] }, { page_index: 2, page_label: '3', bbox: [10, 20, 50, 60] }] }
  override = path => path === '/conversations/chat-a' ? json({ ...emptyChat, messages: [{ ...answer, blocks: [answer.blocks[0], repeatedCitation] }] }) : undefined
  render(<KnowledgeWorkspace baseId="base-a" chatId="chat-a" onLocationChange={() => {}} />)
  const card = await screen.findByRole('button', { name: /S1.*PPP 研究/ })
  expect(card).toHaveTextContent('第 2 页、第 3 页')
  expect(card).not.toHaveTextContent('第 2 页、第 2 页')
})

it('keeps drafts across parsing tabs and library changes and ignores late chat details', async () => {
  const pending = deferred<Response>()
  override = path => path === '/conversations/chat-a' ? pending.promise : undefined
  const view = render(<KnowledgeWorkspace baseId="base-a" chatId="chat-a" onLocationChange={() => {}} />)
  const input = await screen.findByRole('textbox', { name: '文献问题' })
  fireEvent.change(input, { target: { value: '未发送草稿' } })
  fireEvent.click(screen.getByRole('tab', { name: '文档与解析' }))
  fireEvent.click(screen.getByRole('tab', { name: '文献对话' }))
  expect(screen.getByRole('textbox', { name: '文献问题' })).toHaveValue('未发送草稿')
  view.rerender(<KnowledgeWorkspace baseId="base-b" onLocationChange={() => {}} />)
  await screen.findByText('此知识库还没有文献')
  await act(async () => pending.resolve(json({ ...emptyChat, messages: [answer] })))
  expect(screen.queryByText('3.4 min', { selector: 'strong' })).not.toBeInTheDocument()
  view.rerender(<KnowledgeWorkspace baseId="base-a" chatId="chat-a" onLocationChange={() => {}} />)
  expect(await screen.findByRole('textbox', { name: '文献问题' })).toHaveValue('未发送草稿')
})

it('cancels the owned request and keeps a failed retry idempotent', async () => {
  const pending = deferred<Response>(); let signal: AbortSignal | null = null
  override = (path, method, init) => {
    if (path.endsWith('/messages') && method === 'POST') { signal = init.signal ?? null; return pending.promise }
    if (path.endsWith('/cancel')) return json({ status: 'cancelled' })
  }
  const user = userEvent.setup()
  render(<KnowledgeWorkspace baseId="base-a" chatId="chat-a" onLocationChange={() => {}} />)
  await waitFor(() => expect(screen.getByRole('checkbox', { name: '选择 PPP 研究' })).toBeChecked())
  await user.type(screen.getByRole('textbox', { name: '文献问题' }), '问题')
  await user.click(screen.getByRole('button', { name: '发送' }))
  await user.click(await screen.findByRole('button', { name: '停止生成' }))
  await waitFor(() => expect(signal?.aborted).toBe(true))
  const turn = calls.find(call => call.path.endsWith('/messages'))!
  expect(calls.find(call => call.path.endsWith('/cancel'))?.body.request_id).toBe(turn.body.request_id)
  await act(async () => pending.resolve(json({ conversation_id: 'chat-a', assistant_message: answer })))
  expect(screen.queryByText('3.4 min', { selector: 'strong' })).not.toBeInTheDocument()
})

it('blocks unconfigured models with a concrete configuration hint', async () => {
  configured = false
  render(<KnowledgeWorkspace baseId="base-a" chatId="chat-a" onLocationChange={() => {}} />)
  expect(await screen.findByText(/请在服务器配置模型凭据/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()
})

it('reuses the same request identity after a failed send instead of generating a duplicate question', async () => {
  let attempts = 0
  override = (path, method) => path.endsWith('/messages') && method === 'POST' && ++attempts === 1 ? json({ error: { message: '连接中断，请重试' } }, 503) : undefined
  const user = userEvent.setup()
  render(<KnowledgeWorkspace baseId="base-a" chatId="chat-a" onLocationChange={() => {}} />)
  await waitFor(() => expect(screen.getByRole('checkbox', { name: '选择 PPP 研究' })).toBeChecked())
  const input = screen.getByRole('textbox', { name: '文献问题' })
  fireEvent.change(input, { target: { value: '重试的问题' } })
  await user.click(screen.getByRole('button', { name: '发送' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('连接中断，请重试')
  expect(input).toHaveValue('重试的问题')
  await user.click(screen.getByRole('button', { name: '发送' }))
  await screen.findByText('3.4 min', { selector: 'strong' })
  const requests = calls.filter(call => call.path.endsWith('/messages'))
  expect(requests).toHaveLength(2)
  expect(requests[1].body.request_id).toBe(requests[0].body.request_id)
})

it('uses a fresh request identity when retrying a terminal 502 provider failure with the same question', async () => {
  let attempts = 0
  override = (path, method, init) => path.endsWith('/messages') && method === 'POST' && ++attempts === 1 ? json({ error: { code: 'model_proposal_error', category: 'provider', request_id: JSON.parse(String(init.body)).request_id, message: '模型本轮生成失败，请重试' } }, 502) : undefined
  const user = userEvent.setup()
  render(<KnowledgeWorkspace baseId="base-a" chatId="chat-a" onLocationChange={() => {}} />)
  await waitFor(() => expect(screen.getByRole('checkbox', { name: '选择 PPP 研究' })).toBeChecked())
  fireEvent.change(screen.getByRole('textbox', { name: '文献问题' }), { target: { value: '同一个问题' } })
  await user.click(screen.getByRole('button', { name: '发送' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('模型本轮生成失败，请重试')
  await user.click(screen.getByRole('button', { name: '发送' }))
  await screen.findByText('3.4 min', { selector: 'strong' })
  const requests = calls.filter(call => call.path.endsWith('/messages'))
  expect(requests).toHaveLength(2)
  expect(requests[1].body.request_id).not.toBe(requests[0].body.request_id)
})

it.each(['gateway502', 'network'])('keeps the idempotency identity when the result is uncertain after %s', async failure => {
  let attempts = 0
  override = (path, method) => {
    if (path.endsWith('/messages') && method === 'POST' && ++attempts === 1) {
      if (failure === 'network') throw new TypeError('连接已中断')
      return json({ error: { message: '网关暂不可用' } }, 502)
    }
  }
  const user = userEvent.setup()
  render(<KnowledgeWorkspace baseId="base-a" chatId="chat-a" onLocationChange={() => {}} />)
  await waitFor(() => expect(screen.getByRole('checkbox', { name: '选择 PPP 研究' })).toBeChecked())
  fireEvent.change(screen.getByRole('textbox', { name: '文献问题' }), { target: { value: '结果未知的问题' } })
  await user.click(screen.getByRole('button', { name: '发送' }))
  await screen.findByRole('alert')
  await user.click(screen.getByRole('button', { name: '发送' }))
  await screen.findByText('3.4 min', { selector: 'strong' })
  const requests = calls.filter(call => call.path.endsWith('/messages'))
  expect(requests).toHaveLength(2)
  expect(requests[1].body.request_id).toBe(requests[0].body.request_id)
})

it('identifies semantic-index degradation when restoring a lexical-degraded turn', async () => {
  override = path => path === '/conversations/chat-a' ? json({ ...emptyChat, messages: [answer], retrieval_mode: 'lexical_degraded' }) : undefined
  render(<KnowledgeWorkspace baseId="base-a" chatId="chat-a" onLocationChange={() => {}} />)
  expect(await screen.findByText('语义索引暂不可用，本轮使用关键词检索')).toBeInTheDocument()
})

it('commits scope changes with the expected revision for the next turn and rechecks previously opened sources', async () => {
  override = (path, method, init) => {
    if (path === '/conversations/chat-a' && method === 'GET') return json({ ...emptyChat, messages: [answer] })
    if (path.endsWith('/binding') && method === 'PATCH') return json({ ...emptyChat, messages: [answer], binding: { ...binding, ...JSON.parse(String(init.body)), binding_revision: 2 } })
  }
  const user = userEvent.setup()
  render(<KnowledgeWorkspace baseId="base-a" chatId="chat-a" onLocationChange={() => {}} />)
  await user.click(await screen.findByRole('button', { name: /S1.*PPP 研究/ }))
  const source = screen.getByRole('complementary', { name: '来源核对' })
  await within(source).findByText('原始证据 3.4 min')
  await selectOption(user, '检索范围', '整个库中已启用文献')
  fireEvent.change(screen.getByRole('textbox', { name: '文献问题' }), { target: { value: '使用更新后的范围' } })
  await user.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() => expect(calls.find(call => call.path.endsWith('/binding'))?.body).toEqual({ expected_revision: 1, scope_mode: 'all_enabled', document_ids: [], model_profile_id: 'default' }))
  await within(source).findByText('从回答回到原文')
  expect(within(source).queryByRole('link', { name: /原文/ })).not.toBeInTheDocument()
})

it('aborts a pending turn when changing conversations and never applies its late completed answer', async () => {
  const pending = deferred<Response>(); let signal: AbortSignal | null = null; let sentRequestId = ''
  override = (path, method, init) => {
    if (path === '/conversations/chat-b') return json({ ...emptyChat, id: 'chat-b', title: '另一个对话' })
    if (path.endsWith('/messages') && method === 'POST') { signal = init.signal ?? null; sentRequestId = JSON.parse(String(init.body)).request_id; return pending.promise }
  }
  const user = userEvent.setup(); const view = render(<KnowledgeWorkspace baseId="base-a" chatId="chat-a" onLocationChange={() => {}} />)
  await waitFor(() => expect(screen.getByRole('checkbox', { name: '选择 PPP 研究' })).toBeChecked())
  fireEvent.change(screen.getByRole('textbox', { name: '文献问题' }), { target: { value: '正在等待的提问' } })
  await user.click(screen.getByRole('button', { name: '发送' }))
  await screen.findByRole('button', { name: '停止生成' })
  view.rerender(<KnowledgeWorkspace baseId="base-a" chatId="chat-b" onLocationChange={() => {}} />)
  await waitFor(() => expect(signal?.aborted).toBe(true))
  await act(async () => pending.resolve(json({ conversation_id: 'chat-a', request_id: sentRequestId, status: 'completed', user_message: { id: 'old-user', role: 'user', status: 'completed', blocks: [] }, assistant_message: answer })))
  expect(screen.queryByText('3.4 min', { selector: 'strong' })).not.toBeInTheDocument()
  expect(screen.getByRole('textbox', { name: '文献问题' })).toHaveValue('')
})
