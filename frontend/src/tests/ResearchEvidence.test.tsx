import { act, render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { ResearchEvidencePanel } from '../components/ResearchEvidencePanel'
import type { ResearchEvidence } from '../api/research'

const mock = vi.hoisted(() => ({ evidence: vi.fn() }))
vi.mock('../api/research', () => ({ researchApi: mock }))
beforeEach(() => vi.resetAllMocks())
const selection = { sessionId: 's1', runId: 'run-1', alias: 'L1' }
const evidence: ResearchEvidence = { id: 'stable', alias: 'L1', kind: 'literature', title: 'Reset 论文', text: '固定版本原文', locator: { document_id: 'paper', version_id: 'version-1', locations: [{ page_index: 7, page_label: '12' }] }, sha256: 'hash', quality_flags: [], available: true }

it('shows the physical and printed literature page and opens the cited PDF page', async () => {
  mock.evidence.mockResolvedValue(evidence)
  render(<ResearchEvidencePanel selection={selection} summary={[]} />)
  expect(await screen.findByText('PDF 第 8 页 · 印刷页码 12')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '打开文献原页' })).toHaveAttribute('href', '/api/v1/knowledge/documents/paper/original#page=8')
  expect(screen.getByText('version-1')).toBeInTheDocument()
})

it('does not display cached source text when the server reports the evidence unavailable', async () => {
  mock.evidence.mockResolvedValue({ ...evidence, available: false, text: '不应显示的旧缓存' })
  render(<ResearchEvidencePanel selection={selection} summary={[]} />)
  expect(await screen.findByText('该来源当前不可用，请重新核对材料权限与版本。')).toBeInTheDocument()
  expect(screen.queryByText('不应显示的旧缓存')).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: '打开文献原页' })).not.toBeInTheDocument()
})

it('does not replace a newer selection with the same alias from a late old run response', async () => {
  let resolveOld: ((value: ResearchEvidence) => void) | undefined
  mock.evidence.mockImplementation((runId: string) => runId === 'run-1' ? new Promise(resolve => { resolveOld = resolve }) : Promise.resolve({ ...evidence, text: '新轮的原文' }))
  const page = render(<ResearchEvidencePanel selection={selection} summary={[]} />)
  page.rerender(<ResearchEvidencePanel selection={{ ...selection, runId: 'run-2' }} summary={[]} />)
  expect(await screen.findByText('新轮的原文')).toBeInTheDocument()
  await act(async () => resolveOld?.({ ...evidence, text: '旧轮原文' }))
  expect(screen.getByText('新轮的原文')).toBeInTheDocument(); expect(screen.queryByText('旧轮原文')).not.toBeInTheDocument()
})
