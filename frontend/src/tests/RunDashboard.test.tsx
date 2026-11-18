import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { selectOption, selectedOption } from './selectOption'
import { RunDashboard } from '../pages/RunDashboard'
import type { TaskDetail, TaskStatus } from '../api/types'
import { legacyMonitorSnapshot, monitorCursor, monitorTask } from './monitorFixture'

const apiMock = vi.hoisted(() => ({
  listTasks: vi.fn(), getTask: vi.fn(), getTaskLog: vi.fn(), getTaskResults: vi.fn(),
  retryTask: vi.fn(), stopExperiment: vi.fn(), openTaskEvents: vi.fn(),
  getExperiment: vi.fn(), prepareExecution: vi.fn(), confirmExecution: vi.fn(),
  getExperimentMonitor: vi.fn(), openExperimentEvents: vi.fn(),
}))

const streamMock = vi.hoisted(() => ({
  listeners: new Map<string, (event: unknown) => void>(),
  disconnectors: new Map<string, (error: unknown) => void>(),
  async emit(taskId: string, event: { id: string; event?: string; data: { status: string } }) {
    const detail = await apiMock.getTask(taskId)
    this.listeners.get('exp-1')?.({schema_version:1,event_id:monitorCursor('exp-1',event.id),experiment_id:'exp-1',task_id:taskId,attempt_id:detail.active_attempt_id,type:'task.status',data:{task:{...monitorTask(detail),status:event.data.status,attempts:detail.attempts.map((attempt: TaskDetail['attempts'][number])=>attempt.id===detail.active_attempt_id?{...attempt,status:event.data.status as TaskStatus}:attempt)}}})
  },
  disconnect(_taskId: string) { this.disconnectors.get('exp-1')?.(new Error('Disconnected')) },
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
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })
  beforeEach(() => {
    vi.clearAllMocks(); streamMock.listeners.clear(); streamMock.disconnectors.clear()
    apiMock.listTasks.mockResolvedValue(tasks)
    apiMock.getTask.mockImplementation(async (id: string) => id === 't1' ? taskDetail : { ...tasks[1], active_attempt_id: 'b1', attempts: [{ id: 'b1', number: 1, status: 'succeeded', error_code: null, exit_code: 0 }] })
    apiMock.getTaskLog.mockImplementation(async (_taskId: string, attemptId: string) => attemptId === 'a1' ? 'attempt-one log' : 'attempt-two missing output')
    apiMock.getTaskResults.mockResolvedValue([])
    apiMock.retryTask.mockResolvedValue({ status: 'pending' })
    apiMock.stopExperiment.mockResolvedValue({ status: 'stopping' })
    apiMock.getExperimentMonitor.mockImplementation(id => legacyMonitorSnapshot(id,apiMock.listTasks,apiMock.getTask))
    apiMock.openExperimentEvents.mockImplementation((taskId: string, lastId: string | undefined, onEvent: (event: unknown) => void, onDisconnect: () => void) => {
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
    await user.click(await screen.findByRole('button', { name: '查看 2024-350' }))
    await user.click(await screen.findByRole('button', { name: '停止实验' }))

    const dialog = screen.getByRole('dialog', { name: '确认停止实验' })
    expect(apiMock.stopExperiment).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole('button', { name: '确认停止' }))
    expect(apiMock.stopExperiment).toHaveBeenCalledWith('exp-1')
  })

  it('opens a new XML review before retrying and never calls the legacy retry endpoint', async () => {
    const { executionPreview } = await import('./executionFixture')
    const user = userEvent.setup(), onPrepared = vi.fn()
    apiMock.getExperiment.mockResolvedValue({ current_revision: 3 })
    apiMock.prepareExecution.mockResolvedValue({ ...executionPreview, intent: 'retry' })
    render(<RunDashboard experimentId="exp-1" onPrepared={onPrepared} />)
    await user.click(await screen.findByRole('button', { name: /重试 2024-350/ }))
    expect(apiMock.prepareExecution).toHaveBeenCalledWith('exp-1', { expected_revision: 3, task_id: 't1' })
    expect(onPrepared).toHaveBeenCalledOnce()
    expect(apiMock.retryTask).not.toHaveBeenCalled()
    expect(apiMock.confirmExecution).not.toHaveBeenCalled()
  })

  it('reconnects the experiment stream with the last accepted event id', async () => {
    render(<RunDashboard experimentId="exp-1" />); await screen.findByText('2024-350')
    await streamMock.emit('t1', { id: '12', data: { status: 'running' } })
    streamMock.disconnect('t1')

    await waitFor(() => expect(apiMock.openExperimentEvents).toHaveBeenCalledWith('exp-1', monitorCursor('exp-1','12'), expect.any(Function), expect.any(Function), expect.any(AbortSignal), expect.any(Function)))
    expect(apiMock.openTaskEvents).not.toHaveBeenCalled()
  })

  it('does not navigate when a retry preview finishes after leaving the monitor', async () => {
    const { executionPreview } = await import('./executionFixture')
    let finish!: (value: typeof executionPreview) => void
    apiMock.getExperiment.mockResolvedValue({ current_revision:3 })
    apiMock.prepareExecution.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const user=userEvent.setup(), onPrepared=vi.fn()
    const page=render(<RunDashboard experimentId="exp-1" onPrepared={onPrepared} />)
    await user.click(await screen.findByRole('button',{name:/重试 2024-350/}))
    page.unmount()
    await act(async () => { finish(executionPreview) })
    expect(onPrepared).not.toHaveBeenCalled()
    expect(apiMock.confirmExecution).not.toHaveBeenCalled()
  })

  it('shows missing-output details and switches attempt logs', async () => {
    const user = userEvent.setup(); render(<RunDashboard experimentId="exp-1" />)
    await user.click(await screen.findByRole('button', { name: /查看 2024-350/i }))

    expect(await screen.findByText('missing_output')).toBeInTheDocument()
    expect(await screen.findByText('attempt-two missing output')).toBeInTheDocument()
    await selectOption(user, 'Attempt', '#1 · failed')
    expect(await screen.findByText('attempt-one log')).toBeInTheDocument()
  })

  it('shows an explicit empty result state', async () => {
    const user = userEvent.setup(); render(<RunDashboard experimentId="exp-1" />)
    await user.click(await screen.findByRole('button', { name: /查看 2024-351/i }))

    expect(await screen.findByText('该 attempt 没有结果文件')).toBeInTheDocument()
  })

  it('refreshes selected logs and results when a task finishes', async () => {
    const user = userEvent.setup()
    render(<RunDashboard experimentId="exp-1" />)
    await user.click(await screen.findByRole('button', { name: /查看 2024-350/i }))
    await screen.findByText('attempt-two missing output')
    apiMock.getTaskLog.mockResolvedValue('solver completed and results collected')
    apiMock.getTaskResults.mockResolvedValue([{ station: 'S000', kind: 'flt', filename: 'S000.flt', size_bytes: 123, url: '/download/S000' }])
    streamMock.emit('t1', { id: '88', event: 'task_status', data: { status: 'succeeded' } })
    expect(await screen.findByText('solver completed and results collected')).toBeInTheDocument()
    expect(await screen.findByText('S000.flt')).toBeInTheDocument()
  })

  it('discovers the first attempt after a selected pending task starts', async () => {
    const user = userEvent.setup()
    apiMock.listTasks.mockResolvedValue([{ ...tasks[0], status: 'pending' }])
    apiMock.getTask.mockResolvedValue({ ...tasks[0], status: 'pending', active_attempt_id: null, attempts: [] })
    render(<RunDashboard experimentId="exp-1" />)
    await user.click(await screen.findByRole('button', { name: /查看 2024-350/i }))
    await screen.findByText('暂无日志')
    apiMock.getTask.mockResolvedValue({ ...taskDetail, status: 'running', active_attempt_id: 'a1', attempts: [taskDetail.attempts[0]] })
    streamMock.emit('t1', { id: '91', event: 'task_status', data: { status: 'running' } })
    expect(await screen.findByText('attempt-one log')).toBeInTheDocument()
    expect(await selectedOption('Attempt')).toHaveTextContent('#1 · running')
  })

  it('follows a queued retry to the new active attempt but preserves manual archive selection', async () => {
    const user = userEvent.setup()
    apiMock.listTasks.mockResolvedValue([{ ...tasks[0], status: 'pending' }])
    apiMock.getTask.mockResolvedValue({ ...taskDetail, status: 'pending', active_attempt_id: 'a1', attempts: [taskDetail.attempts[0]] })
    render(<RunDashboard experimentId="exp-1" />)
    await user.click(await screen.findByRole('button', { name: /查看 2024-350/i }))
    await screen.findByText('attempt-one log')
    apiMock.getTask.mockResolvedValue({ ...taskDetail, status: 'running' })
    streamMock.emit('t1', { id: '92', event: 'task_status', data: { status: 'running' } })
    expect(await screen.findByText('attempt-two missing output')).toBeInTheDocument()
    expect(await selectedOption('Attempt')).toHaveTextContent('#2 · running')
    await selectOption(user, 'Attempt', '#1 · failed')
    await screen.findByText('attempt-one log')
    streamMock.emit('t1', { id: '93', event: 'task_status', data: { status: 'succeeded' } })
    expect(await screen.findByText('attempt-one log')).toBeInTheDocument()
    expect(await selectedOption('Attempt')).toHaveTextContent('#1 · failed')
  })

  it.each(['success', 'failure'])('hides previous logs and results while following a deferred new active attempt: %s', async outcome => {
    const user = userEvent.setup()
    apiMock.getTaskResults.mockResolvedValueOnce([{ station: null, kind: 'flt', filename: 'attempt-a.flt', size_bytes: 3, url: '/a' }])
    render(<RunDashboard experimentId="exp-1" />)
    await user.click(await screen.findByRole('button', { name: '查看 2024-350' }))
    await screen.findByText('attempt-two missing output'); await screen.findByText('attempt-a.flt')
    let finishLog!: (value: string) => void, failLog!: (error: Error) => void
    let finishResults!: (value: unknown[]) => void, failResults!: (error: Error) => void
    apiMock.getTaskLog.mockImplementationOnce(() => new Promise((resolve, reject) => { finishLog = resolve; failLog = reject }))
    apiMock.getTaskResults.mockImplementationOnce(() => new Promise((resolve, reject) => { finishResults = resolve; failResults = reject }))
    const next = { ...monitorTask(taskDetail as TaskDetail), active_attempt_id: 'b1', attempts: [...taskDetail.attempts, { id: 'b1', number: 3, status: 'failed', exit_code: 1, error_code: null }] }
    await act(async () => streamMock.listeners.get('exp-1')?.({ schema_version: 1, event_id: monitorCursor('exp-1', '1'), experiment_id: 'exp-1', task_id: 't1', attempt_id: 'b1', type: 'attempt.started', data: { task: next } }))
    expect(screen.queryByText('attempt-two missing output')).not.toBeInTheDocument()
    expect(screen.queryByText('attempt-a.flt')).not.toBeInTheDocument()
    await act(async () => {
      if (outcome === 'success') { finishLog('attempt-b log'); finishResults([{ station: null, kind: 'flt', filename: 'attempt-b.flt', size_bytes: 3, url: '/b' }]) }
      else { failLog(new Error('B unavailable')); failResults(new Error('B unavailable')) }
    })
    if (outcome === 'success') { expect(await screen.findByText('attempt-b log')).toBeInTheDocument(); expect(await screen.findByText('attempt-b.flt')).toBeInTheDocument() }
    expect(screen.queryByText('attempt-two missing output')).not.toBeInTheDocument()
    expect(screen.queryByText('attempt-a.flt')).not.toBeInTheDocument()
  })

  it.each(['old_failed', 'no_attempt', 'postprocessing'] as const)('does not poll logs for a non-running selected attempt: %s', async scenario => {
    const timer = vi.spyOn(window, 'setInterval')
    const selected = { ...taskDetail, status: scenario === 'postprocessing' ? 'running' : 'pending', active_attempt_id: scenario === 'no_attempt' ? null : 'a2', attempts: scenario === 'no_attempt' ? [] : taskDetail.attempts.map(item => ({ ...item, status: scenario === 'postprocessing' ? 'succeeded' : 'failed' })) }
    apiMock.getExperimentMonitor.mockResolvedValue({ experiment_id: 'exp-1', cursor: monitorCursor('exp-1'), tasks: [monitorTask(selected as TaskDetail)] })
    const user = userEvent.setup(); render(<RunDashboard experimentId="exp-1" />)
    await user.click(await screen.findByRole('button', { name: '查看 2024-350' }))
    await waitFor(() => expect(screen.getByText(scenario === 'no_attempt' ? '暂无日志' : 'attempt-two missing output')).toBeInTheDocument())
    expect(timer.mock.calls.filter(call => call[1] === 2500)).toHaveLength(0)
    if (scenario === 'no_attempt') expect(apiMock.getTaskLog).not.toHaveBeenCalled()
  })

  it('polls the selected running attempt every 2.5 seconds and reads once more when it finishes', async () => {
    const timers = vi.spyOn(window, 'setInterval'), clear = vi.spyOn(window, 'clearInterval')
    const user = userEvent.setup(); render(<RunDashboard experimentId="exp-1" />)
    await user.click(await screen.findByRole('button', { name: '查看 2024-350' }))
    await screen.findByText('attempt-two missing output')
    const initialCalls = apiMock.getTaskLog.mock.calls.length
    await act(async () => streamMock.emit('t1', { id: '1', data: { status: 'running' } }))
    const polling = timers.mock.calls.find(call => call[1] === 2500)!
    expect(polling).toBeDefined()
    expect(apiMock.getTaskLog).toHaveBeenCalledTimes(initialCalls + 1)
    await act(async () => (polling[0] as () => void)())
    expect(apiMock.getTaskLog).toHaveBeenCalledTimes(initialCalls + 2)
    await act(async () => streamMock.emit('t1', { id: '2', data: { status: 'succeeded' } }))
    expect(apiMock.getTaskLog).toHaveBeenCalledTimes(initialCalls + 3)
    expect(clear).toHaveBeenCalled()
    expect(timers.mock.calls.filter(call => call[1] === 2500)).toHaveLength(1)
  })
})
