import { MonitorProtocolError, projectEvent, retryDelay, validateEvent, validateSnapshot, type MonitorStreamError } from '../api/monitor'
import type { ExperimentMonitorSnapshot } from '../api/types'

export interface MonitorState { snapshot: ExperimentMonitorSnapshot | null; connection: 'loading' | 'connected' | 'reconnecting' | 'offline' | 'stopped'; error: string | null }
interface Transport {
  getSnapshot: (experimentId: string, signal: AbortSignal) => Promise<unknown>
  open: (experimentId: string, cursor: string, onEvent: (event: unknown) => void, onError: (error: unknown) => void, signal: AbortSignal, onActivity: () => void) => (() => void) | undefined
  random?: () => number
}
/** One lifetime-bound snapshot and stream. No per-task streams or unbounded event set. */
export class ExperimentMonitorSession {
  private readonly experimentId: string
  private readonly transport: Transport
  private state: MonitorState = { snapshot: null, connection: 'loading', error: null }
  private listeners = new Set<() => void>()
  private active = false
  private generation = 0
  private controller: AbortController | null = null
  private close: (() => void) | undefined
  private retryTimer: ReturnType<typeof setTimeout> | undefined
  private idleTimer: ReturnType<typeof setTimeout> | undefined
  private failures = 0
  private connectedAt: number | null = null
  private needsSnapshot = true
  constructor(experimentId: string, transport: Transport) { this.experimentId = experimentId; this.transport = transport }
  getSnapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private update(next: Partial<MonitorState>) { this.state = { ...this.state, ...next }; this.listeners.forEach(listener => listener()) }
  start() { if (this.active) return; this.active = true; window.addEventListener('offline', this.offline); window.addEventListener('online', this.online); void this.connect() }
  stop() { this.active = false; this.clearConnection(); window.removeEventListener('offline', this.offline); window.removeEventListener('online', this.online) }
  refresh = () => { if (!this.active) return; this.needsSnapshot = true; this.clearConnection(); this.update({ connection: 'loading', error: null }); void this.connect() }
  private clearConnection() { this.generation++; this.controller?.abort(); this.controller = null; this.close?.(); this.close = undefined; clearTimeout(this.retryTimer); clearTimeout(this.idleTimer); this.retryTimer = undefined; this.idleTimer = undefined }
  private offline = () => { if (!this.active || this.state.connection === 'stopped') return; this.clearConnection(); this.update({ connection: 'offline', error: '网络已断开，恢复后将继续同步。' }) }
  private online = () => { if (this.active && this.state.connection === 'offline') void this.connect() }
  private async connect() {
    if (!this.active) return
    if (navigator.onLine === false) { this.offline(); return }
    const generation = ++this.generation
    const controller = new AbortController(); this.controller = controller; this.connectedAt = null
    const current = () => this.active && this.generation === generation && !controller.signal.aborted
    const activity = () => {
      if (!current()) return
      if (this.connectedAt === null) this.connectedAt = Date.now()
      this.update({ connection: 'connected', error: null })
      clearTimeout(this.idleTimer)
      this.idleTimer = setTimeout(() => { if (current()) this.fail(new Error('监控心跳超时。')) }, 45000)
    }
    try {
      if (this.needsSnapshot || !this.state.snapshot) {
        const raw = await this.transport.getSnapshot(this.experimentId, controller.signal)
        if (!current()) return
        this.update({ snapshot: validateSnapshot(raw, this.experimentId) }); this.needsSnapshot = false
      }
      if (!current()) return
      this.idleTimer = setTimeout(() => { if (current()) this.fail(new Error('监控连接超时。')) }, 45000)
      this.close = this.transport.open(this.experimentId, this.state.snapshot!.cursor, raw => {
        if (!current()) return
        try { const event = validateEvent(raw, this.experimentId); const snapshot = projectEvent(this.state.snapshot!, event); activity(); this.update({ snapshot }) }
        catch (error) { this.fail(error) }
      }, error => { if (current()) this.fail(error) }, controller.signal, activity)
    } catch (error) { if (current()) this.fail(error) }
  }
  private fail(error: unknown) {
    if (!this.active) return
    const status = (error as MonitorStreamError | null)?.status
    const upgrade = error instanceof MonitorProtocolError && error.upgrade
    if (this.connectedAt !== null && Date.now() - this.connectedAt >= 10000) this.failures = 0
    this.clearConnection()
    if (upgrade || (status !== undefined && status >= 400 && status < 500 && status !== 409 && status !== 429)) {
      this.update({ connection: 'stopped', error: upgrade ? error.message : `监控已停止：HTTP ${status}，请刷新页面或检查服务。` }); return
    }
    if (status === 409 || error instanceof MonitorProtocolError) this.needsSnapshot = true
    this.update({ connection: 'reconnecting', error: error instanceof Error ? error.message : '监控连接中断，正在重试。' })
    if (navigator.onLine === false) { this.offline(); return }
    const delay = retryDelay(this.failures++, this.transport.random, (error as MonitorStreamError | null)?.retryAfterMs)
    this.retryTimer = setTimeout(() => { this.retryTimer = undefined; void this.connect() }, delay)
  }
}
