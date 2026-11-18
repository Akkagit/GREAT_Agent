import { afterEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../api/client'
import { ExperimentMonitorSession } from '../monitoring/session'
import { monitorCursor } from './monitorFixture'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

it('preserves snapshot Retry-After and waits for it before retrying initial synchronization', async () => {
  vi.useFakeTimers()
  const json = vi.fn().mockResolvedValue({ error: { code: 'rate_limit', message: 'slow down' } })
  const fetchMock = vi.fn().mockResolvedValueOnce({ ok: false, status: 429, headers: { get: () => '3' }, json })
    .mockResolvedValue({ ok: true, status: 200, json: async () => ({ experiment_id: 'e1', cursor: monitorCursor('e1'), tasks: [] }) })
  vi.stubGlobal('fetch', fetchMock)
  const open = vi.fn().mockReturnValue(() => {})
  const session = new ExperimentMonitorSession('e1', { getSnapshot: api.getExperimentMonitor, open, random: () => .5 })
  session.start(); await vi.advanceTimersByTimeAsync(0)
  expect(session.getSnapshot().connection).toBe('reconnecting')
  await vi.advanceTimersByTimeAsync(2999); expect(fetchMock).toHaveBeenCalledOnce(); expect(open).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(1); expect(fetchMock).toHaveBeenCalledTimes(2); expect(open).toHaveBeenCalledOnce(); session.stop()
})

it('permanently stops snapshot synchronization on an HTML 404 without depending on JSON parsing', async () => {
  vi.useFakeTimers()
  const json = vi.fn().mockRejectedValue(new SyntaxError('Unexpected <'))
  const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 404, headers: { get: () => null }, json })
  vi.stubGlobal('fetch', fetchMock)
  const session = new ExperimentMonitorSession('e1', { getSnapshot: api.getExperimentMonitor, open: vi.fn(), random: () => .5 })
  session.start(); await vi.advanceTimersByTimeAsync(0)
  expect(session.getSnapshot().connection).toBe('stopped')
  expect(session.getSnapshot().error).toContain('404')
  expect(json).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(60000); expect(fetchMock).toHaveBeenCalledOnce(); session.stop()
})

it('honors an HTTP-date Retry-After while resnapshotting an expired stream cursor', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-12T00:00:00Z'))
  const initial = { experiment_id: 'e1', cursor: monitorCursor('e1'), tasks: [] }
  const fetchMock = vi.fn().mockResolvedValueOnce({ ok: true, status: 200, json: async () => initial })
    .mockResolvedValueOnce({ ok: false, status: 429, headers: { get: () => 'Thu, 12 Nov 2026 00:00:04 GMT' } })
    .mockResolvedValue({ ok: true, status: 200, json: async () => ({ ...initial, cursor: monitorCursor('e1', '8') }) })
  vi.stubGlobal('fetch', fetchMock)
  let fail!: (error: unknown) => void
  const open = vi.fn((_id, _cursor, _onEvent, onError) => { fail = onError; return () => {} })
  const session = new ExperimentMonitorSession('e1', { getSnapshot: api.getExperimentMonitor, open, random: () => .5 })
  session.start(); await vi.advanceTimersByTimeAsync(0)
  fail({ status: 409 }); await vi.advanceTimersByTimeAsync(500)
  expect(fetchMock).toHaveBeenCalledTimes(2)
  await vi.advanceTimersByTimeAsync(3499); expect(fetchMock).toHaveBeenCalledTimes(2)
  await vi.advanceTimersByTimeAsync(1)
  expect(fetchMock).toHaveBeenCalledTimes(3); expect(open.mock.calls.at(-1)?.[1]).toBe(monitorCursor('e1', '8')); session.stop()
})

it('retains the ordinary API error code and diagnostics contract', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ error: { code: 'revision_conflict', message: 'changed' } }) }))
  await expect(api.getExperiment('e1')).rejects.toMatchObject({ name: 'ApiError', code: 'revision_conflict', status: 409, message: 'changed' })
  await expect(api.getExperiment('e1')).rejects.toBeInstanceOf(ApiError)
})
