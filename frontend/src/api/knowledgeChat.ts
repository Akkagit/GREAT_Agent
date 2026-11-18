import { ApiError, browserConfig } from './client'
import type { KnowledgeLocation } from './knowledge'

export interface KnowledgeModelProfile { id: string; name: string; configured: boolean; location: string; provider: string; capabilities?: Record<string, unknown> }
export interface KnowledgeBinding { base_id: string; scope_mode: 'selected' | 'all_enabled'; document_ids: string[]; model_profile_id: string; binding_revision: number }
export interface KnowledgeFormulaTranscription {
  latex: string; equation_number: string; page_index: number; bbox: number[]
  source_sha256: string; image_sha256: string; model: string
  status: 'locally_reviewed' | 'machine_transcription'
}
export interface KnowledgeCitation {
  type: 'citation'; source_id: string; document_id: string; version_id: string; chunk_id: string
  title: string; filename: string; evidence_text: string; source_sha256: string
  locations: KnowledgeLocation[]; quality_flags: string[]; available?: boolean; reason?: string; unavailable_reason?: string
  formula_transcriptions?: KnowledgeFormulaTranscription[]
}
export interface KnowledgeMessage { id: string; role: string; status: string; request_id?: string; blocks: ({ type: 'text'; text: string } | KnowledgeCitation)[] }
export interface KnowledgeRetrievalSummary { base_id: string; base_name: string; scope_mode: 'selected' | 'all_enabled'; document_count: number; queries?: string[]; hit_count?: number; evidence_count?: number; documents?: unknown[]; mode?: string; available?: boolean }
export interface KnowledgeConversation { id: string; title: string; binding: KnowledgeBinding; messages: KnowledgeMessage[]; active_run_id: string | null; stage?: string; retrieval_mode?: string; retrieval_summary?: KnowledgeRetrievalSummary }
export interface KnowledgeTurn { conversation_id: string; run_id: string; request_id: string; status: string; user_message: KnowledgeMessage; assistant_message: KnowledgeMessage | null; timings?: Record<string, unknown>; retrieval_mode?: string; retrieval_summary?: KnowledgeRetrievalSummary }
export interface KnowledgePage { page_index: number; page_label: string; width: number; height: number; role: string; raw_text: string; coordinate_space?: string; to_pdf_transform?: number[] | null }
export interface KnowledgeSourceSelection { conversationId: string; messageId: string; citation: KnowledgeCitation; pageIndex?: number }
export function uniqueCitationPages(locations: KnowledgeLocation[]) {
  const pages = new Map<number, KnowledgeLocation>()
  for (const location of locations) if (!pages.has(location.page_index)) pages.set(location.page_index, location)
  return [...pages.values()]
}
/** Restore the parser's display coordinates to PDF user space before viewport crop/rotation/UserUnit/scale. */
export function citationBoxStyle(bbox: number[], pageHeight: number, viewport: { convertToViewportRectangle?: (rectangle: number[]) => number[]; convertToViewportPoint?: (x: number, y: number) => number[] }, toPdfTransform?: number[] | null) {
  const [x0, y0, x1, y1] = bbox
  let pdfRectangle: number[]
  if (toPdfTransform) {
    const [a, b, c, d, e, f] = toPdfTransform
    pdfRectangle = [a * x0 + c * y0 + e, b * x0 + d * y0 + f, a * x1 + c * y1 + e, b * x1 + d * y1 + f]
  } else {
    // Compatibility for older unit fixtures. Real unverified page frames are never highlighted.
    pdfRectangle = [x0, pageHeight - y1, x1, pageHeight - y0]
  }
  // PDF.js 6 exposes point conversion; earlier versions also expose rectangle conversion.
  const rectangle = viewport.convertToViewportRectangle ? viewport.convertToViewportRectangle(pdfRectangle)
    : [...viewport.convertToViewportPoint!(pdfRectangle[0], pdfRectangle[1]), ...viewport.convertToViewportPoint!(pdfRectangle[2], pdfRectangle[3])]
  return { left: Math.min(rectangle[0], rectangle[2]), top: Math.min(rectangle[1], rectangle[3]), width: Math.abs(rectangle[2] - rectangle[0]), height: Math.abs(rectangle[3] - rectangle[1]) }
}
const root = `${browserConfig.apiBaseUrl}/knowledge`
export const knowledgeConversationEventsUrl = (id: string) => `${root}/conversations/${encodeURIComponent(id)}/events`
const pathId = encodeURIComponent
async function request<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${root}${path}`, { method, signal, ...(body === undefined ? {} : { body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } }) })
  const value = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = value?.error ?? value?.detail
    throw new ApiError(typeof detail === 'string' ? detail : detail?.message ?? `请求失败（${response.status}）`, detail?.code ?? 'knowledge_chat_error', response.status, typeof detail === 'object' ? detail : undefined)
  }
  return value as T
}
export const knowledgeChatApi = {
  models: (signal?: AbortSignal) => request<{ items: KnowledgeModelProfile[] }>('/model-profiles', 'GET', undefined, signal),
  testModel: (id: string, signal?: AbortSignal) => request<{ configured?: boolean; ok?: boolean; message?: string; status?: string }>(`/model-profiles/${pathId(id)}/test`, 'POST', {}, signal),
  list: (baseId: string, signal?: AbortSignal) => request<{ items: KnowledgeConversation[] }>(`/conversations?base_id=${pathId(baseId)}`, 'GET', undefined, signal),
  create: (binding: Omit<KnowledgeBinding, 'binding_revision'>, signal?: AbortSignal) => request<KnowledgeConversation>('/conversations', 'POST', binding, signal),
  get: (id: string, signal?: AbortSignal) => request<KnowledgeConversation>(`/conversations/${pathId(id)}`, 'GET', undefined, signal),
  bind: (id: string, binding: Omit<KnowledgeBinding, 'base_id' | 'binding_revision'> & { expected_revision: number }, signal?: AbortSignal) => request<KnowledgeConversation>(`/conversations/${pathId(id)}/binding`, 'PATCH', binding, signal),
  send: (id: string, message: string, requestId: string, signal?: AbortSignal) => request<KnowledgeTurn>(`/conversations/${pathId(id)}/messages`, 'POST', { message, request_id: requestId }, signal),
  cancel: (id: string, requestId: string, signal?: AbortSignal) => request<{ status: string }>(`/conversations/${pathId(id)}/cancel`, 'POST', { request_id: requestId }, signal),
  citation: (conversationId: string, messageId: string, sourceId: string, signal?: AbortSignal) => request<KnowledgeCitation>(`/conversations/${pathId(conversationId)}/messages/${pathId(messageId)}/citations/${pathId(sourceId)}`, 'GET', undefined, signal),
  page: (documentId: string, versionId: string, pageIndex: number, signal?: AbortSignal) => request<KnowledgePage>(`/documents/${pathId(documentId)}/versions/${pathId(versionId)}/pages/${pageIndex}`, 'GET', undefined, signal),
}
