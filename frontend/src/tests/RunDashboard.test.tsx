import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { RunDashboard } from '../pages/RunDashboard'

const apiMock = vi.hoisted(() => ({
  listTasks: vi.fn(), getTask: vi.fn(), getTaskLog: vi.fn(), getTaskResults: vi.fn(),
  retryTask: vi.fn(), stopExperiment: vi.fn(), openTaskEvents: vi.fn(),
}))

const streamMock = vi.hoisted(() => ({
  listeners: new Map<string, (event: unknown) => void>(),
  disconnectors: new Map<string, () => void>(),
  emit(taskId: string, event: unknown) { this.listeners.get(taskId)?.(event) },
  disconnect(taskId: string) { this.disconnectors.get(taskId)?.() },
}))

vi.mock('../api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/client')>()
  return { ...original, api: { ...original.api, ...apiMock } }
})

const tasks = [
  { id: 't1', experiment_id: 'exp-1', year: 2024, doy: 350, status: 'failed' },
  { id: 't2', experiment_id: 'exp-1', year: 2024, doy: 351, status: 'succeeded' },
]

const taskDetail = {
  ...tasks[0],
  active_attempt_id: 'a2',
  attempts: [
    { id: 'a1', number: 1, status: 'failed', error_code: 'solver_exit', exit_code: 1 },
    { id: 'a2', number: 2, status: 'failed', error_code: 'missing_output', exit_code: 0 },
  ],
}

describe('RunDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks(); streamMock.listeners.clear(); streamMock.disconnectors.clear()
    apiMock.listTasks.mockResolvedValue(tasks)
    apiMock.getTask.mockImplementation(async (id: string) => id === 't1' ? taskDetail : { ...tasks[1], active_attempt_id: 'b1', attempts: [{ id: 'b1', number: 1, status: 'succeeded', error_code: null, exit_code: 0 }] })
    apiMock.getTaskLog.mockImplementation(async (_taskId: string, attemptId: string) => attemptId === 'a1' ? 'attempt-one log' : 'attempt-two missing output')
    apiMock.getTaskResults.mockResolvedValue([])
    apiMock.retryTask.mockResolvedValue({ status: 'pending' })
    apiMock.stopExperiment.mockResolvedValue({ status: 'stopping' })
    apiMock.openTaskEvents.mockImplementation((taskId: string, lastId: string | undefined, onEvent: (event: unknown) => void, onDisconnect: () => void) => {
      void lastId
      streamMock.listeners.set(taskId, onEvent); streamMock.disconnectors.set(taskId, onDisconnect)
      return () => { streamMock.listeners.delete(taskId); streamMock.disconnectors.delete(taskId) }
    })
  })

  it('updates daily task state from SSE without duplicating events', async () => {
    render(<RunDashboard experimentId="exp-1" />)
    await screen.findByText('2024-350')

    streamMock.emit('t1', { id: '12', event: 'task_status', data: { status: 'running' } })
    streamMock.emit('t1', { id: '12', event: 'task_status', data: { status: 'running' } })

    expect(await screen.findAllByText('运行中')).toHaveLength(1)
  })

  it('allows retry only for failed or interrupted jobs', async () => {
    render(<RunDashboard experimentId="exp-1" />)

    expect(await screen.findByRole('button', { name: /重试 2024-350/i })).toBeEnabled()
    expect(screen.queryByRole('button', { name: /重试 2024-351/i })).not.toBeInTheDocument()
  })

  it('requires confirmation before stopping the experiment', async () => {
    const user = userEvent.setup(); render(<RunDashboard experimentId="exp-1" />)
    await user.click(await screen.findByRole('button', { name: '停止实验' }))

    const dialog = screen.getByRole('dialog', { name: '确认停止实验' })
    expect(apiMock.stopExperiment).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole('button', { name: '确认停止' }))
    expect(apiMock.stopExperiment).toHaveBeenCalledWith('exp-1')
  })

  it('reconnects a task stream with the last accepted event id', async () => {
    render(<RunDashboard experimentId="exp-1" />); await screen.findByText('2024-350')
    streamMock.emit('t1', { id: '12', event: 'task_status', data: { status: 'running' } })
    streamMock.disconnect('t1')

    await waitFor(() => expect(apiMock.openTaskEvents).toHaveBeenCalledWith('t1', '12', expect.any(Function), expect.any(Function)))
  })

  it('shows missing-output details and switches attempt logs', async () => {
    const user = userEvent.setup(); render(<RunDashboard experimentId="exp-1" />)
    await user.click(await screen.findByRole('button', { name: /查看 2024-350/i }))

    expect(await screen.findByText('missing_output')).toBeInTheDocument()
    expect(await screen.findByText('attempt-two missing output')).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Attempt'), 'a1')
    expect(await screen.findByText('attempt-one log')).toBeInTheDocument()
  })

  it('shows an explicit empty result state', async () => {
    const user = userEvent.setup(); render(<RunDashboard experimentId="exp-1" />)
    await user.click(await screen.findByRole('button', { name: /查看 2024-351/i }))

    expect(await screen.findByText('该 attempt 没有结果文件')).toBeInTheDocument()
  })
})
