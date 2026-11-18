import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { ArtifactBrowser } from '../components/ArtifactBrowser'
import { selectOption } from './selectOption'
import { legacyMonitorSnapshot, monitorCursor, monitorTask } from './monitorFixture'
import { ExperimentMonitorProvider } from '../monitoring/ExperimentMonitor'
import { RunDashboard } from '../pages/RunDashboard'

const mock = vi.hoisted(() => ({ listTasks: vi.fn(), getTask: vi.fn(), getTaskResults: vi.fn(), getTaskXml: vi.fn(),getExperimentMonitor:vi.fn(),openExperimentEvents:vi.fn() }))
vi.mock('../api/client', async original => ({ ...await original<typeof import('../api/client')>(), api: mock }))

const task = { id: 't1', experiment_id: 'e1', year: 2024, doy: 350, status: 'succeeded' }
const attempt = { id: 'a1', number: 1, status: 'succeeded', exit_code: 0, error_code: null }

beforeEach(() => {
  vi.resetAllMocks()
  mock.listTasks.mockResolvedValue([task])
  mock.getTask.mockResolvedValue({ ...task, active_attempt_id: 'a1', attempts: [attempt] })
  mock.getExperimentMonitor.mockImplementation(id => legacyMonitorSnapshot(id,mock.listTasks,mock.getTask))
  mock.openExperimentEvents.mockReturnValue(() => {})
})

it('hands off the exact selected historical attempt as research material', async () => {
  const user = userEvent.setup(), handoff = vi.fn()
  mock.getTask.mockResolvedValue({ ...task, active_attempt_id: 'a2', attempts: [attempt, { ...attempt, id: 'a2', number: 2 }] })
  mock.getTaskResults.mockResolvedValue([])
  render(<ArtifactBrowser experimentId="e1" mode="images" onResearch={handoff} />)
  await selectOption(user, '结果 Attempt', '#1 · succeeded')
  await user.click(screen.getByRole('button', { name: '带入科研分析' }))
  expect(handoff).toHaveBeenCalledWith({ kind: 'experiment', experiment_id: 'e1', task_id: 't1', attempt_id: 'a1', role: 'baseline' })
})

it('previews only the three summary plots in a stable order with readable labels', async () => {
  const user = userEvent.setup()
  mock.getTaskResults.mockResolvedValue([
    { filename: 'convergence.png', station: null, kind: 'plot', plot_type: null, size_bytes: 10, url: '/cdf/convergence' },
    { filename: 'fix_rate.png', station: null, kind: 'plot', plot_type: 'fix_rate', size_bytes: 30, url: '/summary/fix-rate' },
    { filename: 'station-enu.png', station: 'AHB0', kind: 'plot', plot_type: null, size_bytes: 20, url: '/station/enu' },
    { filename: 'convergence.png', station: null, kind: 'plot', plot_type: 'convergence', size_bytes: 40, url: '/summary/convergence' },
    { filename: 'enu.png', station: null, kind: 'plot', plot_type: 'enu', size_bytes: 50, url: '/summary/enu' },
    { filename: 'summary.csv', station: null, kind: 'summary', plot_type: null, size_bytes: 90, url: '/summary/csv' },
  ])
  render(<ArtifactBrowser experimentId="e1" mode="images" />)

  expect(await screen.findByRole('img', { name: '全局 ENU' })).toHaveAttribute('src', '/summary/enu')
  await user.click(screen.getByRole('combobox', { name: '图片文件' }))
  expect(screen.getAllByRole('option').map(option => option.textContent)).toEqual(['全局 ENU', '收敛时间', '固定率'])
  await user.click(screen.getByRole('option', { name: '收敛时间' }))
  expect(screen.getByRole('img', { name: '收敛时间' })).toHaveAttribute('src', '/summary/convergence')
  expect(screen.getByRole('link', { name: '打开原图' })).toHaveAttribute('href', '/summary/convergence')
  await selectOption(user, '图片文件', '固定率')
  expect(screen.getByRole('img', { name: '固定率' })).toHaveAttribute('src', '/summary/fix-rate')
})

it('omits unavailable summary plots instead of substituting legacy images with the same basename', async () => {
  const user = userEvent.setup()
  mock.getTaskResults.mockResolvedValue([
    { filename: 'enu.png', station: null, kind: 'plot', plot_type: 'enu', size_bytes: 50, url: '/summary/enu' },
    { filename: 'convergence.png', station: null, kind: 'plot', plot_type: null, size_bytes: 10, url: '/cdf/convergence' },
    { filename: 'fix_rate.png', station: null, kind: 'plot', size_bytes: 30, url: '/legacy/fix-rate' },
  ])
  render(<ArtifactBrowser experimentId="e1" mode="images" />)

  expect(await screen.findByRole('img', { name: '全局 ENU' })).toHaveAttribute('src', '/summary/enu')
  await user.click(screen.getByRole('combobox', { name: '图片文件' }))
  expect(screen.getAllByRole('option').map(option => option.textContent)).toEqual(['全局 ENU'])
})

it('shows an empty preview when an attempt has only legacy plot files', async () => {
  mock.getTaskResults.mockResolvedValue([
    { filename: 'convergence.png', station: null, kind: 'plot', plot_type: null, size_bytes: 10, url: '/cdf/convergence' },
    { filename: 'station-enu.png', station: 'AHB0', kind: 'plot', size_bytes: 20, url: '/station/enu' },
  ])
  render(<ArtifactBrowser experimentId="e1" mode="images" />)

  expect(await screen.findByText(/尚无可预览的图片/)).toBeInTheDocument()
  expect(screen.queryByRole('figure')).not.toBeInTheDocument()
  expect(screen.queryByRole('combobox', { name: '图片文件' })).not.toBeInTheDocument()
})

it('keeps daily task and attempt selection scoped to their actual summary images', async () => {
  const user = userEvent.setup()
  const secondTask = { ...task, id: 't2', doy: 351 }
  const secondAttempt = { ...attempt, id: 'a2', number: 2 }
  const thirdAttempt = { ...attempt, id: 'a3' }
  mock.listTasks.mockResolvedValue([task, secondTask])
  mock.getTask.mockImplementation(async id => id === 't1'
    ? { ...task, active_attempt_id: 'a2', attempts: [attempt, secondAttempt] }
    : { ...secondTask, active_attempt_id: 'a3', attempts: [thirdAttempt] })
  mock.getTaskResults.mockImplementation(async (_taskId, attemptId) => [{
    filename: 'enu.png', station: null, kind: 'plot', plot_type: 'enu', size_bytes: 50, url: `/summary/${attemptId}/enu`,
  }])
  render(<ArtifactBrowser experimentId="e1" mode="images" />)

  expect(await screen.findByRole('img', { name: '全局 ENU' })).toHaveAttribute('src', '/summary/a2/enu')
  await selectOption(user, '结果 Attempt', '#1 · succeeded')
  expect(await screen.findByRole('img', { name: '全局 ENU' })).toHaveAttribute('src', '/summary/a1/enu')
  await selectOption(user, '日期任务', '2024 / DOY 351 · succeeded')
  expect(await screen.findByRole('img', { name: '全局 ENU' })).toHaveAttribute('src', '/summary/a3/enu')
})

it('keeps archived XML readable when switching attempts', async () => {
  const user = userEvent.setup()
  mock.getTask.mockResolvedValue({ ...task, active_attempt_id: 'a2', attempts: [attempt, { ...attempt, id: 'a2', number: 2 }] })
  mock.getTaskXml.mockImplementation(async (_taskId, attemptId) => ({ xml_path: `/attempts/${attemptId}/config.xml`, xml_content: `<config attempt="${attemptId}"/>` }))
  render(<ArtifactBrowser experimentId="e1" mode="xml" />)

  expect(await screen.findByRole('region', { name: 'XML 全文' })).toHaveTextContent('<config attempt="a2"/>')
  await selectOption(user, '结果 Attempt', '#1 · succeeded')
  expect(await screen.findByRole('region', { name: 'XML 全文' })).toHaveTextContent('<config attempt="a1"/>')
  expect(screen.getByText('/attempts/a1/config.xml')).toBeInTheDocument()
  expect(screen.queryByRole('combobox', { name: '图片文件' })).not.toBeInTheDocument()
})

it('refreshes an already-open terminal attempt after manual analysis and versions the same image URL', async () => {
  const initial = monitorTask({ ...task, status: 'succeeded', active_attempt_id: 'a1', attempts: [{ ...attempt, status: 'succeeded', analysis_status: 'not_started', artifact_version: 0 }] })
  mock.getExperimentMonitor.mockResolvedValue({ experiment_id: 'e1', cursor: monitorCursor('e1'), tasks: [initial] })
  mock.getTaskResults.mockResolvedValue([])
  render(<ArtifactBrowser experimentId="e1" mode="images" />)
  await screen.findByText(/尚无可预览的图片/)
  expect(mock.openExperimentEvents).toHaveBeenCalledOnce()
  const receive = mock.openExperimentEvents.mock.calls[0][2]
  const next = { ...initial, analysis_status: 'completed', artifact_version: 2, attempts: [{ ...initial.attempts[0], analysis_status: 'completed', artifact_version: 2 }] }
  mock.getTaskResults.mockResolvedValue([{ filename: 'enu.png', kind: 'plot', station: null, plot_type: 'enu', size_bytes: 50, url: '/same/enu.png' }])
  await act(async () => receive({ schema_version: 1, event_id: monitorCursor('e1', '2'), experiment_id: 'e1', task_id: 't1', attempt_id: 'a1', type: 'analysis.completed', data: { task: next } }))
  expect(await screen.findByRole('img', { name: '全局 ENU' })).toHaveAttribute('src', '/same/enu.png?artifact_version=2')
  expect(screen.getByRole('status')).toHaveTextContent('分析完成')
})

it('shares one stream for 100 tasks across task and artifact views, preserving historical choice on retry', async () => {
  const user = userEvent.setup()
  const initial = monitorTask({ ...task, status: 'succeeded', active_attempt_id: 'a2', attempts: [{ ...attempt, status: 'succeeded', analysis_status: 'completed', artifact_version: 1 }, { ...attempt, id: 'a2', number: 2, status: 'succeeded', analysis_status: 'completed', artifact_version: 2 }] })
  mock.getExperimentMonitor.mockResolvedValue({ experiment_id: 'e1', cursor: monitorCursor('e1'), tasks: Array.from({ length: 100 }, (_, i) => ({ ...initial, id: `t${i + 1}`, doy: i + 1 })) })
  mock.getTaskResults.mockImplementation(async (_task, id) => [{ filename: 'enu.png', kind: 'plot', station: null, plot_type: 'enu', size_bytes: 50, url: `/summary/${id}` }])
  const page = render(<ExperimentMonitorProvider experimentId="e1"><RunDashboard experimentId="e1" /><ArtifactBrowser experimentId="e1" mode="images" /></ExperimentMonitorProvider>)
  await screen.findByRole('img', { name: '全局 ENU' })
  expect(mock.getExperimentMonitor).toHaveBeenCalledOnce(); expect(mock.openExperimentEvents).toHaveBeenCalledOnce(); expect(mock.getTask).not.toHaveBeenCalled()
  await selectOption(user, '结果 Attempt', '#1 · succeeded')
  expect(await screen.findByRole('img', { name: '全局 ENU' })).toHaveAttribute('src', '/summary/a1?artifact_version=1')
  const calls = mock.getTaskResults.mock.calls.length
  const next = { ...initial, status: 'running', active_attempt_id: 'a3', attempts: [...initial.attempts, { ...attempt, id: 'a3', number: 3, status: 'running', analysis_status: 'not_started', artifact_version: 0 }] }
  await act(async () => mock.openExperimentEvents.mock.calls[0][2]({ schema_version: 1, event_id: monitorCursor('e1', '3'), experiment_id: 'e1', task_id: 't1', attempt_id: 'a3', type: 'attempt.started', data: { task: next } }))
  await waitFor(() => expect(screen.getByRole('img', { name: '全局 ENU' })).toHaveAttribute('src', '/summary/a1?artifact_version=1'))
  expect(mock.getTaskResults).toHaveBeenCalledTimes(calls)
  expect(mock.openExperimentEvents).toHaveBeenCalledOnce()
  const signal: AbortSignal = mock.openExperimentEvents.mock.calls[0][4]
  page.unmount(); expect(signal.aborted).toBe(true)
})

it.each(['images', 'xml'] as const)('hides the previous %s immediately while following a deferred new active attempt', async mode => {
  const initial = monitorTask({ ...task, status: 'succeeded', active_attempt_id: 'a1', attempts: [{ ...attempt, status: 'succeeded' }] })
  mock.getExperimentMonitor.mockResolvedValue({ experiment_id: 'e1', cursor: monitorCursor('e1'), tasks: [initial] })
  let finish!: (value: unknown) => void
  const previousImage = { filename: 'enu.png', kind: 'plot', station: null, plot_type: 'enu', size_bytes: 50, url: '/attempt-a/enu.png' }
  mock.getTaskResults.mockResolvedValueOnce([previousImage]).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  mock.getTaskXml.mockResolvedValueOnce({ xml_path: '/attempt-a/config.xml', xml_content: '<config>A</config>' }).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  render(<ArtifactBrowser experimentId="e1" mode={mode} />)
  if (mode === 'images') await screen.findByRole('img', { name: '全局 ENU' })
  else await screen.findByText('/attempt-a/config.xml')
  const next = { ...initial, active_attempt_id: 'b1', attempts: [...initial.attempts, { ...attempt, id: 'b1', number: 2, status: 'succeeded' }] }
  await act(async () => mock.openExperimentEvents.mock.calls[0][2]({ schema_version: 1, event_id: monitorCursor('e1', '1'), experiment_id: 'e1', task_id: 't1', attempt_id: 'b1', type: 'attempt.started', data: { task: next } }))
  expect(screen.queryByRole('img', { name: '全局 ENU' })).not.toBeInTheDocument()
  expect(screen.queryByText('/attempt-a/config.xml')).not.toBeInTheDocument()
  expect(screen.getByText(/正在读取真实产物/)).toBeInTheDocument()
  await act(async () => finish(mode === 'images' ? [{ ...previousImage, url: '/attempt-b/enu.png' }] : { xml_path: '/attempt-b/config.xml', xml_content: '<config>B</config>' }))
  if (mode === 'images') expect(await screen.findByRole('img', { name: '全局 ENU' })).toHaveAttribute('src', '/attempt-b/enu.png')
  else expect(await screen.findByText('/attempt-b/config.xml')).toBeInTheDocument()
})

it.each(['images', 'xml'] as const)('keeps the previous %s hidden when the new active attempt request fails', async mode => {
  const initial = monitorTask({ ...task, status: 'succeeded', active_attempt_id: 'a1', attempts: [{ ...attempt, status: 'succeeded' }] })
  mock.getExperimentMonitor.mockResolvedValue({ experiment_id: 'e1', cursor: monitorCursor('e1'), tasks: [initial] })
  mock.getTaskResults.mockResolvedValueOnce([{ filename: 'enu.png', kind: 'plot', station: null, plot_type: 'enu', size_bytes: 50, url: '/attempt-a/enu.png' }]).mockRejectedValueOnce(new Error('B unavailable'))
  mock.getTaskXml.mockResolvedValueOnce({ xml_path: '/attempt-a/config.xml', xml_content: '<config>A</config>' }).mockRejectedValueOnce(new Error('B unavailable'))
  render(<ArtifactBrowser experimentId="e1" mode={mode} />)
  if (mode === 'images') await screen.findByRole('img', { name: '全局 ENU' })
  else await screen.findByText('/attempt-a/config.xml')
  const next = { ...initial, active_attempt_id: 'b1', attempts: [...initial.attempts, { ...attempt, id: 'b1', number: 2, status: 'succeeded' }] }
  await act(async () => mock.openExperimentEvents.mock.calls[0][2]({ schema_version: 1, event_id: monitorCursor('e1', '1'), experiment_id: 'e1', task_id: 't1', attempt_id: 'b1', type: 'attempt.started', data: { task: next } }))
  expect(await screen.findByRole('alert')).toHaveTextContent(mode === 'images' ? '无法读取结果图片' : '该 attempt 没有可读取的归档 XML')
  expect(screen.queryByRole('img', { name: '全局 ENU' })).not.toBeInTheDocument()
  expect(screen.queryByText('/attempt-a/config.xml')).not.toBeInTheDocument()
})

it.each(['images', 'xml'] as const)('hides loaded %s when selecting a pending task without any attempt', async mode => {
  const initial = monitorTask({ ...task, status: 'succeeded', active_attempt_id: 'a1', attempts: [{ ...attempt, status: 'succeeded' }] })
  const pending = { ...initial, id: 'pending', doy: 351, status: 'pending', active_attempt_id: null, attempts: [] }
  mock.getExperimentMonitor.mockResolvedValue({ experiment_id: 'e1', cursor: monitorCursor('e1'), tasks: [initial, pending] })
  mock.getTaskResults.mockResolvedValue([{ filename: 'enu.png', kind: 'plot', station: null, plot_type: 'enu', size_bytes: 50, url: '/attempt-a/enu.png' }])
  mock.getTaskXml.mockResolvedValue({ xml_path: '/attempt-a/config.xml', xml_content: '<config>A</config>' })
  const user = userEvent.setup(); render(<ArtifactBrowser experimentId="e1" mode={mode} />)
  if (mode === 'images') await screen.findByRole('img', { name: '全局 ENU' })
  else await screen.findByText('/attempt-a/config.xml')
  await selectOption(user, '日期任务', '2024 / DOY 351 · pending')
  expect(screen.queryByRole('img', { name: '全局 ENU' })).not.toBeInTheDocument()
  expect(screen.queryByText('/attempt-a/config.xml')).not.toBeInTheDocument()
  expect(screen.queryByText(/正在读取真实产物/)).not.toBeInTheDocument()
})
