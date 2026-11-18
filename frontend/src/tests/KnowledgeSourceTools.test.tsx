import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createHash, webcrypto } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { KnowledgeCitation, KnowledgeSourceSelection } from '../api/knowledgeChat'
import type { KnowledgeDocument } from '../api/knowledge'
import { KnowledgeSourcePanel } from '../components/KnowledgeSourcePanel'
import { selectOption } from './selectOption'

const original = '  原文 α₂ 与 $E=mc^2$（式 3）\n第二行 😀  '
const translated = '  Original α₂ and $E=mc^2$ (Eq. 3)\nSecond line 😀  '
const citation: KnowledgeCitation = { type: 'citation', source_id: 'S1', document_id: 'doc-a', version_id: 'v1', chunk_id: 'chunk-a', title: 'Tao 研究', filename: 'tao.txt', evidence_text: original, source_sha256: 'original-file-hash', available: true, quality_flags: [], locations: [{ page_index: 0, page_label: 'iv' }, { page_index: 2, page_label: '3' }] }
const selected = (value = citation): KnowledgeSourceSelection => ({ conversationId: 'chat/a', messageId: 'message b', citation: value })
const translation = (overrides: Record<string, unknown> = {}) => ({ original_text: original, translated_text: translated, target_language: 'en', evidence_text_sha256: createHash('sha256').update(original).digest('hex'), model_profile: 'test-model', translation_version: 'v1', cached: false, ...overrides })
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })
let calls: { url: string; init?: RequestInit }[]
let translate: (init?: RequestInit) => Promise<Response>

beforeEach(() => {
  calls = []
  translate = async () => json(translation())
  vi.stubGlobal('crypto', webcrypto)
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); calls.push({ url, init })
    if (url.endsWith('/translation')) return translate(init)
    if (url.includes('/citations/')) return json(url.endsWith('/S2') ? { ...citation, source_id: 'S2', evidence_text: 'Second source' } : citation)
    return json({ page_index: 0, page_label: 'iv', width: 100, height: 100, raw_text: original, role: 'page' })
  }))
})
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

async function requestEnglish(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole('button', { name: '翻译片段' })
  await selectOption(user, '翻译目标语言', '英文')
  await user.click(screen.getByRole('button', { name: '翻译片段' }))
}
async function copy(user: ReturnType<typeof userEvent.setup>, item: string) {
  await user.click(screen.getByRole('button', { name: '复制引用' }))
  await user.click(screen.getByRole('menuitem', { name: item }))
}

it('translates only the identified citation, preserves literal original and shows a marked comparison', async () => {
  const user = userEvent.setup()
  render(<KnowledgeSourcePanel selection={selected()} documents={[]} />)
  await requestEnglish(user)
  await screen.findByText('机器翻译，仅供阅读参考；核验请以原文为准。')
  const call = calls.find(call => call.url.endsWith('/translation'))!
  expect(call.url).toBe('/api/v1/knowledge/conversations/chat%2Fa/messages/message%20b/citations/S1/translation')
  expect(call.init?.method).toBe('POST')
  expect(JSON.parse(String(call.init?.body))).toEqual({ target_language: 'en' })
  await user.click(screen.getByRole('tab', { name: '上下对照' }))
  expect(screen.getByLabelText('引用原文').textContent).toBe(original)
  expect(screen.getByLabelText('引用译文')).toHaveTextContent('Original')
  expect(screen.getByRole('link', { name: '打开完整原文' })).toHaveAttribute('href', '/api/v1/knowledge/documents/doc-a/original#page=1')
})

it.each([
  { label: '译文', showOriginal: false },
  { label: '上下对照', showOriginal: true },
])('opens $label before any translation and fetches the selected language', async ({ label, showOriginal }) => {
  let finish!: (response: Response) => void
  translate = () => new Promise<Response>(resolve => { finish = resolve })
  const user = userEvent.setup()
  render(<KnowledgeSourcePanel selection={selected()} documents={[]} />)
  await screen.findByRole('button', { name: '翻译片段' })
  await selectOption(user, '翻译目标语言', '英文')
  const tab = screen.getByRole('tab', { name: label })
  expect(tab).toBeEnabled()
  await user.click(tab)
  expect(tab).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('status')).toHaveTextContent('正在翻译所选片段')
  expect(calls.filter(call => call.url.endsWith('/translation'))).toHaveLength(1)
  expect(JSON.parse(String(calls.find(call => call.url.endsWith('/translation'))!.init?.body))).toEqual({ target_language: 'en' })
  await act(async () => { finish(json(translation())) })
  expect(await screen.findByLabelText('引用译文')).toHaveTextContent('Original')
  expect(screen.getByRole('tab', { name: label })).toHaveAttribute('aria-selected', 'true')
  expect(Boolean(screen.queryByLabelText('引用原文'))).toBe(showOriginal)
})

it('keeps the selected view when an in-flight tab translation finishes and reuses its result', async () => {
  let finish!: (response: Response) => void
  translate = () => new Promise<Response>(resolve => { finish = resolve })
  const user = userEvent.setup()
  render(<KnowledgeSourcePanel selection={selected()} documents={[]} />)
  await screen.findByRole('button', { name: '翻译片段' })
  await selectOption(user, '翻译目标语言', '英文')
  await user.click(screen.getByRole('tab', { name: '译文' }))
  await user.click(screen.getByRole('tab', { name: '上下对照' }))
  expect(calls.filter(call => call.url.endsWith('/translation'))).toHaveLength(1)
  expect(screen.getByRole('tab', { name: '上下对照' })).toHaveAttribute('aria-selected', 'true')
  await user.click(screen.getByRole('tab', { name: '原文' }))
  await act(async () => { finish(json(translation())) })
  expect(screen.getByRole('tab', { name: '原文' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.queryByLabelText('引用译文')).not.toBeInTheDocument()
  await user.click(screen.getByRole('tab', { name: '译文' }))
  expect(await screen.findByLabelText('引用译文')).toHaveTextContent('Original')
  expect(calls.filter(call => call.url.endsWith('/translation'))).toHaveLength(1)
})

it('retries a failed tab translation from the same tab without requiring the translate button', async () => {
  translate = async () => json({ detail: '翻译暂不可用，请重试。' }, 503)
  const user = userEvent.setup()
  render(<KnowledgeSourcePanel selection={selected()} documents={[]} />)
  await screen.findByRole('button', { name: '翻译片段' })
  await selectOption(user, '翻译目标语言', '英文')
  await user.click(screen.getByRole('tab', { name: '译文' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('翻译暂不可用')
  translate = async () => json(translation())
  await user.click(screen.getByRole('tab', { name: '译文' }))
  expect(await screen.findByLabelText('引用译文')).toHaveTextContent('Original')
  expect(screen.getByRole('tab', { name: '译文' })).toHaveAttribute('aria-selected', 'true')
})

it('writes exact Unicode, whitespace and formula text to the clipboard with visible success', async () => {
  const user = userEvent.setup()
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
  render(<KnowledgeSourcePanel selection={selected()} documents={[]} />)
  await screen.findByRole('button', { name: '翻译片段' })
  await copy(user, '复制原文')
  expect(writeText).toHaveBeenLastCalledWith(original)
  expect(screen.getByRole('status')).toHaveTextContent('已复制原文')
  await requestEnglish(user)
  await screen.findByText('机器翻译，仅供阅读参考；核验请以原文为准。')
  await copy(user, '复制译文')
  expect(writeText).toHaveBeenLastCalledWith(translated)
  await copy(user, '复制并附文献/页码')
  expect(writeText).toHaveBeenLastCalledWith(`${original}\n\n出处：[S1] Tao 研究（tao.txt），第 iv 页（物理页 1）、第 3 页（物理页 3），版本 v1`)
})

it('does not claim clipboard success and exposes exact selectable text when writing fails', async () => {
  const user = userEvent.setup()
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error('Permission denied')) } })
  render(<KnowledgeSourcePanel selection={selected()} documents={[]} />)
  await screen.findByRole('button', { name: '翻译片段' })
  await copy(user, '复制原文')
  expect(await screen.findByRole('textbox', { name: '手动复制内容' })).toHaveValue(original)
  expect(screen.getByRole('alert')).toHaveTextContent('复制失败')
  expect(screen.queryByText('已复制原文')).not.toBeInTheDocument()
})

it.each([
  { original_text: original.trim() },
  { evidence_text_sha256: '0'.repeat(64) },
  { target_language: 'zh-CN' },
  { translated_text: '' },
])('rejects mismatched or empty translation payload %j', async mismatch => {
  translate = async () => json(translation(mismatch))
  const user = userEvent.setup()
  render(<KnowledgeSourcePanel selection={selected()} documents={[]} />)
  await requestEnglish(user)
  expect(await screen.findByRole('alert')).toHaveTextContent('译文校验失败')
  expect(screen.queryByLabelText('引用译文')).not.toBeInTheDocument()
  expect(screen.getByLabelText('引用原文').textContent).toBe(original)
})

it('aborts and ignores late results after switching target language', async () => {
  let resolve!: (response: Response) => void
  translate = () => new Promise<Response>(done => { resolve = done })
  const user = userEvent.setup()
  render(<KnowledgeSourcePanel selection={selected()} documents={[]} />)
  await requestEnglish(user)
  const signal = calls.find(call => call.url.endsWith('/translation'))!.init?.signal
  await selectOption(user, '翻译目标语言', '中文')
  expect(signal?.aborted).toBe(true)
  await act(async () => { resolve(json(translation())) })
  expect(screen.queryByLabelText('引用译文')).not.toBeInTheDocument()
  expect(screen.queryByText('正在翻译所选片段…')).not.toBeInTheDocument()
})

it('aborts an active translation on source change and never displays it under another source', async () => {
  let resolve!: (response: Response) => void
  translate = () => new Promise<Response>(done => { resolve = done })
  const user = userEvent.setup()
  const view = render(<KnowledgeSourcePanel selection={selected()} documents={[]} />)
  await requestEnglish(user)
  const signal = calls.find(call => call.url.endsWith('/translation'))!.init?.signal
  view.rerender(<KnowledgeSourcePanel selection={selected({ ...citation, source_id: 'S2' })} documents={[]} />)
  await waitFor(() => expect(screen.getByLabelText('引用原文')).toHaveTextContent('Second source'))
  expect(signal?.aborted).toBe(true)
  await act(async () => { resolve(json(translation())) })
  expect(screen.queryByLabelText('引用译文')).not.toBeInTheDocument()
})

it('cancels on document disable and keeps translation and copy unavailable', async () => {
  let resolve!: (response: Response) => void
  translate = () => new Promise<Response>(done => { resolve = done })
  const user = userEvent.setup()
  const view = render(<KnowledgeSourcePanel selection={selected()} documents={[]} />)
  await requestEnglish(user)
  const signal = calls.find(call => call.url.endsWith('/translation'))!.init?.signal
  view.rerender(<KnowledgeSourcePanel selection={selected()} documents={[{ id: 'doc-a', enabled: false } as KnowledgeDocument]} />)
  await waitFor(() => expect(signal?.aborted).toBe(true))
  expect(screen.getByRole('button', { name: '翻译片段' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '复制引用' })).toBeDisabled()
  await act(async () => { resolve(json(translation())) })
  expect(screen.queryByLabelText('引用译文')).not.toBeInTheDocument()
})

it('offers cancellation and discards a cancelled result', async () => {
  let resolve!: (response: Response) => void
  translate = () => new Promise<Response>(done => { resolve = done })
  const user = userEvent.setup()
  render(<KnowledgeSourcePanel selection={selected()} documents={[]} />)
  await requestEnglish(user)
  await user.click(screen.getByRole('button', { name: '取消翻译' }))
  expect(calls.find(call => call.url.endsWith('/translation'))!.init?.signal?.aborted).toBe(true)
  await act(async () => { resolve(json(translation())) })
  expect(screen.queryByLabelText('引用译文')).not.toBeInTheDocument()
})

it.each(['', '   \n'])('disables translation and literal copy for an empty excerpt %j', async evidence => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...citation, evidence_text: evidence, locations: [] })))
  render(<KnowledgeSourcePanel selection={selected()} documents={[]} />)
  expect(await screen.findByRole('button', { name: '翻译片段' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '复制引用' })).toBeDisabled()
  expect(calls.filter(call => call.url.endsWith('/translation'))).toHaveLength(0)
})

it('disables tools for a server-unavailable citation', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...citation, available: false, unavailable_reason: 'source_unavailable_or_scope_revoked' })))
  render(<KnowledgeSourcePanel selection={selected()} documents={[]} />)
  expect(await screen.findByRole('button', { name: '翻译片段' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '复制引用' })).toBeDisabled()
})

it('keeps untranslated copy disabled and switches views without changing literal source text', async () => {
  const user = userEvent.setup()
  render(<KnowledgeSourcePanel selection={selected()} documents={[]} />)
  await screen.findByRole('button', { name: '翻译片段' })
  expect(calls.filter(call => call.url.endsWith('/translation'))).toHaveLength(0)
  await user.click(screen.getByRole('button', { name: '复制引用' }))
  expect(screen.getByRole('menuitem', { name: '复制译文' })).toHaveAttribute('aria-disabled', 'true')
  await user.keyboard('{Escape}')
  await requestEnglish(user)
  await screen.findByLabelText('引用译文')
  await user.click(screen.getByRole('tab', { name: '译文' }))
  expect(screen.queryByLabelText('引用原文')).not.toBeInTheDocument()
  await user.click(screen.getByRole('tab', { name: '原文' }))
  expect(screen.getByLabelText('引用原文').textContent).toBe(original)
  expect(screen.queryByLabelText('引用译文')).not.toBeInTheDocument()
})

it('invalidates an old translation and disables tools when a new request discovers revoked source access', async () => {
  const user = userEvent.setup()
  render(<KnowledgeSourcePanel selection={selected()} documents={[]} />)
  await requestEnglish(user)
  await screen.findByLabelText('引用译文')
  translate = async () => json({ detail: { code: 'knowledge_source_unavailable', message: '文献引用已不可用，请刷新来源。' } }, 422)
  await user.click(screen.getByRole('button', { name: '翻译片段' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '翻译片段' })).toBeDisabled())
  expect(screen.getByRole('button', { name: '复制引用' })).toBeDisabled()
  expect(screen.queryByLabelText('引用译文')).not.toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('文献引用已不可用')
})

it('aborts translation when the panel unmounts', async () => {
  let resolve!: (response: Response) => void
  translate = () => new Promise<Response>(done => { resolve = done })
  const user = userEvent.setup()
  const view = render(<KnowledgeSourcePanel selection={selected()} documents={[]} />)
  await requestEnglish(user)
  const signal = calls.find(call => call.url.endsWith('/translation'))!.init?.signal
  view.unmount()
  expect(signal?.aborted).toBe(true)
  await act(async () => { resolve(json(translation())) })
})
