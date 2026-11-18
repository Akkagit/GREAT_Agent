import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { RecoveryNotice } from '../components/RecoveryNotice'
import type { MonitorTask } from '../api/types'

const mock = vi.hoisted(() => ({ getTaskRecovery: vi.fn(), recheckTaskRecovery: vi.fn(), stopTaskRecovery: vi.fn() }))
vi.mock('../api/client', () => ({ api: mock }))
const task: MonitorTask = { id: 't1', experiment_id: 'e1', year: 2024, doy: 350, status: 'running', active_attempt_id: 'a1', attempts: [], analysis_status: 'not_started', artifact_version: 0, recovery: { state: 'blocked', reason: 'identity_incomplete', last_checked_at: '2026-11-12T10:00:00Z', version: 1 } }
beforeEach(() => { vi.resetAllMocks(); mock.recheckTaskRecovery.mockResolvedValue({ accepted: true }); mock.stopTaskRecovery.mockResolvedValue({ accepted: true }) })

it('shows a blocked reason and submits only the server-allowed recheck bound to the attempt', async () => {
  mock.getTaskRecovery.mockResolvedValue({ allowed_actions: ['recheck'] })
  const user = userEvent.setup()
  render(<RecoveryNotice task={task} />)
  expect(screen.getByText('任务恢复受阻')).toBeInTheDocument()
  expect(screen.getByText('identity_incomplete')).toBeInTheDocument()
  await user.click(await screen.findByRole('button', { name: '重新检测' }))
  expect(mock.recheckTaskRecovery).toHaveBeenCalledWith('t1', { expected_attempt_id: 'a1', request_id: expect.any(String) })
  expect(await screen.findByRole('status')).toHaveTextContent('等待 Worker 复核')
  expect(screen.queryByRole('button', { name: '停止遗留任务' })).not.toBeInTheDocument()
})

it('requests a verified orphan stop without reporting the process as already stopped', async () => {
  mock.getTaskRecovery.mockResolvedValue({ allowed_actions: ['recheck', 'stop'] })
  const user = userEvent.setup()
  render(<RecoveryNotice task={{ ...task, recovery: { ...task.recovery, state: 'observing', reason: 'process_alive' } }} />)
  await user.click(await screen.findByRole('button', { name: '停止遗留任务' }))
  expect(mock.stopTaskRecovery).toHaveBeenCalledWith('t1', { expected_attempt_id: 'a1', request_id: expect.any(String) })
  expect(await screen.findByRole('status')).toHaveTextContent('等待 Worker 核验身份并确认退出')
})

it('ignores recovery action capabilities returned after the task was left', async () => {
  let resolve!: (value: unknown) => void
  mock.getTaskRecovery.mockImplementation(() => new Promise(done => { resolve = done }))
  const page = render(<RecoveryNotice task={task} />)
  page.unmount()
  await act(async () => resolve({ allowed_actions: ['stop'] }))
  expect(screen.queryByRole('button', { name: '停止遗留任务' })).not.toBeInTheDocument()
})
