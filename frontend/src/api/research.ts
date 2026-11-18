import { ApiError, browserConfig } from './client'

export type ResearchMaterial =
  | { kind: 'experiment'; experiment_id: string; task_id: string; attempt_id: string; role: string }
  | { kind: 'code'; source_id: string; paths: string[]; provenance: string; snapshot_run_id?: string; snapshot_material_id?: string }
  | { kind: 'literature'; document_id: string; version_id: string }
/** Material identity follows JSON values; repository serialization may reorder object keys. */
export function researchMaterialKey(value: ResearchMaterial | ResearchMaterial[]): string {
  return JSON.stringify(value, (_key, nested) => nested && typeof nested === 'object' && !Array.isArray(nested)
    ? Object.fromEntries(Object.keys(nested).sort().map(key => [key, nested[key]])) : nested)
}
export interface ResearchCatalog {
  experiments: { id: string; title: string; tasks: { id: string; year: number; doy: number; attempts: { id: string; number: number; status: string }[] }[] }[]
  sources: { id: string; name: string }[]
  documents: { id: string; title: string; base_id: string; version_id: string }[]
  profiles: { id: string; name: string; configured: boolean; capabilities?: Record<string, unknown> }[]
}
export interface ResearchBlock { id: string; type: string; text: string; evidence_ids?: string[] }
export interface ResearchMessage { id: string; run_id: string; role: string; status: string; text: string; blocks: ResearchBlock[]; sequence: number }
export interface ResearchEvidenceRef { run_id: string; alias: string; evidence_id: string }
export interface ResearchSuggestionFields { target?: string; rationale?: string; change?: string; impact?: string; validation?: string }
export interface ResearchSuggestion extends ResearchSuggestionFields { id?: string; text: string; support?: string[]; opposition?: string[] }
export interface ResearchItem extends ResearchSuggestionFields { id: string; kind: string; text: string; status: string; support: string[]; opposition: string[]; support_refs?: ResearchEvidenceRef[]; opposition_refs?: ResearchEvidenceRef[]; unknowns?: string[]; origin_run_id?: string; revision?: number; quote?: string }
export interface ResearchReport { id: string; session_id: string; title: string; content: string; run_ids: string[]; version?: number; created_at?: string; snapshot?: { goal?: string; runs?: ResearchRun[]; items?: ResearchItem[]; evidence?: (ResearchEvidence & { run_id: string })[] } }
export interface ResearchSession {
  id: string; title: string; goal: string; version: number; material_revision: number; state_revision: number
  settings: { model_profile_id: string; mode: 'quick' | 'deep' }; materials: ResearchMaterial[]; material_summary: Record<string, unknown>[]
  active_run_id: string | null; messages: ResearchMessage[]; items: ResearchItem[]; reports: ResearchReport[]; runs?: ResearchRun[]; stage?: string
}
export interface ResearchTarget { run_id: string; message_id?: string; block_id?: string; item_id?: string }
export interface ResearchRunInput { request_id: string; expected_version: number; message: string; followup_target?: ResearchTarget }
export interface ResearchRun {
  id: string; session_id: string; request_id: string; status: string; stage: string; message: string
  model_profile_id: string; mode: string; snapshot_summary?: Record<string, unknown>[]; error_code?: string; error_message?: string; retry_of?: string
  result?: { answer: string; answer_blocks: ResearchBlock[]; observations?: unknown[]; hypotheses?: unknown[]; suggestions?: ResearchSuggestion[]; open_questions?: unknown[]; limitations?: string[]; answer_status?: string; source_ids?: string[] }
  usage?: Record<string, unknown>
}
export interface ResearchEvidence { id: string; alias: string; kind: string; title: string; text: string; locator: Record<string, unknown>; sha256: string; quality_flags: string[]; available: boolean }
const root = `${browserConfig.apiBaseUrl}/research`
const id = encodeURIComponent
async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`${root}${path}`, { method, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) })
  const value = await response.json().catch(() => null)
  if (!response.ok) throw new ApiError(value?.error?.message ?? `请求失败（${response.status}）`, value?.error?.code ?? 'research_error', response.status)
  return value as T
}
export const researchApi = {
  catalog: () => request<ResearchCatalog>('/catalog'),
  list: () => request<{ items: ResearchSession[] }>('/sessions'),
  create: (input: { title?: string; goal?: string; materials?: ResearchMaterial[]; model_profile_id?: string; mode?: 'quick' | 'deep' }) => request<ResearchSession>('/sessions', 'POST', input),
  get: (sessionId: string) => request<ResearchSession>(`/sessions/${id(sessionId)}`),
  materials: (sessionId: string, input: { expected_version: number; materials: ResearchMaterial[] }) => request<ResearchSession>(`/sessions/${id(sessionId)}/materials`, 'PATCH', input),
  settings: (sessionId: string, input: { expected_version: number; model_profile_id: string; mode: 'quick' | 'deep' }) => request<ResearchSession>(`/sessions/${id(sessionId)}/settings`, 'PATCH', input),
  send: (sessionId: string, input: ResearchRunInput) => request<ResearchRun>(`/sessions/${id(sessionId)}/runs`, 'POST', input),
  run: (runId: string) => request<ResearchRun>(`/runs/${id(runId)}`),
  cancel: (runId: string) => request<ResearchRun>(`/runs/${id(runId)}/cancel`, 'POST', {}),
  retry: (runId: string, requestId: string) => request<ResearchRun>(`/runs/${id(runId)}/retry`, 'POST', { request_id: requestId }),
  evidence: (runId: string, alias: string) => request<ResearchEvidence>(`/runs/${id(runId)}/evidence/${id(alias)}`),
  reports: (sessionId: string) => request<{ items: ResearchReport[] }>(`/sessions/${id(sessionId)}/reports`),
  saveReport: (sessionId: string, input: { title?: string; run_ids?: string[] }) => request<ResearchReport>(`/sessions/${id(sessionId)}/reports`, 'POST', input),
}
