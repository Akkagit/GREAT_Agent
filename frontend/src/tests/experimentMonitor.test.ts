import { afterEach, describe, expect, it, vi } from 'vitest'
import { SseParser, MonitorProtocolError, openMonitorStream, parseCursor, projectEvent, retryDelay, validateSnapshot, validateEvent, type SseMessage } from '../api/monitor'
import { ExperimentMonitorSession } from '../monitoring/session'

const epoch = '11111111-1111-4111-8111-111111111111'
const cursor = (id: string) => `v1:${epoch}:e1:${id}`
const task = { id: 't1', experiment_id: 'e1', year: 2024, doy: 350, status: 'succeeded', active_attempt_id: 'a1', attempts: [{ id: 'a1', number: 1, status: 'succeeded', exit_code: 0, error_code: null, analysis_status: 'not_started', artifact_version: 0 }], analysis_status: 'not_started', artifact_version: 0, recovery: { state: 'none', reason: null, last_checked_at: null, version: 0 } }
const snapshot = () => validateSnapshot({ experiment_id: 'e1', cursor: cursor('9007199254740993'), tasks: [task] }, 'e1')
const event = (id = '9007199254740995', type = 'analysis.completed') => validateEvent({ schema_version: 1, event_id: cursor(id), experiment_id: 'e1', task_id: 't1', attempt_id: 'a1', type, data: { task: { ...task, analysis_status: 'completed', artifact_version: 1, attempts: [{ ...task.attempts[0], analysis_status: 'completed', artifact_version: 1 }] } } }, 'e1')

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('experiment SSE protocol', () => {
  it('decodes split UTF-8, CRLF/CR/LF, multiline data, comments and field whitespace', () => {
    const messages: SseMessage[] = []
    const parser = new SseParser(message => messages.push(message))
    const bytes = new TextEncoder().encode(': 心跳\r\nid: 7\revent: task.status\rdata: {"text":\r\ndata: "中文"}\n\n')
    for (const byte of bytes) parser.push(new Uint8Array([byte]))
    parser.finish()
    expect(messages).toEqual([{ id: '7', event: 'task.status', data: '{"text":\n"中文"}' }])
  })
  it('bounds both unterminated lines and accumulated event data', () => {
    expect(() => new SseParser(() => {}, 64).push(new TextEncoder().encode('data: ' + 'x'.repeat(65)))).toThrow(MonitorProtocolError)
    const parser = new SseParser(() => {}, 64)
    expect(() => parser.push(new TextEncoder().encode(('data: ' + 'x'.repeat(20) + '\n').repeat(4)))).toThrow(MonitorProtocolError)
  })
  it('uses BigInt cursor positions and accepts global gaps without regressing duplicate events', () => {
    expect(parseCursor(cursor('9007199254740993'), 'e1').position).toBe(9007199254740993n)
    const current = snapshot(), next = projectEvent(current, event())
    expect(next.tasks[0].analysis_status).toBe('completed')
    expect(projectEvent(next, event())).toBe(next)
    expect(projectEvent(next, event('9007199254740994'))).toBe(next)
    expect(() => parseCursor(cursor('1'), 'other')).toThrow(MonitorProtocolError)
  })
  it('ignores unknown v1 event types only after validation, and stops unknown schema', () => {
    const current = snapshot(), unknown = event('9007199254740995', 'future.informational')
    expect(projectEvent(current, unknown)).toEqual({ ...current, cursor: unknown.event_id })
    expect(() => validateEvent({ ...unknown, schema_version: 2 }, 'e1')).toThrow(/升级/)
    expect(() => validateEvent({ ...unknown, type: 'task.status', data: { status: 'invented' } }, 'e1')).toThrow(MonitorProtocolError)
  })
  it('uses bounded exponential delays with jitter and capped Retry-After', () => {
    expect([0, 1, 2, 3, 4, 5, 20].map(failure => retryDelay(failure, () => .5))).toEqual([500, 1000, 2000, 4000, 8000, 15000, 15000])
    expect(retryDelay(0, () => 0)).toBe(400)
    expect(retryDelay(0, () => 1)).toBe(600)
    expect(retryDelay(0, () => .5, 999999)).toBe(15000)
  })
  it('sends the snapshot cursor in Last-Event-ID and validates streamed envelopes', async () => {
    const received: unknown[] = [], failures: unknown[] = []
    const envelope = event()
    const bytes = new TextEncoder().encode(`: heartbeat\r\nid: ${envelope.event_id}\r\nevent: ${envelope.type}\r\ndata: ${JSON.stringify(envelope)}\r\n\r\n`)
    let sent = false
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, body: { getReader: () => ({ read: async () => { if (sent) return { done: true }; sent = true; return { done: false, value: bytes } }, cancel: async () => {} }) }, headers: { get: () => 'text/event-stream' } })
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController(), activity = vi.fn()
    openMonitorStream('/api/v1', 'e1', cursor('9007199254740993'), item => received.push(item), error => failures.push(error), controller.signal, activity)
    for (let i = 0; i < 12; i++) await Promise.resolve()
    expect(fetchMock.mock.calls[0][1].headers['Last-Event-ID']).toBe(cursor('9007199254740993'))
    expect(received).toEqual([envelope]); expect(activity.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(failures).toHaveLength(1)
    controller.abort()
  })
  it('rejects malformed UTF-8 and incomplete final bytes without silently consuming a cursor', () => {
    const parser = new SseParser(() => {})
    expect(() => parser.push(new Uint8Array([0xff]))).toThrow(MonitorProtocolError)
    const incomplete = new SseParser(() => {})
    incomplete.push(new Uint8Array([0xe4])); expect(() => incomplete.finish()).toThrow(MonitorProtocolError)
  })
})

describe('shared experiment session', () => {
  it.each(['404', 'upgrade'])('keeps a permanent %s stop across offline/online until explicit refresh', async kind => {
    vi.useFakeTimers()
    let fail!: (error: unknown) => void, receive!: (value: unknown) => void
    const getSnapshot = vi.fn().mockResolvedValue(snapshot())
    const open = vi.fn((_id, _cursor, onEvent, onError) => { fail = onError; receive = onEvent; return () => {} })
    const session = new ExperimentMonitorSession('e1', { getSnapshot, open, random: () => .5 })
    session.start(); await vi.advanceTimersByTimeAsync(0)
    if (kind === '404') fail({ status: 404 }); else receive({ ...event(), schema_version: 2 })
    const stopped = session.getSnapshot()
    expect(stopped.connection).toBe('stopped')
    window.dispatchEvent(new Event('offline')); window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(60000)
    expect(session.getSnapshot()).toEqual(stopped); expect(open).toHaveBeenCalledOnce()
    session.refresh(); await vi.advanceTimersByTimeAsync(0)
    expect(getSnapshot).toHaveBeenCalledTimes(2); expect(open).toHaveBeenCalledTimes(2); session.stop()
  })
  it('opens one stream for 100 terminal tasks and applies later manual analysis with cursor resume', async () => {
    vi.useFakeTimers()
    const aborts: AbortSignal[] = [], events: Array<(event: unknown) => void> = [], errors: Array<(error: unknown) => void> = []
    const getSnapshot = vi.fn().mockResolvedValue({ ...snapshot(), tasks: Array.from({ length: 100 }, (_, i) => ({ ...task, id: `t${i + 1}` })) })
    const open = vi.fn((_id, _cursor, onEvent, onError, signal) => { events.push(onEvent); errors.push(onError); aborts.push(signal); return () => {} })
    const session = new ExperimentMonitorSession('e1', { getSnapshot, open, random: () => .5 })
    session.start(); await vi.advanceTimersByTimeAsync(0)
    expect(open).toHaveBeenCalledTimes(1)
    events[0](event()); expect(session.getSnapshot().snapshot?.tasks[0].analysis_status).toBe('completed')
    errors[0](new Error('offline'))
    await vi.advanceTimersByTimeAsync(499); expect(open).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1); expect(open.mock.calls[1][1]).toBe(cursor('9007199254740995'))
    session.stop(); expect(aborts.every(signal => signal.aborted)).toBe(true)
    await vi.advanceTimersByTimeAsync(60000); expect(open).toHaveBeenCalledTimes(2)
  })
  it('resnapshots a rejected cursor and stops on permanent protocol or HTTP errors', async () => {
    vi.useFakeTimers()
    let fail!: (error: unknown) => void
    const getSnapshot = vi.fn().mockResolvedValue(snapshot())
    const open = vi.fn((_id, _cursor, _onEvent, onError, _signal, onActivity) => { fail = onError; onActivity(); return () => {} })
    const session = new ExperimentMonitorSession('e1', { getSnapshot, open, random: () => .5 })
    session.start(); await vi.advanceTimersByTimeAsync(0)
    fail({ status: 409 }); await vi.advanceTimersByTimeAsync(500)
    expect(getSnapshot).toHaveBeenCalledTimes(2)
    fail({ status: 404 }); await vi.advanceTimersByTimeAsync(60000)
    expect(open).toHaveBeenCalledTimes(2); expect(session.getSnapshot().connection).toBe('stopped')
    session.stop()
  })
  it('backs off short connections, resets after stability and detects idle streams', async () => {
    vi.useFakeTimers()
    let fail!: (error: unknown) => void
    const open = vi.fn((_id, _cursor, _onEvent, onError, _signal, onActivity) => { fail = onError; onActivity(); return () => {} })
    const session = new ExperimentMonitorSession('e1', { getSnapshot: async () => snapshot(), open, random: () => .5 })
    session.start(); await vi.advanceTimersByTimeAsync(0)
    fail(new Error()); await vi.advanceTimersByTimeAsync(500)
    fail(new Error()); await vi.advanceTimersByTimeAsync(999); expect(open).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(1); await vi.advanceTimersByTimeAsync(10000)
    fail(new Error()); await vi.advanceTimersByTimeAsync(500); expect(open).toHaveBeenCalledTimes(4)
    await vi.advanceTimersByTimeAsync(45000); expect(session.getSnapshot().connection).toBe('reconnecting')
    session.stop()
  })
  it('pauses retry offline and ignores late responses after switching or stopping', async () => {
    vi.useFakeTimers()
    let resolve!: (value: unknown) => void
    const open = vi.fn()
    const session = new ExperimentMonitorSession('e1', { getSnapshot: () => new Promise(done => { resolve = done }), open })
    session.start(); session.stop(); resolve(snapshot()); await vi.advanceTimersByTimeAsync(0)
    expect(open).not.toHaveBeenCalled()
    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true })
    const resumed = new ExperimentMonitorSession('e1', { getSnapshot: async () => snapshot(), open })
    resumed.start(); await vi.advanceTimersByTimeAsync(60000); expect(open).not.toHaveBeenCalled()
    Object.defineProperty(navigator, 'onLine', { value: true, configurable: true }); window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(0); expect(open).toHaveBeenCalledOnce(); resumed.stop()
  })
  it('honors Retry-After, keeps the budget through offline pause and stops for upgrade', async () => {
    vi.useFakeTimers()
    let fail!: (error: unknown) => void, receive!: (value: unknown) => void
    const open = vi.fn((_id, _cursor, onEvent, onError) => { fail = onError; receive = onEvent; return () => {} })
    const session = new ExperimentMonitorSession('e1', { getSnapshot: async () => snapshot(), open, random: () => .5 })
    session.start(); await vi.advanceTimersByTimeAsync(0)
    fail({ status: 429, retryAfterMs: 3000 }); await vi.advanceTimersByTimeAsync(2999)
    expect(open).toHaveBeenCalledOnce(); await vi.advanceTimersByTimeAsync(1); expect(open).toHaveBeenCalledTimes(2)
    fail(new Error('short failure')); window.dispatchEvent(new Event('offline'))
    await vi.advanceTimersByTimeAsync(60000); expect(open).toHaveBeenCalledTimes(2)
    window.dispatchEvent(new Event('online')); await vi.advanceTimersByTimeAsync(0)
    fail(new Error('short failure')); await vi.advanceTimersByTimeAsync(1999); expect(open).toHaveBeenCalledTimes(3)
    await vi.advanceTimersByTimeAsync(1); expect(open).toHaveBeenCalledTimes(4)
    receive({ ...event(), schema_version: 2 }); await vi.advanceTimersByTimeAsync(60000)
    expect(open).toHaveBeenCalledTimes(4); expect(session.getSnapshot().connection).toBe('stopped'); session.stop()
  })
})
