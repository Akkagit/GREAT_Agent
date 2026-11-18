/// <reference types="node" />
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ReadableStream as NodeReadableStream } from 'node:stream/web'
import { MarkdownContent } from '../components/MarkdownContent'
import { KnowledgeChat } from '../components/KnowledgeChat'

// jsdom omits Web Streams. Node implements the browser interface, while its
// type declarations include additional BYOB overloads absent from lib.dom.
const ReadableStream = NodeReadableStream as unknown as typeof globalThis.ReadableStream

afterEach(() => vi.unstubAllGlobals())

it('renders inline and display mathematics with equation labels while preserving every code delimiter', () => {
  const source = String.raw`结论 $x_i^2$，兼容 \(a_1\)。

$$\frac{a}{b}\tag{12}$$

\[\begin{pmatrix}a&b\\c&d\end{pmatrix}\]

行内代码：\`\(literal\) $raw$\`

\`\`\`tex
\[literal\] $$raw$$
\`\`\``.replaceAll('\\`', '`')
  const page = render(<MarkdownContent>{source}</MarkdownContent>)
  expect(page.container.querySelectorAll('.katex')).toHaveLength(4)
  expect(page.container.querySelectorAll('.katex-display')).toHaveLength(2)
  expect(page.container.querySelector('.tag')).toHaveTextContent('(12)')
  expect(page.container.querySelector('p > code')).toHaveTextContent(String.raw`\(literal\) $raw$`)
  expect(page.container.querySelector('pre > code')?.textContent).toBe(String.raw`\[literal\] $$raw$$` + '\n')
})

it('falls back only at a malformed formula and refuses trusted HTML commands', () => {
  const page = render(<MarkdownContent>{String.raw`正常 **结论** $x^2$；坏公式 $\badcommand{x}$ 后文保留。

$\href{javascript:alert(1)}{danger}$

<script>window.bad=true</script>`}</MarkdownContent>)
  expect(page.container.querySelector('.katex')).toBeInTheDocument()
  expect(screen.getByText('结论', { selector: 'strong' })).toBeInTheDocument()
  expect(page.container).toHaveTextContent('后文保留。')
  expect(page.container.querySelector('a[href^="javascript:"], script')).toBeNull()
  expect(page.container).toHaveTextContent('\\badcommand')
})

it('preserves unclosed and nested-marker code fences without interpreting math delimiters', () => {
  const text = '```tex\n~~~\n\\[literal\\]\n\\(also literal\\)'
  const page = render(<MarkdownContent>{text}</MarkdownContent>)
  expect(page.container.querySelector('pre > code')?.textContent).toBe('~~~\n\\[literal\\]\n\\(also literal\\)\n')
  expect(page.container.querySelector('.katex')).toBeNull()
})

it('preserves a valid single-backtick code span containing a longer backtick run and TeX delimiters', () => {
  const page = render(<MarkdownContent>{'`before ``` \\(x_i\\) after`'}</MarkdownContent>)
  expect(page.container.querySelector('p > code')?.textContent).toBe('before ``` \\(x_i\\) after')
  expect(page.container.querySelector('.katex')).toBeNull()
})

const binding = { base_id: 'base-a', scope_mode: 'selected' as const, document_ids: ['doc-a'], model_profile_id: 'default', binding_revision: 1 }
const retrieval = { base_id: 'base-a', base_name: 'GNSS 文献', scope_mode: 'selected', document_count: 1, hit_count: 7, evidence_count: 3 }
const chat = { id: 'chat-a', title: '', binding, messages: [{ id: 'u', role: 'user', status: 'completed', blocks: [{ type: 'text', text: '短问题' }] }], active_run_id: null, stage: 'completed', retrieval_summary: retrieval }
const props = { baseId: 'base-a', chatId: 'chat-a', binding, profiles: [{ id: 'default', name: 'GLM', configured: true, location: 'cloud', provider: 'glm' }], onModelChange() {}, onRestoreBinding() {}, onCreated() {}, onSource() {} }
function json(value: unknown) { return new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } }) }

const formula = { latex: String.raw`N_{ij}=\frac{a_i-a_j}{\lambda}`, equation_number: '10', page_index: 3, bbox: [20, 40, 200, 90], source_sha256: 'a'.repeat(64), image_sha256: 'b'.repeat(64), model: 'local-review', status: 'locally_reviewed' }
const formulaCitation = { type: 'citation', source_id: 'S1', document_id: 'doc-a', version_id: 'version-a', chunk_id: 'chunk-a', title: 'Tao study', filename: 'tao.pdf', evidence_text: 'Authoritative literal evidence', source_sha256: 'a'.repeat(64), locations: [{ page_index: 3, page_label: '4' }], quality_flags: [], available: true }

it('marks a plain explicitly labeled core conclusion lead without rewriting answer or citation text', async () => {
  const text = '核心结论：先验改正保证兼容。\n\n兼容关系说明：详见原文[S1]。'
  const citation = { ...formulaCitation, evidence_text: '核心结论：原文引用保持不变。' }
  const onSource = vi.fn()
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...chat, messages: [{ id: 'a', role: 'assistant', status: 'completed', blocks: [{ type: 'text', text }, citation] }] })))
  const view = render(<KnowledgeChat {...props} onSource={onSource} />)
  const lead = await screen.findByText('核心结论：先验改正保证兼容。')
  expect(lead.matches('.kw-core-conclusion > p:first-child')).toBe(true)
  expect(view.container.querySelectorAll('.kw-core-conclusion')).toHaveLength(1)
  expect(lead.nextElementSibling).toHaveTextContent('兼容关系说明：详见原文[S1]。')
  fireEvent.click(screen.getByRole('button', { name: /查看原文证据/ }))
  expect(onSource).toHaveBeenCalledWith({ conversationId: 'chat-a', messageId: 'a', citation })
})

it.each(['普通首段。', '> 核心结论：引用。', '```text\n核心结论：代码。\n```', '    核心结论：缩进代码。', '**核心结论：**已有强调。', '## 核心结论\n\n已有标题。'])('preserves existing prose, quotes and Markdown structures: %s', async text => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...chat, messages: [{ id: 'a', role: 'assistant', status: 'completed', blocks: [{ type: 'text', text }] }] })))
  const view = render(<KnowledgeChat {...props} />)
  await screen.findByRole('article', { name: '文献回答' })
  expect(view.container.querySelector('.kw-core-conclusion')).toBeNull()
  if (text.startsWith('>')) expect(view.container.querySelector('blockquote')).toBeInTheDocument()
  if (text.includes('代码')) expect(view.container.querySelector('pre > code')).toBeInTheDocument()
  if (text.startsWith('**')) expect(view.container.querySelector('strong')).toBeInTheDocument()
  if (text.startsWith('##')) expect(view.container.querySelector('h2')).toBeInTheDocument()
})

it('restores locally reviewed page formulas next to their citation and opens the unchanged original identity', async () => {
  const citation = { ...formulaCitation, locations: [{ page_index: 0, page_label: '1' }, ...formulaCitation.locations], formula_transcriptions: [formula] }
  const onSource = vi.fn()
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...chat, messages: [{ id: 'a', role: 'assistant', status: 'completed', blocks: [{ type: 'text', text: '请结合原页核对。' }, citation] }] })))
  render(<KnowledgeChat {...props} onSource={onSource} />)
  const section = await screen.findByRole('region', { name: '原文公式 (10)' })
  expect(within(section).getByText('原文公式 (10) · 已本地核对')).toBeInTheDocument()
  expect(section.querySelector('.katex-display .katex')).toBeInTheDocument()
  fireEvent.click(within(section).getByRole('button', { name: /S1.*第 4 页.*原文/ }))
  expect(onSource).toHaveBeenCalledWith({ conversationId: 'chat-a', messageId: 'a', citation, pageIndex: 3 })
  expect(onSource.mock.calls[0][0].citation.evidence_text).toBe('Authoritative literal evidence')
  fireEvent.click(screen.getByRole('button', { name: /查看原文证据/ }))
  expect(onSource).toHaveBeenLastCalledWith({ conversationId: 'chat-a', messageId: 'a', citation })
})

it('labels local PDF extraction as needing review', async () => {
  const citation = { ...formulaCitation, formula_transcriptions: [{ ...formula, model: 'local_pdf_text_v1', status: 'machine_transcription' }] }
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...chat, messages: [{ id: 'a', role: 'assistant', status: 'completed', blocks: [citation] }] })))
  render(<KnowledgeChat {...props} />)
  expect(await screen.findByText('原文公式 (10) · 本地提取，需核对原页')).toBeInTheDocument()
})

it('deduplicates page formulas across citations, warns for machine transcriptions and ignores invalid structures', async () => {
  const machine = { ...formula, equation_number: '(11)', status: 'machine_transcription' }
  const citations = [
    { ...formulaCitation, formula_transcriptions: [{ ...formula, status: 'machine_transcription' }, machine, { ...formula, equation_number: '99', latex: '' }, { ...formula, equation_number: '98', source_sha256: 'c'.repeat(64) }, { ...formula, equation_number: '97', bbox: [0, 0, 0, 0] }, { ...formula, equation_number: '95', status: 'unknown' }, { ...formula, equation_number: '94', image_sha256: '' }, { ...formula, equation_number: '93', page_index: -1 }, null] },
    { ...formulaCitation, source_id: 'S2', formula_transcriptions: [formula] },
    { ...formulaCitation, source_id: 'S3', available: false, formula_transcriptions: [{ ...formula, equation_number: '96' }] },
  ]
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...chat, messages: [{ id: 'a', role: 'assistant', status: 'completed', blocks: citations }] })))
  render(<KnowledgeChat {...props} />)
  await screen.findByRole('region', { name: '原文公式 (10)' })
  expect(screen.getAllByRole('region', { name: /原文公式/ })).toHaveLength(2)
  expect(screen.getByText('原文公式 (10) · 已本地核对')).toBeInTheDocument()
  expect(screen.getByText('原文公式 (11) · 机器转录，需核对原页')).toBeInTheDocument()
  expect(screen.queryByText(/原文公式 \((?:99|98|97|96)\)/)).toBeNull()
})

it('restores real retrieval counts and presents a short question without a separate user heading', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json(chat)))
  render(<KnowledgeChat {...props} />)
  const question = await screen.findByRole('article', { name: '你的问题' })
  expect(within(question).queryByRole('heading')).toBeNull()
  expect(question).toHaveTextContent('短问题')
  expect(await screen.findByText(/GNSS 文献.*所选 1 篇.*命中 7.*证据 3/)).toBeInTheDocument()
})

it('shows the submitted question immediately, hides the empty state, and lets the successful server message replace it once', async () => {
  let finish!: (value: Response) => void
  let requestId = ''
  const pending = new Promise<Response>(resolve => { finish = resolve })
  vi.stubGlobal('fetch', vi.fn(async (input: string, init: RequestInit = {}) => {
    if (input.endsWith('/events')) return new Response(new ReadableStream(), { headers: { 'Content-Type': 'text/event-stream' } })
    if (input.endsWith('/messages')) { requestId = JSON.parse(String(init.body)).request_id; return pending }
    return json({ ...chat, messages: [] })
  }))
  render(<KnowledgeChat {...props} />)
  await screen.findByText('从文献证据开始提问')
  const input = screen.getByRole('textbox', { name: '文献问题' })
  fireEvent.change(input, { target: { value: '立刻可见的问题' } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  const bubble = screen.getByRole('article', { name: '你的问题' })
  expect(bubble).toHaveTextContent('立刻可见的问题')
  expect(bubble).toHaveTextContent('等待回答')
  expect(screen.queryByText('从文献证据开始提问')).toBeNull()
  expect(input).toHaveValue('')
  fireEvent.change(input, { target: { value: '等待时写下一条草稿' } })
  await act(async () => finish(json({ conversation_id: 'chat-a', request_id: requestId, status: 'completed', user_message: { id: 'server-user', role: 'user', request_id: requestId, status: 'completed', blocks: [{ type: 'text', text: '立刻可见的问题' }] }, assistant_message: { id: 'a', role: 'assistant', status: 'completed', blocks: [{ type: 'text', text: '已收到' }] } })))
  expect(screen.getAllByRole('article', { name: '你的问题' })).toHaveLength(1)
  expect(screen.queryByText('等待回答')).toBeNull()
  expect(screen.getByText('已收到')).toBeInTheDocument()
  expect(input).toHaveValue('等待时写下一条草稿')
})

it('keeps the first pending question when a newly created conversation receives its routed chat id', async () => {
  const onCreated = vi.fn()
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    if (input.endsWith('/events')) return new Response(new ReadableStream(), { headers: { 'Content-Type': 'text/event-stream' } })
    if (input.endsWith('/messages')) return new Promise<Response>(() => {})
    return json({ ...chat, messages: [] })
  }))
  const view = render(<KnowledgeChat {...props} chatId={undefined} onCreated={onCreated} />)
  fireEvent.change(screen.getByRole('textbox', { name: '文献问题' }), { target: { value: '首轮问题' } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() => expect(onCreated).toHaveBeenCalledOnce())
  view.rerender(<KnowledgeChat {...props} onCreated={onCreated} />)
  expect(screen.getByRole('article', { name: '你的问题' })).toHaveTextContent('首轮问题')
  expect(screen.queryByText('从文献证据开始提问')).toBeNull()
})

it('restores a rejected question to its draft and removes its pending bubble without claiming success', async () => {
  let finish!: (value: Response) => void
  const pending = new Promise<Response>(resolve => { finish = resolve })
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    if (input.endsWith('/events')) return new Response(new ReadableStream(), { headers: { 'Content-Type': 'text/event-stream' } })
    if (input.endsWith('/messages')) return pending
    return json({ ...chat, messages: [] })
  }))
  render(<KnowledgeChat {...props} />)
  await screen.findByText('从文献证据开始提问')
  const input = screen.getByRole('textbox', { name: '文献问题' })
  fireEvent.change(input, { target: { value: '失败后保留的问题' } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  expect(screen.getByRole('article', { name: '你的问题' })).toHaveTextContent('失败后保留的问题')
  await act(async () => finish(new Response(JSON.stringify({ error: { message: '暂时无法发送' } }), { status: 503, headers: { 'Content-Type': 'application/json' } })))
  expect(screen.getByRole('alert')).toHaveTextContent('暂时无法发送')
  expect(input).toHaveValue('失败后保留的问题')
  expect(screen.queryByRole('article', { name: '你的问题' })).toBeNull()
})

it('clears the pending bubble on cancel and on conversation changes and ignores the old completed turn', async () => {
  let finish!: (value: Response) => void
  const pending = new Promise<Response>(resolve => { finish = resolve })
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    if (input.endsWith('/events')) return new Response(new ReadableStream(), { headers: { 'Content-Type': 'text/event-stream' } })
    if (input.endsWith('/messages')) return pending
    if (input.endsWith('/cancel')) return json({ status: 'cancelled' })
    return json({ ...chat, id: input.endsWith('chat-b') ? 'chat-b' : 'chat-a', messages: [] })
  }))
  const view = render(<KnowledgeChat {...props} />)
  await screen.findByText('从文献证据开始提问')
  fireEvent.change(screen.getByRole('textbox', { name: '文献问题' }), { target: { value: '取消的问题' } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  expect(screen.getByRole('article', { name: '你的问题' })).toHaveTextContent('取消的问题')
  fireEvent.click(screen.getByRole('button', { name: '停止生成' }))
  await waitFor(() => expect(screen.queryByRole('article', { name: '你的问题' })).toBeNull())
  expect(screen.getByRole('textbox', { name: '文献问题' })).toHaveValue('取消的问题')
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  expect(screen.getByRole('article', { name: '你的问题' })).toBeInTheDocument()
  view.rerender(<KnowledgeChat {...props} chatId="chat-b" />)
  await screen.findByText('从文献证据开始提问')
  expect(screen.queryByRole('article', { name: '你的问题' })).toBeNull()
  expect(screen.getByRole('textbox', { name: '文献问题' })).toHaveValue('')
  await act(async () => finish(json({ status: 'completed', conversation_id: 'chat-a', request_id: 'old', user_message: { id: 'old-user', role: 'user', blocks: [{ type: 'text', text: '旧问题' }] }, assistant_message: null })))
  expect(screen.queryByText('旧问题')).toBeNull()
})

it('captures a fast real retrieval event, stops the stream on completion and ignores late data after switching chats', async () => {
  let stream: ReadableStreamDefaultController<Uint8Array> | undefined
  let aborted = false
  let requestId = ''
  let finish!: (value: Response) => void
  const pending = new Promise<Response>(resolve => { finish = resolve })
  vi.stubGlobal('fetch', vi.fn(async (input: string, init: RequestInit = {}) => {
    if (input.endsWith('/events')) {
      init.signal?.addEventListener('abort', () => { aborted = true })
      return new Response(new ReadableStream({ start(controller) { stream = controller } }), { headers: { 'Content-Type': 'text/event-stream' } })
    }
    if (input.endsWith('/messages')) { requestId = JSON.parse(String(init.body)).request_id; return pending }
    return json(chat)
  }))
  const view = render(<KnowledgeChat {...props} />)
  await screen.findByText('短问题')
  fireEvent.change(screen.getByRole('textbox', { name: '文献问题' }), { target: { value: '检索' } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() => expect(stream).toBeDefined())
  await act(async () => stream!.enqueue(new TextEncoder().encode(`id: 1\nevent: retrieving\ndata: ${JSON.stringify({ request_id: requestId, retrieval: { ...retrieval, hit_count: 9 } })}\n\n`)))
  expect(await screen.findByText(/正在检索「GNSS 文献」.*所选 1 篇.*命中 9/)).toBeInTheDocument()
  view.rerender(<KnowledgeChat {...props} chatId="chat-b" />)
  await waitFor(() => expect(aborted).toBe(true))
  await act(async () => finish(json({ status: 'completed', conversation_id: 'chat-a', request_id: 'old', assistant_message: null })))
  expect(screen.queryByText(/命中 9/)).toBeNull()
})

it('disconnects a restored active conversation at its terminal event and never reconnects', async () => {
  let stream: ReadableStreamDefaultController<Uint8Array> | undefined
  let aborted = false
  let streamRequests = 0
  let details = 0
  let finish!: (value: Response) => void
  const finalDetail = new Promise<Response>(resolve => { finish = resolve })
  vi.stubGlobal('fetch', vi.fn(async (input: string, init: RequestInit = {}) => {
    if (input.endsWith('/events')) {
      streamRequests++
      init.signal?.addEventListener('abort', () => { aborted = true })
      return new Response(new ReadableStream({ start(controller) { stream = controller } }), { headers: { 'Content-Type': 'text/event-stream' } })
    }
    if (++details === 1) return json({ ...chat, active_run_id: 'run-a', stage: 'retrieving' })
    return new Promise<Response>((resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
      void finalDetail.then(resolve)
    })
  }))
  render(<KnowledgeChat {...props} />)
  await waitFor(() => expect(stream).toBeDefined())
  await act(async () => stream!.enqueue(new TextEncoder().encode(`id: 2\nevent: completed\ndata: ${JSON.stringify({ run_id: 'run-a', retrieval })}\n\n`)))
  await waitFor(() => expect(aborted).toBe(true))
  expect(screen.queryByRole('button', { name: '停止生成' })).toBeNull()
  expect(streamRequests).toBe(1)
  await act(async () => finish(json({ ...chat, messages: [...chat.messages, { id: 'a', role: 'assistant', status: 'completed', blocks: [{ type: 'text', text: '完成后的回答' }] }] })))
  expect(await screen.findByText('完成后的回答')).toBeInTheDocument()
})
