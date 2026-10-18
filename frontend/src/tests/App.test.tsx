import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../App'
import entryHtml from '../../index.html?raw'

const apiMock = vi.hoisted(() => ({
  createExperiment: vi.fn(),
}))

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>()
  return { ...original, api: { ...original.api, ...apiMock } }
})

vi.mock('../pages/ExperimentWorkspace', () => ({
  ExperimentWorkspace: ({ experimentId }: { experimentId: string }) => (
    <div>实验工作区 {experimentId}</div>
  ),
}))

vi.mock('../pages/RunDashboard', () => ({
  RunDashboard: ({ experimentId }: { experimentId: string }) => (
    <div>运行看板 {experimentId}</div>
  ),
}))

describe('App entry', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.history.replaceState({}, '', '/')
    apiMock.createExperiment.mockResolvedValue({
      id: 'exp-created',
      status: 'draft',
      current_revision: 1,
    })
  })

  it('creates the first experiment instead of opening a fake local id', async () => {
    const user = userEvent.setup()
    render(<App />)

    expect(screen.getByRole('heading', { name: '建立首个 PPP-FLT 实验' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '创建实验并进入规划台' }))

    expect(await screen.findByText('实验工作区 exp-created')).toBeInTheDocument()
    expect(window.location.search).toBe('?experiment=exp-created')
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
})
