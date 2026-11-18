import type { AnalysisJob, ConversationDetail, ConversationTurn, CreatedExperiment, ExecutionPreview, ExperimentSpec, ExperimentSummary, ExperimentView, Proposal, ResultFile, Revision, TaskDetail, TaskEvent, TaskSummary, ValidationResult } from './types'
import { getMonitorSnapshot, openMonitorStream } from './monitor'

export const browserConfig = Object.freeze({
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL ?? '/api/v1',
})

export type ModelProposalErrorCategory = 'schema' | 'invalid_json' | 'timeout' | 'provider'
const proposalErrorCategories:readonly string[]=['schema','invalid_json','timeout','provider']

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly category?: ModelProposalErrorCategory
  readonly requestId?: string

  constructor(message: string, code: string, status: number, diagnostics?: {category?:unknown;request_id?:unknown}) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    if(status===502 && code==='model_proposal_error'){
      if(typeof diagnostics?.category==='string' && proposalErrorCategories.includes(diagnostics.category))this.category=diagnostics.category as ModelProposalErrorCategory
      const requestId=diagnostics?.request_id
      if(typeof requestId==='string' && requestId.length===36 && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(requestId))this.requestId=requestId
    }
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
      error,
    )
  }
  return body as T
}

export const api = {
  ensureConversation: (experimentId:string|null) => request<ConversationDetail>('/conversations', {method:'POST',body:JSON.stringify({experiment_id:experimentId})}),
  getConversation: (id:string) => request<ConversationDetail>(`/conversations/${id}`),
  sendConversationMessage: (id:string,message:string,clientRequestId:string,signal?:AbortSignal,executionPreviewId?:string) => request<ConversationTurn>(`/conversations/${id}/messages`,{method:'POST',body:JSON.stringify({message,client_request_id:clientRequestId,...(executionPreviewId!==undefined?{execution_preview_id:executionPreviewId}:{})}),signal}),
  listExperiments: () => request<ExperimentSummary[]>('/experiments'),
  createExperiment: (spec: ExperimentSpec) => request<CreatedExperiment>('/experiments', {
    method: 'POST', body: JSON.stringify(spec),
  }),
  getExperiment: (id: string) => request<ExperimentView>(`/experiments/${id}`),
  proposeChat: (id: string, message: string, signal?: AbortSignal) => request<Proposal>('/chat/proposals', {
    method: 'POST', body: JSON.stringify({ experiment_id: id, message }), signal,
  }),
  listProposals: (id: string, status?: Proposal['status']) => request<Proposal[]>(`/experiments/${id}/proposals${status ? `?status=${encodeURIComponent(status)}` : ''}`),
  getProposal: (id: string, proposalId: string) => request<Proposal>(`/experiments/${id}/proposals/${proposalId}`),
  rejectProposal: (id: string, proposalId: string) => request<Proposal>(`/experiments/${id}/proposals/${proposalId}/reject`, { method: 'POST' }),
  previewProposal: (id: string, proposal: { source: 'card'; candidate: ExperimentSpec; base_revision_id: string; source_proposal_id?: string }) =>
    request<Proposal>(`/experiments/${id}/proposals`, {
      method: 'POST', body: JSON.stringify(proposal),
    }),
  acceptRevision: (id: string, input: { proposal_id: string; expected_revision: number }) =>
    request<Revision>(`/experiments/${id}/revisions`, {
      method: 'POST', body: JSON.stringify(input),
    }),
  validateExperiment: (id: string, input: {expected_revision_id:string;expected_revision:number}) => request<ValidationResult>(`/experiments/${id}/validate`, { method: 'POST', body:JSON.stringify(input) }),
  freezeExperiment: (id: string) => request<Revision>(`/experiments/${id}/freeze`, { method: 'POST' }),
  prepareExecution: (id: string, input: { expected_revision: number; task_id?: string }) => request<ExecutionPreview>(`/experiments/${id}/execution-preview`, { method: 'POST', body: JSON.stringify(input) }),
  getExecutionPreview: (id: string, previewId: string) => request<ExecutionPreview>(`/experiments/${id}/execution-previews/${previewId}`),
  confirmExecution: (id: string, input: { preview_id: string; expected_revision: number; confirmed: true }) => request<{ tasks: string[]; count: number }>(`/experiments/${id}/run`, { method: 'POST', body: JSON.stringify(input) }),
  listTasks: (id: string) => request<TaskSummary[]>(`/experiments/${id}/tasks`),
  getExperimentMonitor: (id: string, signal?: AbortSignal) => getMonitorSnapshot(browserConfig.apiBaseUrl, id, signal),
  openExperimentEvents: (id: string, cursor: string, onEvent: (event: unknown) => void, onError: (error: unknown) => void, signal: AbortSignal, onActivity?: () => void) => openMonitorStream(browserConfig.apiBaseUrl, id, cursor, onEvent, onError, signal, onActivity),
  getTaskRecovery: (id: string) => request<{ recovery: import('./types').RecoverySummary; allowed_actions: string[] }>(`/tasks/${id}/recovery`),
  recheckTaskRecovery: (id: string, input: { expected_attempt_id: string; request_id: string }) => request<unknown>(`/tasks/${id}/recovery/recheck`, { method: 'POST', body: JSON.stringify(input) }),
  stopTaskRecovery: (id: string, input: { expected_attempt_id: string; request_id: string }) => request<unknown>(`/tasks/${id}/recovery/stop`, { method: 'POST', body: JSON.stringify(input) }),
  getTask: (id: string) => request<TaskDetail>(`/tasks/${id}`),
  listAnalysisJobs: (taskId: string) => request<{ items: AnalysisJob[] }>(`/tasks/${encodeURIComponent(taskId)}/analysis-jobs`).then(response => response.items),
  retryAnalysisJob: (taskId: string, jobId: string) => request<AnalysisJob>(`/tasks/${encodeURIComponent(taskId)}/analysis-jobs/${encodeURIComponent(jobId)}/retry`, { method: 'POST' }),
  getTaskLog: (taskId: string, attemptId: string) => request<{ content: string }>(`/tasks/${taskId}/attempts/${attemptId}/log`).then((item) => item.content),
  getTaskResults: (taskId: string, attemptId: string) => request<ResultFile[]>(`/tasks/${taskId}/attempts/${attemptId}/results`),
  getTaskXml: (taskId: string, attemptId: string) => request<{ xml_path: string; xml_content: string }>(`/tasks/${taskId}/attempts/${attemptId}/xml`),
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
