import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MonitorTask, TaskAttempt } from '../api/types'
import { RunDashboard } from '../pages/RunDashboard'
import { selectOption } from './selectOption'

const mocks = vi.hoisted(() => ({
  getTaskLog: vi.fn(), getTaskResults: vi.fn(), listAnalysisJobs: vi.fn(), retryAnalysisJob: vi.fn(),
  prepareExecution: vi.fn(), retryTask: vi.fn(), refresh: vi.fn(),
  monitor: { tasks: [] as MonitorTask[], error: null, connection: 'live' },
}))

vi.mock('../api/client', async importOriginal => {
  const original = await importOriginal<typeof import('../api/client')>()
  return { ...original, api: { ...original.api, ...mocks } }
})
vi.mock('../monitoring/useExperimentMonitor', () => ({
  useOptionalExperimentMonitor: () => ({}),
  useExperimentMonitor: () => ({ ...mocks.monitor, refresh: mocks.refresh }),
}))

function attempt(id: string, number = 1): TaskAttempt {
  return { id, number, status: 'succeeded', exit_code: 0, error_code: null, analysis_status: 'failed' }
}
function task(id: string, doy: number, attempts = [attempt(`${id}-a1`)]): MonitorTask {
  return { id, experiment_id: 'exp-1', year: 2024, doy, status: 'succeeded',
    active_attempt_id: attempts.at(-1)!.id, attempts, analysis_status: 'failed', artifact_version: 1,
    recovery: { state: 'none', reason: null, last_checked_at: null, version: 0 } }
}
function job(id: string, taskId: string, attemptId: string, status = 'failed') {
  return { id, task_id: taskId, attempt_id: attemptId, revision_id: 'revision-1', status,
    run_count: 1, config: {}, error_message: null }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.monitor.tasks = [task('t1', 350), task('t2', 351)]
  mocks.getTaskLog.mockResolvedValue('preserved solver log')
  mocks.getTaskResults.mockResolvedValue([])
  mocks.listAnalysisJobs.mockResolvedValue([job('j1', 't1', 't1-a1')])
  mocks.retryAnalysisJob.mockResolvedValue(job('j1', 't1', 't1-a1', 'pending'))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('uses only the analysis-job HTTP endpoints and retains API failures', async () => {
  const { api, browserConfig } = await vi.importActual<typeof import('../api/client')>('../api/client')
  const queued = job('job one', 'task one', 'a1', 'pending')
  const fetch = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ items: [queued] }) })
    .mockResolvedValueOnce({ ok: true, json: async () => queued })
    .mockResolvedValueOnce({ ok: false, status: 409, json: async () => ({ error: { code: 'not_retryable', message: '不能重试' } }) })
  vi.stubGlobal('fetch', fetch)
  expect(await api.listAnalysisJobs('task one')).toEqual([queued])
  expect(fetch).toHaveBeenLastCalledWith(`${browserConfig.apiBaseUrl}/tasks/task%20one/analysis-jobs`, expect.any(Object))
  expect(await api.retryAnalysisJob('task one', 'job one')).toEqual(queued)
  expect(fetch).toHaveBeenLastCalledWith(`${browserConfig.apiBaseUrl}/tasks/task%20one/analysis-jobs/job%20one/retry`, expect.objectContaining({ method: 'POST' }))
  await expect(api.retryAnalysisJob('task one', 'job one')).rejects.toMatchObject({ status: 409, code: 'not_retryable', message: '不能重试' })
})

describe('analysis-only retry', () => {
  it('retries exactly the selected successful attempt, prevents double submission, and preserves solver output', async () => {
    const pending = deferred<ReturnType<typeof job>>()
    mocks.listAnalysisJobs.mockResolvedValue([
      job('wrong-attempt', 't1', 'old-attempt'), job('wrong-task', 't2', 't1-a1'), job('j1', 't1', 't1-a1'),
    ])
    mocks.retryAnalysisJob.mockReturnValue(pending.promise)
    const user = userEvent.setup()
    render(<RunDashboard experimentId="exp-1" />)
    await user.click(screen.getByRole('button', { name: '查看 2024-350' }))
    const retry = await screen.findByRole('button', { name: '重试后处理' })
    await user.dblClick(retry)
    expect(mocks.retryAnalysisJob).toHaveBeenCalledExactlyOnceWith('t1', 'j1')
    expect(retry).toBeDisabled()
    await act(async () => pending.resolve(job('j1', 't1', 't1-a1', 'pending')))
    expect(await screen.findByText('已提交后处理重试，解算结果保留。')).toBeInTheDocument()
    expect(screen.getByText('preserved solver log')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重试后处理' })).not.toBeInTheDocument()
    expect(mocks.prepareExecution).not.toHaveBeenCalled()
    expect(mocks.retryTask).not.toHaveBeenCalled()
    expect(mocks.refresh).toHaveBeenCalledOnce()
  })

  it.each([
    { status: 'failed', analysis_status: 'failed' },
    { status: 'running', analysis_status: 'failed' },
    { status: 'succeeded', analysis_status: 'completed' },
  ] as const)('does not offer analysis retry for $status / $analysis_status', async state => {
    mocks.monitor.tasks = [task('t1', 350, [{ ...attempt('t1-a1'), ...state }])]
    const user = userEvent.setup()
    render(<RunDashboard experimentId="exp-1" />)
    await user.click(screen.getByRole('button', { name: '查看 2024-350' }))
    expect(screen.queryByRole('button', { name: '重试后处理' })).not.toBeInTheDocument()
    expect(mocks.listAnalysisJobs).not.toHaveBeenCalled()
  })

  it('explains a historical failure without a matching job', async () => {
    mocks.listAnalysisJobs.mockResolvedValue([job('wrong-attempt', 't1', 'other-attempt')])
    const user = userEvent.setup()
    render(<RunDashboard experimentId="exp-1" />)
    await user.click(screen.getByRole('button', { name: '查看 2024-350' }))
    expect(await screen.findByText('该历史 attempt 暂无可重试的后处理作业。')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重试后处理' })).not.toBeInTheDocument()
    expect(mocks.retryAnalysisJob).not.toHaveBeenCalled()
  })

  it('ignores an old task list response before retrying the newly selected task', async () => {
    const old = deferred<ReturnType<typeof job>[]>()
    mocks.listAnalysisJobs.mockReturnValueOnce(old.promise).mockResolvedValueOnce([job('j2', 't2', 't2-a1')])
    mocks.retryAnalysisJob.mockResolvedValue(job('j2', 't2', 't2-a1', 'pending'))
    const user = userEvent.setup()
    render(<RunDashboard experimentId="exp-1" />)
    await user.click(screen.getByRole('button', { name: '查看 2024-350' }))
    await user.click(screen.getByRole('button', { name: '查看 2024-351' }))
    await screen.findByRole('button', { name: '重试后处理' })
    await act(async () => old.resolve([job('j1', 't1', 't1-a1')]))
    await user.click(screen.getByRole('button', { name: '重试后处理' }))
    expect(mocks.retryAnalysisJob).toHaveBeenCalledExactlyOnceWith('t2', 'j2')
  })

  it('ignores an old attempt list response and never retries that old selection', async () => {
    mocks.monitor.tasks = [task('t1', 350, [attempt('a1', 1), attempt('a2', 2)])]
    const old = deferred<ReturnType<typeof job>[]>()
    mocks.listAnalysisJobs.mockReturnValueOnce(old.promise).mockResolvedValueOnce([job('old-job', 't1', 'a1')])
    const user = userEvent.setup()
    render(<RunDashboard experimentId="exp-1" />)
    await user.click(screen.getByRole('button', { name: '查看 2024-350' }))
    await selectOption(user, 'Attempt', '#1 · succeeded')
    await screen.findByRole('button', { name: '重试后处理' })
    await act(async () => old.resolve([job('latest-job', 't1', 'a2')]))
    await user.click(screen.getByRole('button', { name: '重试后处理' }))
    expect(mocks.retryAnalysisJob).toHaveBeenCalledExactlyOnceWith('t1', 'old-job')
  })

  it.each([
    ['task', 'error'], ['attempt', 'error'], ['experiment', 'error'],
    ['task', 'success'], ['attempt', 'success'], ['experiment', 'success'],
  ] as const)('discards late retry %s / %s', async (selection, outcome) => {
    mocks.monitor.tasks[0] = task('t1', 350, [attempt('a1', 1), attempt('a2', 2)])
    mocks.listAnalysisJobs.mockResolvedValue([job('j1', 't1', 'a2')])
    const pending = deferred<ReturnType<typeof job>>()
    mocks.retryAnalysisJob.mockReturnValue(pending.promise)
    const user = userEvent.setup()
    const page = render(<RunDashboard experimentId="exp-1" />)
    await user.click(screen.getByRole('button', { name: '查看 2024-350' }))
    await user.click(await screen.findByRole('button', { name: '重试后处理' }))
    if (selection === 'task') await user.click(screen.getByRole('button', { name: '查看 2024-351' }))
    else if (selection === 'attempt') await selectOption(user, 'Attempt', '#1 · succeeded')
    else page.rerender(<RunDashboard experimentId="exp-2" />)
    await act(async () => {
      if (outcome === 'error') pending.reject(new Error('old selection retry failed'))
      else pending.resolve(job('j1', 't1', 'a2', 'pending'))
    })
    expect(screen.queryByText('old selection retry failed')).not.toBeInTheDocument()
    expect(screen.queryByText('已提交后处理重试，解算结果保留。')).not.toBeInTheDocument()
  })

  it('shows a current retry failure and allows an explicit retry again', async () => {
    mocks.retryAnalysisJob.mockRejectedValueOnce(new Error('后处理队列暂不可用'))
      .mockResolvedValueOnce(job('j1', 't1', 't1-a1', 'pending'))
    const user = userEvent.setup()
    render(<RunDashboard experimentId="exp-1" />)
    await user.click(screen.getByRole('button', { name: '查看 2024-350' }))
    await user.click(await screen.findByRole('button', { name: '重试后处理' }))
    expect(await screen.findByText('后处理队列暂不可用')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '重试后处理' }))
    await waitFor(() => expect(mocks.retryAnalysisJob).toHaveBeenCalledTimes(2))
  })
})
