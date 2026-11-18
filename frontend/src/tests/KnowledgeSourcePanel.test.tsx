import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { citationBoxStyle } from '../api/knowledgeChat'
import type { KnowledgeSourceSelection } from '../api/knowledgeChat'
import { KnowledgeSourcePanel } from '../components/KnowledgeSourcePanel'
import { selectOption } from './selectOption'

const pdf = vi.hoisted(() => ({ getDocument: vi.fn(), render: vi.fn(), getPage: vi.fn(), worker: { workerSrc: '' } }))
vi.mock('pdfjs-dist', () => ({ getDocument: pdf.getDocument, GlobalWorkerOptions: pdf.worker }))
vi.mock('pdfjs-dist/build/pdf.worker.min.mjs?url', () => ({ default: '/assets/self-hosted-pdf-worker.mjs' }))
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); pdf.getDocument.mockReset(); pdf.render.mockReset(); pdf.getPage.mockReset() })

it.each([3, undefined, -1, 99, 1.5])('chooses requested page %s only from refreshed authoritative citation pages', async pageIndex => {
  const citation = { type: 'citation' as const, source_id: 'S1', document_id: 'doc-a', version_id: 'v1', chunk_id: 'chunk', title: 'Original', filename: 'study.txt', evidence_text: 'evidence', source_sha256: 'hash', quality_flags: [], locations: [{ page_index: 0, page_label: '1' }, { page_index: 3, page_label: '4' }] }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(String(input).includes('/citations/') ? citation : { raw_text: String(input) }), { status: 200 })))
  const selection: KnowledgeSourceSelection = { conversationId: 'chat', messageId: 'message', citation: { ...citation, locations: [{ page_index: 99, page_label: '100' }] }, pageIndex }
  render(<KnowledgeSourcePanel selection={selection} documents={[]} />)
  const expected = pageIndex === 3 ? 3 : 0
  await waitFor(() => expect(screen.getByRole('link', { name: '打开完整原文' })).toHaveAttribute('href', expect.stringContaining(`#page=${expected + 1}`)))
})

it('ignores late citation and page responses after changing selection', async () => {
  const citation = { type: 'citation' as const, source_id: 'S1', document_id: 'doc-a', version_id: 'v1', chunk_id: 'chunk', title: 'Original', filename: 'study.txt', evidence_text: 'evidence', source_sha256: 'hash', quality_flags: [], locations: [{ page_index: 0, page_label: '1' }, { page_index: 3, page_label: '4' }] }
  const pending: { url: string; resolve: (value: Response) => void }[] = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => new Promise<Response>(resolve => pending.push({ url: String(input), resolve }))))
  const selection: KnowledgeSourceSelection = { conversationId: 'chat', messageId: 'message', citation, pageIndex: 3 }
  const view = render(<KnowledgeSourcePanel selection={selection} documents={[]} />)
  const respond = async (index: number, value: unknown) => { await act(async () => pending[index].resolve(new Response(JSON.stringify(value), { status: 200 }))) }
  await respond(0, citation)
  await waitFor(() => expect(pending.some(item => item.url.endsWith('/pages/3'))).toBe(true))
  view.rerender(<KnowledgeSourcePanel selection={{ ...selection, citation: { ...citation, source_id: 'S2' }, pageIndex: 0 }} documents={[]} />)
  await respond(2, { ...citation, source_id: 'S2', title: 'Second' })
  await respond(3, { raw_text: 'current page' })
  await respond(1, { raw_text: 'stale page' })
  expect(await screen.findByText('current page')).toBeInTheDocument()
  expect(screen.queryByText('stale page')).not.toBeInTheDocument()
  view.rerender(<KnowledgeSourcePanel selection={{ ...selection, citation: { ...citation, source_id: 'S3' } }} documents={[]} />)
  view.rerender(<KnowledgeSourcePanel selection={{ ...selection, citation: { ...citation, source_id: 'S4' } }} documents={[]} />)
  await respond(5, { ...citation, source_id: 'S4', title: 'Latest' })
  await respond(4, { ...citation, source_id: 'S3', title: 'Stale' })
  expect(await screen.findByRole('heading', { name: 'Latest' })).toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Stale' })).not.toBeInTheDocument()
})

it('converts top-left PDF points before the rotated scaled viewport without applying HiDPI twice', () => {
  const inputs: number[][] = []
  const box = citationBoxStyle([10, 20, 60, 40], 100, {
    convertToViewportRectangle: rectangle => {
      inputs.push(rectangle)
      // A 90-degree viewport at 2x; devicePixelRatio is deliberately absent from CSS coordinates.
      const [x0, y0, x1, y1] = rectangle
      return [y0 * 2, x0 * 2, y1 * 2, x1 * 2]
    },
  })
  expect(inputs).toEqual([[10, 60, 60, 80]])
  expect(box).toEqual({ left: 120, top: 20, width: 40, height: 100 })
})

it('normalizes reversed viewport rectangles caused by rotation', () => {
  expect(citationBoxStyle([1, 2, 3, 4], 10, { convertToViewportRectangle: () => [80, 100, 20, 40] })).toEqual({ left: 20, top: 40, width: 60, height: 60 })
})

it('supports the PDF.js 6 viewport point conversion API', () => {
  expect(citationBoxStyle([10, 20, 60, 40], 100, { convertToViewportPoint: (x: number, y: number) => [x * 2, (100 - y) * 2] })).toEqual({ left: 20, top: 40, width: 100, height: 40 })
})

it('renders the verified immutable page with multiple boxes, changes physical pages and keeps HiDPI out of CSS geometry', async () => {
  const citation = { type: 'citation' as const, source_id: 'S1', document_id: 'doc-a', version_id: 'old-version', chunk_id: 'chunk-a', title: '原文', filename: 'study.pdf', evidence_text: '引用证据', source_sha256: 'hash', available: true, quality_flags: [], locations: [{ page_index: 0, page_label: 'i', bbox: [10, 20, 60, 40] }, { page_index: 0, page_label: 'i', bbox: [60, 40, 90, 60] }, { page_index: 2, page_label: '3', bbox: [20, 30, 80, 50] }] }
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input); requests.push(url)
    return new Response(JSON.stringify(url.includes('/citations/') ? citation : { page_index: url.endsWith('/2') ? 2 : 0, page_label: 'i', width: 200, height: 100, raw_text: '', role: 'page', coordinate_space: 'rotated_media_top_left', to_pdf_transform: [1, 0, 0, -1, 0, 100] }), { status: 200 })
  }))
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as CanvasRenderingContext2D)
  const previousDpr = window.devicePixelRatio
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2 })
  pdf.render.mockReturnValue({ promise: Promise.resolve(), cancel: vi.fn() })
  pdf.getPage.mockResolvedValue({ getViewport: ({ scale }: { scale: number }) => ({ width: 200 * scale, height: 100 * scale, convertToViewportRectangle: ([x0, y0, x1, y1]: number[]) => [x0 * scale, (100 - y1) * scale, x1 * scale, (100 - y0) * scale] }), render: pdf.render })
  pdf.getDocument.mockReturnValue({ promise: Promise.resolve({ getPage: pdf.getPage }), destroy: vi.fn() })
  try {
    render(<KnowledgeSourcePanel selection={{ conversationId: 'chat-a', messageId: 'message-a', citation }} documents={[]} />)
    await waitFor(() => expect(screen.getAllByLabelText('引用原文区域')).toHaveLength(2))
    expect(requests).toContain('/api/v1/knowledge/documents/doc-a/versions/old-version/pages/0')
    expect(pdf.worker.workerSrc).toBe('/assets/self-hosted-pdf-worker.mjs')
    expect(pdf.getPage).toHaveBeenCalledWith(1)
    expect(pdf.render.mock.calls[0][0].transform).toEqual([2, 0, 0, 2, 0, 0])
    expect(screen.getAllByLabelText('引用原文区域')[0]).toHaveStyle({ left: '10px', top: '20px', width: '50px', height: '20px' })
    const canvas = screen.getByLabelText('PDF 原文第 1 页') as HTMLCanvasElement
    expect(canvas.width).toBe(400)
    expect(canvas).toHaveStyle({ width: '200px' })
    const user = userEvent.setup()
    await user.click(screen.getByRole('combobox', { name: '引用页码' }))
    expect(screen.getAllByRole('option')).toHaveLength(2)
    await user.keyboard('{Escape}')
    await selectOption(user, '原页缩放', '200%')
    await waitFor(() => expect(screen.getAllByLabelText('引用原文区域')[0]).toHaveStyle({ left: '20px', top: '40px', width: '100px', height: '40px' }))
    await selectOption(user, '引用页码', '第 3 页（物理页 3）')
    await waitFor(() => expect(pdf.getPage).toHaveBeenCalledWith(3))
    expect(requests).toContain('/api/v1/knowledge/documents/doc-a/versions/old-version/pages/2')
  } finally { Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: previousDpr }) }
})

it.each([undefined, '', 'unverified_top_left', 'unknown_coordinate_space', 'rotated_media_top_left'])('shows the original page without invented highlight geometry for %s without a verified transform', async coordinateSpace => {
  const citation = { type: 'citation' as const, source_id: 'S1', document_id: 'doc-a', version_id: 'old-version', chunk_id: 'chunk-a', title: '原文', filename: 'study.pdf', evidence_text: '引用证据', source_sha256: 'hash', available: true, quality_flags: [], locations: [{ page_index: 0, page_label: 'i', bbox: [10, 20, 60, 40] }] }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(String(input).includes('/citations/') ? citation : { page_index: 0, page_label: 'i', width: 200, height: 100, raw_text: '', role: 'page', coordinate_space: coordinateSpace, to_pdf_transform: null }), { status: 200 })))
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as CanvasRenderingContext2D)
  pdf.render.mockReturnValue({ promise: Promise.resolve(), cancel: vi.fn() })
  pdf.getPage.mockResolvedValue({ getViewport: () => ({ width: 200, height: 100, convertToViewportPoint: (x: number, y: number) => [x, 100 - y] }), render: pdf.render })
  pdf.getDocument.mockReturnValue({ promise: Promise.resolve({ getPage: pdf.getPage }), destroy: vi.fn() })
  render(<KnowledgeSourcePanel selection={{ conversationId: 'chat-a', messageId: 'message-a', citation }} documents={[]} />)
  await waitFor(() => expect(pdf.render).toHaveBeenCalled())
  expect(await screen.findByText('此版本尚未核验页面坐标，暂不框选引用区域；重新解析后可补充定位。')).toBeInTheDocument()
  expect(screen.queryByLabelText('引用原文区域')).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: '打开完整原文' })).toBeInTheDocument()
})
