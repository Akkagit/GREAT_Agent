import type { ExperimentSpec, ExperimentView, Proposal, ResultFile, Revision, TaskDetail, TaskEvent, TaskSummary, ValidationResult } from './types'

export const browserConfig = Object.freeze({
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL ?? '/api/v1',
})

export class ApiError extends Error {
  readonly code: string
  readonly status: number

  constructor(message: string, code: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${browserConfig.apiBaseUrl}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  const body = await response.json()
  if (!response.ok) {
    const error = body?.error
    throw new ApiError(
      error?.message ?? `Request failed with status ${response.status}`,
      error?.code ?? 'request_failed',
      response.status,
    )
  }
  return body as T
}

export const api = {
  getExperiment: (id: string) => request<ExperimentView>(`/experiments/${id}`),
  proposeChat: (id: string, message: string) => request<Proposal>('/chat/proposals', {
    method: 'POST', body: JSON.stringify({ experiment_id: id, message }),
  }),
  previewProposal: (id: string, proposal: { source: 'card'; candidate: ExperimentSpec }) =>
    request<Proposal>(`/experiments/${id}/proposals`, {
      method: 'POST', body: JSON.stringify(proposal),
    }),
  acceptRevision: (id: string, input: { proposal_id: string; expected_revision: number }) =>
    request<Revision>(`/experiments/${id}/revisions`, {
      method: 'POST', body: JSON.stringify(input),
    }),
  validateExperiment: (id: string) => request<ValidationResult>(`/experiments/${id}/validate`, { method: 'POST' }),
  freezeExperiment: (id: string) => request<Revision>(`/experiments/${id}/freeze`, { method: 'POST' }),
  listTasks: (id: string) => request<TaskSummary[]>(`/experiments/${id}/tasks`),
  getTask: (id: string) => request<TaskDetail>(`/tasks/${id}`),
  getTaskLog: (taskId: string, attemptId: string) => request<{ content: string }>(`/tasks/${taskId}/attempts/${attemptId}/log`).then((item) => item.content),
  getTaskResults: (taskId: string, attemptId: string) => request<ResultFile[]>(`/tasks/${taskId}/attempts/${attemptId}/results`),
  retryTask: (id: string) => request<{ status: string }>(`/tasks/${id}/retry`, { method: 'POST' }),
  stopExperiment: (id: string) => request<{ status: string }>(`/experiments/${id}/stop`, { method: 'POST' }),
  openTaskEvents(
    taskId: string,
    lastEventId: string | undefined,
    onEvent: (event: TaskEvent) => void,
    onDisconnect: () => void,
  ): () => void {
    const controller = new AbortController()
    void fetch(`${browserConfig.apiBaseUrl}/tasks/${taskId}/events`, {
      headers: { Accept: 'text/event-stream', ...(lastEventId ? { 'Last-Event-ID': lastEventId } : {}) },
      signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok || !response.body) throw new Error(`SSE ${response.status}`)
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''
      while (true) {
        const { done, value } = await reader.read(); buffer += decoder.decode(value, { stream: !done })
        const blocks = buffer.split('\n\n'); buffer = blocks.pop() ?? ''
        for (const block of blocks) {
          let id = ''; let event = 'message'; const data: string[] = []
          for (const line of block.split('\n')) {
            if (line.startsWith('id:')) id = line.slice(3).trim()
            else if (line.startsWith('event:')) event = line.slice(6).trim()
            else if (line.startsWith('data:')) data.push(line.slice(5).trim())
          }
          if (id && data.length) onEvent({ id, event, data: JSON.parse(data.join('\n')) })
        }
        if (done) break
      }
      if (!controller.signal.aborted) onDisconnect()
    }).catch(() => { if (!controller.signal.aborted) onDisconnect() })
    return () => controller.abort()
  },
}
