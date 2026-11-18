import { MonitorHttpError, retryDelay, SseParser } from '../api/monitor'
import { knowledgeConversationEventsUrl } from '../api/knowledgeChat'
import type { KnowledgeRetrievalSummary } from '../api/knowledgeChat'

export const terminalKnowledgeStages = new Set(['completed', 'failed', 'interrupted', 'cancelled', 'timed_out'])
const stages = new Set(['started', 'retrieving', 'generating', ...terminalKnowledgeStages])
const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0

/** Only the server's public retrieval fields enter the reading surface. */
export function publicRetrieval(value: unknown, baseId: string): KnowledgeRetrievalSummary | undefined {
  if (!object(value) || value.base_id !== baseId || typeof value.base_name !== 'string' || !['selected', 'all_enabled'].includes(String(value.scope_mode)) || !count(value.document_count)) return
  return {
    base_id: baseId, base_name: value.base_name, scope_mode: value.scope_mode as KnowledgeRetrievalSummary['scope_mode'], document_count: value.document_count,
    ...(value.available === false ? { available: false } : {
      ...(count(value.hit_count) ? { hit_count: value.hit_count } : {}),
      ...(count(value.evidence_count) ? { evidence_count: value.evidence_count } : {}),
      ...(typeof value.mode === 'string' ? { mode: value.mode } : {}),
    }),
  }
}

interface Options {
  baseId: string; requestId?: string; runId?: string; signal: AbortSignal
  onProgress: (stage: string, retrieval?: KnowledgeRetrievalSummary) => void
  onDisconnect?: () => void
}

/** One active conversation stream. Replay catches short retrieval phases;
 * request/run identity excludes older turns. Terminal events end its lifetime. */
export function watchKnowledgeProgress(conversationId: string, options: Options) {
  let cursor = '0', failures = 0, stopped = false
  let controller: AbortController | undefined
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  let retryTimer: ReturnType<typeof setTimeout> | undefined
  const close = () => { stopped = true; controller?.abort(); void reader?.cancel().catch(() => {}); clearTimeout(retryTimer); options.signal.removeEventListener('abort', close) }
  options.signal.addEventListener('abort', close, { once: true })
  if (options.signal.aborted) { close(); return close }
  async function connect() {
    if (stopped) return
    controller = new AbortController()
    try {
      const response = await fetch(knowledgeConversationEventsUrl(conversationId), { signal: controller.signal, headers: { Accept: 'text/event-stream', 'Last-Event-ID': cursor } })
      if (stopped) return
      if (!response.ok) throw new MonitorHttpError(response)
      if (!response.body || !response.headers.get('Content-Type')?.includes('text/event-stream')) throw new Error('文献进度连接无效。')
      reader = response.body.getReader()
      const parser = new SseParser(message => {
        if (stopped || !/^\d+$/.test(message.id) || !stages.has(message.event)) return
        cursor = message.id
        const data: unknown = JSON.parse(message.data)
        if (!object(data) || (options.requestId ? data.request_id !== options.requestId : options.runId ? data.run_id !== options.runId : true)) return
        options.onProgress(message.event, publicRetrieval(data.retrieval, options.baseId))
        failures = 0
        if (terminalKnowledgeStages.has(message.event)) close()
      })
      while (!stopped) { const next = await reader.read(); if (next.done) { parser.finish(); break }; parser.push(next.value) }
      if (!stopped) throw new Error('文献进度连接中断。')
    } catch (error) {
      if (stopped) return
      controller?.abort(); await reader?.cancel().catch(() => {}); reader = undefined
      options.onDisconnect?.()
      // A scope/permission error cannot be repaired by reconnecting.
      if (error instanceof MonitorHttpError && error.status >= 400 && error.status < 500 && error.status !== 429) { close(); return }
      retryTimer = setTimeout(() => void connect(), retryDelay(failures++, Math.random, error instanceof MonitorHttpError ? error.retryAfterMs : undefined))
    }
  }
  void connect()
  return close
}

export function retrievalLabel(summary: KnowledgeRetrievalSummary) {
  const scope = summary.scope_mode === 'selected' ? `所选 ${summary.document_count} 篇` : `已启用 ${summary.document_count} 篇`
  if (summary.available === false) return `「${summary.base_name}」· ${scope} · 当前来源不可用`
  return `「${summary.base_name}」· ${scope}${summary.hit_count === undefined ? '' : ` · 命中 ${summary.hit_count} 条`}${summary.evidence_count === undefined ? '' : ` · 证据 ${summary.evidence_count} 条`}`
}
