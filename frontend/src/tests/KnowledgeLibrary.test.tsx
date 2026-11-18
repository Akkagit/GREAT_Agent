import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { KnowledgeLibrary } from '../components/KnowledgeLibrary'
import { selectOption } from './selectOption'

const baseA = { id: 'base-a', name: 'GNSS 文献', created_at: '2026-11-12T00:00:00Z' }
const baseB = { id: 'base-b', name: '观测资料', created_at: '2026-11-12T00:00:00Z' }
const job = { id: 'job-a', document_id: 'doc-a', version_id: 'version-a', status: 'queued', error_code: null, error_message: null as string | null, warnings: [] as string[] }
const documentA = { id: 'doc-a', base_id: 'base-a', filename: '论文 α.pdf', title: 'PPP 研究', enabled: true, active_version_id: 'version-a', status: 'completed', latest_job: { ...job, status: 'completed' }, created_at: '2026-11-12T00:00:00Z' }
const evidence = { chunk_id: 'chunk-a', document_id: 'doc-a', version_id: 'version-a', title: 'PPP 研究', text: 'B2b corrections improve precise point positioning.', section: '3. Results', locations: [{ page_index: 3, page_label: 'iv', bbox: [10, 20, 300, 100] }], quality_flags: ['formula_unverified'], score: 1.2 }

type Handler = (path: string, method: string, init: RequestInit) => Response | Promise<Response> | undefined
let bases = [baseA]
let documents = [documentA]
let handler: Handler
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }) }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }

beforeEach(() => {
  bases = [baseA]; documents = [documentA]; handler = () => undefined
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), 'http://localhost')
    const path = `${url.pathname}${url.search}`.replace('/api/v1/knowledge', '')
    const method = init.method ?? 'GET'
    const overridden = handler(path, method, init)
    if (overridden) return await overridden
    if (path === '/bases' && method === 'GET') return json({ items: bases })
    if (path === '/bases/base-a/documents' && method === 'GET') return json({ items: documents })
    if (path === '/bases/base-b/documents' && method === 'GET') return json({ items: [] })
    if (path === '/documents/doc-a' && method === 'GET') return json(documents[0])
    throw new Error(`Unexpected request: ${method} ${path}`)
  }))
})

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

it('creates a named library and selects it for documents', async () => {
  const user = userEvent.setup()
  handler = (path, method, init) => {
    if (path === '/bases' && method === 'POST') {
      const input = JSON.parse(String(init.body))
      bases = [...bases, { ...baseB, name: input.name }]
      return json(bases[1], 201)
    }
  }
  render(<KnowledgeLibrary open onClose={() => {}} />)
  await screen.findByText('PPP 研究')
  await user.type(screen.getByRole('textbox', { name: '新知识库名称' }), '  观测资料  ')
  await user.click(screen.getByRole('button', { name: '新建知识库' }))
  expect(await screen.findByText('此知识库还没有文献')).toBeInTheDocument()
  expect(screen.getByRole('combobox', { name: '知识库' }).closest('label')).toHaveTextContent('观测资料')
  expect(screen.queryByText('PPP 研究')).not.toBeInTheDocument()
})

it('refreshes active-version page and chunk counts after a completed upload instead of keeping unpublished zero counts', async () => {
  const pendingDocument = { ...documentA, active_version_id: null, status: 'parsing', latest_job: { ...job, status: 'parsing' }, page_count: 0, chunk_count: 0 }
  handler = (path, method) => {
    if (path === '/bases/base-a/documents') return json({ items: [pendingDocument] })
    if (path === '/jobs/job-a') return json({ ...job, status: 'completed' })
    if (path === '/documents/doc-a' && method === 'GET') return json({ ...documentA, page_count: 16, chunk_count: 91 })
  }
  render(<KnowledgeLibrary open onClose={() => {}} />)
  expect(await screen.findByText('16 页 · 91 个切片')).toBeInTheDocument()
  expect(screen.queryByText('0 页 · 0 个切片')).not.toBeInTheDocument()
})

it('waits for the initial library list before allowing creation so its snapshot cannot erase a new library', async () => {
  const user = userEvent.setup(); const initial = deferred<Response>()
  handler = (path, method, init) => {
    if (path === '/bases' && method === 'GET') return initial.promise
    if (path === '/bases' && method === 'POST') return json({ ...baseB, name: JSON.parse(String(init.body)).name }, 201)
  }
  render(<KnowledgeLibrary open onClose={() => {}} />)
  await user.type(screen.getByRole('textbox', { name: '新知识库名称' }), '观测资料')
  expect(screen.getByRole('button', { name: '新建知识库' })).toBeDisabled()
  await act(async () => initial.resolve(json({ items: [baseA] })))
  await screen.findByText('PPP 研究')
  await user.click(screen.getByRole('button', { name: '新建知识库' }))
  expect(await screen.findByText('此知识库还没有文献')).toBeInTheDocument()
  expect(screen.getByRole('combobox', { name: '知识库' }).closest('label')).toHaveTextContent('观测资料')
})

it.each([
  ['completed', [], null, '可检索'],
  ['completed_with_warnings', ['reading_order_heuristic'], null, '可检索 · 请核对原文'],
  ['failed', [], '扫描件需要 OCR', '入库失败'],
] as const)('uploads raw file bytes and follows the durable job to %s', async (status, warnings, errorMessage, expected) => {
  const user = userEvent.setup()
  documents = []
  let accepted = false; let polls = 0
  const file = new File(['%PDF local data'], '论文 α.pdf', { type: 'application/pdf' })
  handler = (path, method, init) => {
    if (path === `/bases/base-a/documents?filename=${encodeURIComponent(file.name)}` && method === 'POST') {
      // Reject a JSON or multipart transport: the browser must send the original File.
      if (init.body !== file || new Headers(init.headers).get('Content-Type') !== 'application/pdf') return json({ error: { message: '上传格式错误' } }, 415)
      accepted = true
      return json({ document: { ...documentA, active_version_id: null, status: 'queued' }, job, duplicate: false }, 202)
    }
    if (path === '/jobs/job-a') {
      polls++
      if (polls === 1) return json({ ...job, status: 'parsing' })
      documents = [{ ...documentA, status, latest_job: { ...job, status, warnings: [...warnings], error_message: errorMessage } }]
      return json(documents[0].latest_job)
    }
  }
  render(<KnowledgeLibrary open onClose={() => {}} />)
  await screen.findByText('此知识库还没有文献')
  await user.upload(screen.getByLabelText('上传文献'), file)
  expect(accepted).toBe(true)
  expect(await screen.findByText('解析中')).toBeInTheDocument()
  expect(screen.queryByText('可检索')).not.toBeInTheDocument()
  expect(await screen.findByText(expected, {}, { timeout: 5000 })).toBeInTheDocument()
  if (errorMessage) expect(screen.getByText(errorMessage)).toBeInTheDocument()
  if (warnings.length) expect(screen.getByText(/阅读顺序/)).toBeInTheDocument()
})

it('shows upload errors without pretending the document was ingested', async () => {
  const user = userEvent.setup(); documents = []
  handler = (path, method) => path.startsWith('/bases/base-a/documents?') && method === 'POST'
    ? json({ error: { code: 'upload_too_large', message: '文献超过 50 MiB' } }, 413) : undefined
  render(<KnowledgeLibrary open onClose={() => {}} />)
  await screen.findByText('此知识库还没有文献')
  await user.upload(screen.getByLabelText('上传文献'), new File(['big'], 'big.pdf', { type: 'application/pdf' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('文献超过 50 MiB')
  expect(screen.queryByText('入库完成')).not.toBeInTheDocument()
})

it('shows a terminal job failure even when the document refresh is temporarily unavailable', async () => {
  documents = [{ ...documentA, status: 'parsing', latest_job: { ...job, status: 'parsing' } }]
  handler = (path, method) => {
    if (path === '/jobs/job-a') return json({ ...job, status: 'failed', error_message: '文献未提取到可检索文字' })
    if (path === '/documents/doc-a' && method === 'GET') return json({ error: { message: '详情暂时不可用' } }, 503)
  }
  render(<KnowledgeLibrary open onClose={() => {}} />)
  expect(await screen.findByText('入库失败')).toBeInTheDocument()
  expect(screen.getByText('文献未提取到可检索文字')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '查看切片 PPP 研究' })).toBeEnabled()
})

it('searches this library lexically and opens the exact evidence page and version', async () => {
  const user = userEvent.setup()
  handler = (path, method, init) => {
    if (path === '/bases/base-a/search' && method === 'POST') {
      const input = JSON.parse(String(init.body))
      return json({ mode: 'lexical', items: input.query === 'B2b corrections' && input.limit === 8 ? [evidence] : [] })
    }
    if (path === '/chunks/chunk-a') return json({ ...evidence, id: evidence.chunk_id, text: '完整的 B2b 证据切片。' })
  }
  render(<KnowledgeLibrary open onClose={() => {}} />)
  await screen.findByText('PPP 研究')
  await user.type(screen.getByRole('textbox', { name: '检索文献' }), 'B2b corrections')
  await user.click(screen.getByRole('button', { name: '检索' }))
  const result = await screen.findByRole('article', { name: '检索证据 1' })
  expect(result).toHaveTextContent('B2b corrections improve precise point positioning.')
  expect(result).toHaveTextContent('公式尚未核验')
  expect(within(result).getByRole('link', { name: '原文第 iv 页' })).toHaveAttribute('href', '/api/v1/knowledge/documents/doc-a/original#page=4')
  await user.click(within(result).getByRole('button', { name: '查看完整切片' }))
  expect(await screen.findByRole('region', { name: '切片详情' })).toHaveTextContent('完整的 B2b 证据切片。')
  expect(screen.getByText(/当前使用词法检索/)).toBeInTheDocument()
})

it('browses the active immutable version and removes evidence immediately after disabling it', async () => {
  const user = userEvent.setup()
  handler = (path, method, init) => {
    if (path === '/documents/doc-a/chunks?version_id=version-a&offset=0&limit=20') return json({ items: [{ ...evidence, id: 'chunk-a' }], total: 1 })
    if (path === '/documents/doc-a' && method === 'PATCH') {
      documents = [{ ...documentA, enabled: JSON.parse(String(init.body)).enabled }]
      return json(documents[0])
    }
  }
  render(<KnowledgeLibrary open onClose={() => {}} />)
  await user.click(await screen.findByRole('button', { name: '查看切片 PPP 研究' }))
  expect(await screen.findByRole('region', { name: '文献切片' })).toHaveTextContent(evidence.text)
  await user.click(screen.getByRole('button', { name: '停用 PPP 研究' }))
  expect(await screen.findByRole('button', { name: '启用 PPP 研究' })).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: /原文/ })).not.toBeInTheDocument()
  expect(screen.queryByRole('region', { name: '文献切片' })).not.toBeInTheDocument()
})

it('requests a fresh parse with the selected local parser and displays a failed retry while retaining old chunks', async () => {
  const user = userEvent.setup()
  handler = (path, method, init) => {
    if (path === '/documents/doc-a/reparse' && method === 'POST') {
      const input = JSON.parse(String(init.body))
      if (input.parser !== 'docling' || !/^[0-9a-f-]{36}$/i.test(input.request_id)) return json({ error: { message: '重解析请求无效' } }, 400)
      return json({ document: documentA, job: { ...job, version_id: 'version-b' }, duplicate: false }, 202)
    }
    if (path === '/jobs/job-a') return json({ ...job, version_id: 'version-b', status: 'failed', error_code: 'parser_unavailable', error_message: '本地 Docling 模型未配置' })
    if (path === '/documents/doc-a/chunks?version_id=version-a&offset=0&limit=20') return json({ items: [{ ...evidence, id: 'chunk-a' }], total: 1 })
  }
  render(<KnowledgeLibrary open onClose={() => {}} />)
  await screen.findByText('PPP 研究')
  await selectOption(user, '重解析方式 PPP 研究', 'Docling（需本地模型）')
  await user.click(screen.getByRole('button', { name: '重解析 PPP 研究' }))
  expect(await screen.findByText('本地 Docling 模型未配置')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '查看切片 PPP 研究' }))
  expect(await screen.findByRole('region', { name: '文献切片' })).toHaveTextContent(evidence.text)
})

it('ignores late document and search responses after switching libraries', async () => {
  const user = userEvent.setup(); bases = [baseA, baseB]
  const pendingSearch = deferred<Response>(); const pendingDocuments = deferred<Response>()
  let documentRequests = 0
  handler = (path, method) => {
    if (path === '/bases/base-a/documents' && method === 'GET') return ++documentRequests > 1 ? pendingDocuments.promise : undefined
    if (path === '/bases/base-a/search') return pendingSearch.promise
  }
  render(<KnowledgeLibrary open onClose={() => {}} />)
  await screen.findByText('PPP 研究')
  await user.type(screen.getByRole('textbox', { name: '检索文献' }), 'B2b')
  await user.click(screen.getByRole('button', { name: '检索' }))
  await selectOption(user, '知识库', '观测资料')
  expect(await screen.findByText('此知识库还没有文献')).toBeInTheDocument()
  await act(async () => pendingSearch.resolve(json({ mode: 'lexical', items: [evidence] })))
  expect(screen.queryByText(evidence.text)).not.toBeInTheDocument()
  await selectOption(user, '知识库', 'GNSS 文献')
  await selectOption(user, '知识库', '观测资料')
  await act(async () => pendingDocuments.resolve(json({ items: documents })))
  expect(screen.queryByText('PPP 研究')).not.toBeInTheDocument()
})

it.each(['close', 'unmount'] as const)('cancels an outstanding poll on %s and never schedules another', async action => {
  documents = [{ ...documentA, status: 'parsing', latest_job: { ...job, status: 'parsing' } }]
  const pending = deferred<Response>(); let signal: AbortSignal | null = null; let polls = 0
  handler = (path, _method, init) => {
    if (path === '/jobs/job-a') { polls++; signal = init.signal ?? null; return pending.promise }
  }
  const view = render(<KnowledgeLibrary open onClose={() => {}} />)
  await waitFor(() => expect(polls).toBe(1))
  if (action === 'close') view.rerender(<KnowledgeLibrary open={false} onClose={() => {}} />)
  else view.unmount()
  expect(signal!.aborted).toBe(true)
  vi.useFakeTimers()
  await act(async () => { pending.resolve(json({ ...job, status: 'parsing' })); await vi.advanceTimersByTimeAsync(10_000) })
  expect(polls).toBe(1)
})

it('clears pending search results before a new query and does not let the old reply win', async () => {
  const old = deferred<Response>(); const user = userEvent.setup()
  handler = (path, _method, init) => {
    if (path === '/bases/base-a/search') return JSON.parse(String(init.body)).query === 'old' ? old.promise : json({ mode: 'lexical', items: [{ ...evidence, text: '新检索结果' }] })
  }
  render(<KnowledgeLibrary open onClose={() => {}} />)
  const input = await screen.findByRole('textbox', { name: '检索文献' })
  fireEvent.change(input, { target: { value: 'old' } }); fireEvent.submit(input.closest('form')!)
  fireEvent.change(input, { target: { value: 'new' } }); fireEvent.submit(input.closest('form')!)
  expect(await screen.findByText('新检索结果')).toBeInTheDocument()
  await act(async () => old.resolve(json({ mode: 'lexical', items: [evidence] })))
  expect(screen.queryByText(evidence.text)).not.toBeInTheDocument()
  await user.clear(input)
})
