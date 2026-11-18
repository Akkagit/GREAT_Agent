import type { AnalysisStatus, ExperimentMonitorEvent, ExperimentMonitorSnapshot, MonitorTask, TaskAttempt } from './types'

export class MonitorProtocolError extends Error {
  readonly upgrade: boolean
  constructor(message = '监控数据无效，正在重新同步。', upgrade = false) { super(message); this.upgrade = upgrade }
}
export interface SseMessage { id: string; event: string; data: string }

/** Incremental SSE line parser. Memory is bounded even without a terminating newline. */
export class SseParser {
  private decoder = new TextDecoder('utf-8', { fatal: true })
  private line = ''
  private skipLF = false
  private id = ''
  private event = 'message'
  private data: string[] = []
  private bytes = 0
  private first = true
  private readonly onMessage: (message: SseMessage) => void
  private readonly limit: number
  constructor(onMessage: (message: SseMessage) => void, limit = 1024 * 1024) { this.onMessage = onMessage; this.limit = limit }
  push(chunk: Uint8Array) { let decoded: string; try { decoded = this.decoder.decode(chunk, { stream: true }) } catch { throw new MonitorProtocolError('监控流 UTF-8 无效。') }; this.consume(decoded) }
  finish() { let decoded: string; try { decoded = this.decoder.decode() } catch { throw new MonitorProtocolError('监控流 UTF-8 不完整。') }; this.consume(decoded) /* SSE discards an incomplete final event. */ }
  private consume(value: string) {
    for (const char of value) {
      if (this.first) { this.first = false; if (char === '\uFEFF') continue }
      if (this.skipLF) { this.skipLF = false; if (char === '\n') continue }
      if (char === '\r' || char === '\n') { this.consumeLine(); this.skipLF = char === '\r' }
      else { this.line += char; if (this.line.length + this.bytes > this.limit) throw new MonitorProtocolError('监控事件超过大小限制。') }
    }
  }
  private consumeLine() {
    const line = this.line; this.line = ''
    if (line === '') {
      if (this.data.length) this.onMessage({ id: this.id, event: this.event, data: this.data.join('\n') })
      this.event = 'message'; this.data = []; this.bytes = 0; return
    }
    if (line.startsWith(':')) return
    const separator = line.indexOf(':')
    const field = separator < 0 ? line : line.slice(0, separator)
    let value = separator < 0 ? '' : line.slice(separator + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'data') { this.bytes += value.length + 1; if (this.bytes > this.limit) throw new MonitorProtocolError('监控事件超过大小限制。'); this.data.push(value) }
    else if (field === 'event') this.event = value
    else if (field === 'id' && !value.includes('\0')) this.id = value
  }
}

function object(value: unknown): value is Record<string, unknown> { return typeof value === 'object' && value !== null && !Array.isArray(value) }
const statuses = ['pending', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted']
const analyses: readonly unknown[] = ['unknown', 'not_started', 'running', 'completed', 'failed', 'skipped']
const kinds = new Set(['task.created', 'task.status', 'attempt.queued', 'attempt.started', 'task.cancel_requested', 'recovery.changed', 'analysis.started', 'analysis.completed', 'analysis.failed', 'analysis.skipped', 'artifacts.changed'])
const nonnegative = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0
const nullableString = (value: unknown) => value === null || typeof value === 'string'
function validRecovery(value: unknown) { return object(value) && typeof value.state === 'string' && nullableString(value.reason) && nullableString(value.last_checked_at) && nonnegative(value.version) }
function validAttempt(value: unknown): value is TaskAttempt {
  return object(value) && typeof value.id === 'string' && Number.isSafeInteger(value.number) && (value.number as number) > 0 && statuses.includes(value.status as string) && (value.exit_code === null || Number.isSafeInteger(value.exit_code)) && nullableString(value.error_code) && (value.analysis_status === undefined || analyses.includes(value.analysis_status)) && (value.artifact_version === undefined || nonnegative(value.artifact_version)) && (value.recovery === undefined || validRecovery(value.recovery))
}
function validTask(value: unknown, experimentId: string): value is MonitorTask {
  return object(value) && typeof value.id === 'string' && value.experiment_id === experimentId && Number.isSafeInteger(value.year) && Number.isSafeInteger(value.doy) && statuses.includes(value.status as string) && nullableString(value.active_attempt_id) && Array.isArray(value.attempts) && value.attempts.every(validAttempt) && (value.active_attempt_id === null || value.attempts.some(a => a.id === value.active_attempt_id)) && analyses.includes(value.analysis_status) && nonnegative(value.artifact_version) && validRecovery(value.recovery)
}
export function parseCursor(value: unknown, experimentId: string) {
  if (typeof value !== 'string') throw new MonitorProtocolError()
  const parts = /^v1:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):([^:]+):(0|[1-9][0-9]{0,18})$/i.exec(value)
  if (!parts || parts[2] !== experimentId || BigInt(parts[3]) > 9223372036854775807n) throw new MonitorProtocolError('实验监控游标无效。')
  return { epoch: parts[1], position: BigInt(parts[3]) }
}
export function validateSnapshot(value: unknown, experimentId: string): ExperimentMonitorSnapshot {
  if (!object(value) || value.experiment_id !== experimentId || !Array.isArray(value.tasks) || !value.tasks.every(task => validTask(task, experimentId)) || new Set(value.tasks.map(task => task.id)).size !== value.tasks.length) throw new MonitorProtocolError()
  parseCursor(value.cursor, experimentId)
  return value as unknown as ExperimentMonitorSnapshot
}
export function validateEvent(value: unknown, experimentId: string): ExperimentMonitorEvent {
  if (!object(value)) throw new MonitorProtocolError()
  if (typeof value.schema_version === 'number' && value.schema_version !== 1) throw new MonitorProtocolError('监控协议已更新，请升级或刷新页面。', true)
  if (value.schema_version !== 1 || value.experiment_id !== experimentId || !nullableString(value.task_id) || !nullableString(value.attempt_id) || typeof value.type !== 'string' || !object(value.data)) throw new MonitorProtocolError()
  parseCursor(value.event_id, experimentId)
  if (kinds.has(value.type) && (!validTask(value.data.task, experimentId) || value.data.task.id !== value.task_id || (value.attempt_id !== null && !value.data.task.attempts.some(attempt => attempt.id === value.attempt_id)))) throw new MonitorProtocolError()
  return value as unknown as ExperimentMonitorEvent
}
export function projectEvent(current: ExperimentMonitorSnapshot, event: ExperimentMonitorEvent): ExperimentMonitorSnapshot {
  const next = parseCursor(event.event_id, current.experiment_id), previous = parseCursor(current.cursor, current.experiment_id)
  if (next.epoch !== previous.epoch) throw new MonitorProtocolError('监控日志已更换，正在重新同步。')
  if (next.position <= previous.position) return current
  if (!kinds.has(event.type)) return { ...current, cursor: event.event_id }
  const task = event.data.task as MonitorTask
  const index = current.tasks.findIndex(item => item.id === task.id)
  return { ...current, cursor: event.event_id, tasks: index < 0 ? [...current.tasks, task] : current.tasks.map(item => item.id === task.id ? task : item) }
}
export function retryDelay(failure: number, random = Math.random, retryAfterMs?: number) {
  const base = [500, 1000, 2000, 4000, 8000, 15000][Math.min(failure, 5)]
  return Math.round(Math.min(15000, Math.max(base * (.8 + .4 * random()), retryAfterMs ?? 0)))
}
export const analysisLabels: Record<AnalysisStatus, string> = { unknown: '分析状态未知', not_started: '等待分析', running: '分析中', completed: '分析完成', failed: '分析失败', skipped: '已跳过分析' }

export interface MonitorStreamError { status?: number; retryAfterMs?: number; message?: string }
export class MonitorHttpError extends Error {
  readonly status: number
  readonly retryAfterMs?: number
  constructor(response: Response) {
    super(`实验监控 HTTP ${response.status}`)
    this.status = response.status
    const retry = response.headers.get('Retry-After')
    const delay = retry ? (/^\d+(\.\d+)?$/.test(retry) ? Number(retry) * 1000 : Math.max(0, Date.parse(retry) - Date.now())) : undefined
    if (Number.isFinite(delay)) this.retryAfterMs = delay
  }
}
/** Preserve status/Retry-After before attempting JSON; HTML error pages must not become network retries. */
export async function getMonitorSnapshot(baseUrl: string, experimentId: string, signal?: AbortSignal): Promise<ExperimentMonitorSnapshot> {
  const response = await fetch(`${baseUrl}/experiments/${encodeURIComponent(experimentId)}/monitor`, { headers: { Accept: 'application/json' }, signal })
  if (!response.ok) throw new MonitorHttpError(response)
  let value: unknown
  try { value = await response.json() }
  catch (error) { if (signal?.aborted) throw error; throw new MonitorProtocolError('监控快照 JSON 无效。') }
  return validateSnapshot(value, experimentId)
}
export function openMonitorStream(baseUrl: string, experimentId: string, cursor: string, onEvent: (event: unknown) => void, onError: (error: unknown) => void, signal: AbortSignal, onActivity?: () => void): () => void {
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal.addEventListener('abort', abort, { once: true }); if (signal.aborted) abort()
  void (async () => {
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
    try {
      const response = await fetch(`${baseUrl}/experiments/${encodeURIComponent(experimentId)}/events`, { headers: { Accept: 'text/event-stream', 'Last-Event-ID': cursor }, signal: controller.signal })
      if (!response.ok) throw new MonitorHttpError(response)
      if (!response.body || !response.headers.get('Content-Type')?.includes('text/event-stream')) throw new MonitorProtocolError('服务返回了无效的监控响应。')
      onActivity?.()
      const parser = new SseParser(message => {
        let event: unknown
        try { event = JSON.parse(message.data) } catch { throw new MonitorProtocolError() }
        const valid = validateEvent(event, experimentId)
        if (message.id !== valid.event_id || (message.event !== 'message' && message.event !== valid.type)) throw new MonitorProtocolError()
        onEvent(valid)
      })
      reader = response.body.getReader()
      while (!controller.signal.aborted) { const { done, value } = await reader.read(); if (done) { parser.finish(); break }; onActivity?.(); parser.push(value) }
      if (!controller.signal.aborted) onError(new Error('监控连接已断开。'))
    } catch (error) { if (!controller.signal.aborted) onError(error instanceof TypeError && error.message.includes('encoded') ? new MonitorProtocolError() : error) }
    finally { await reader?.cancel().catch(() => {}); signal.removeEventListener('abort', abort) }
  })()
  return () => { abort(); signal.removeEventListener('abort', abort) }
}
