import { ApiError, browserConfig } from './client'

export interface KnowledgeBase { id: string; name: string; created_at: string }
export type KnowledgeJobStatus = 'queued' | 'parsing' | 'indexing' | 'completed' | 'completed_with_warnings' | 'failed'
export interface KnowledgeJob {
  id: string
  document_id: string
  version_id: string | null
  status: KnowledgeJobStatus
  error_code?: string | null
  error_message?: string | null
  warnings?: string[]
}
export interface KnowledgeDocument {
  id: string
  base_id: string
  filename: string
  title?: string | null
  enabled: boolean
  active_version_id: string | null
  status: string
  latest_job?: KnowledgeJob | null
  page_count?: number
  chunk_count?: number
}
export interface KnowledgeLocation { page_index: number; page_label: string; bbox?: number[] | null }
export interface KnowledgeChunk {
  id?: string
  chunk_id?: string
  document_id: string
  version_id: string
  title?: string
  text: string
  section?: string | null
  locations: KnowledgeLocation[]
  quality_flags: string[]
  score?: number
}
export interface KnowledgeAccepted { document: KnowledgeDocument; job: KnowledgeJob; duplicate: boolean }
export type KnowledgeParser = 'pdfplumber' | 'docling'

const root = `${browserConfig.apiBaseUrl}/knowledge`
const idPath = (id: string) => encodeURIComponent(id)

// File uploads use this client directly so their bytes never pass through the JSON client.
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${root}${path}`, init)
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const error = body?.error ?? body?.detail
    const message = typeof error === 'string' ? error : error?.message
    throw new ApiError(message ?? `请求失败（${response.status}）`, error?.code ?? 'knowledge_request_failed', response.status)
  }
  return body as T
}
function jsonRequest(method: string, body: unknown, signal?: AbortSignal): RequestInit {
  return { method, body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' }, signal }
}

export const knowledgeApi = {
  listBases: (signal?: AbortSignal) => request<{ items: KnowledgeBase[] }>('/bases', { signal }),
  createBase: (name: string, signal?: AbortSignal) => request<KnowledgeBase>('/bases', jsonRequest('POST', { name }, signal)),
  listDocuments: (baseId: string, signal?: AbortSignal) => request<{ items: KnowledgeDocument[] }>(`/bases/${idPath(baseId)}/documents`, { signal }),
  upload: (baseId: string, file: File, signal?: AbortSignal) => {
    const extension = file.name.split('.').at(-1)?.toLowerCase()
    const mediaType = file.type || (extension === 'pdf' ? 'application/pdf' : extension === 'md' ? 'text/markdown' : 'text/plain')
    return request<KnowledgeAccepted>(`/bases/${idPath(baseId)}/documents?filename=${encodeURIComponent(file.name)}`, {
      method: 'POST', body: file, headers: { 'Content-Type': mediaType }, signal,
    })
  },
  getDocument: (id: string, signal?: AbortSignal) => request<KnowledgeDocument>(`/documents/${idPath(id)}`, { signal }),
  setEnabled: (id: string, enabled: boolean, signal?: AbortSignal) => request<KnowledgeDocument>(`/documents/${idPath(id)}`, jsonRequest('PATCH', { enabled }, signal)),
  reparse: (id: string, parser: KnowledgeParser, requestId: string, signal?: AbortSignal) => request<KnowledgeAccepted>(`/documents/${idPath(id)}/reparse`, jsonRequest('POST', { parser, request_id: requestId }, signal)),
  getJob: (id: string, signal?: AbortSignal) => request<KnowledgeJob>(`/jobs/${idPath(id)}`, { signal }),
  search: (baseId: string, query: string, signal?: AbortSignal) => request<{ mode: 'lexical'; items: KnowledgeChunk[] }>(`/bases/${idPath(baseId)}/search`, jsonRequest('POST', { query, limit: 8 }, signal)),
  listChunks: (id: string, versionId: string, offset: number, signal?: AbortSignal) => request<{ items: KnowledgeChunk[]; total: number }>(`/documents/${idPath(id)}/chunks?version_id=${idPath(versionId)}&offset=${offset}&limit=20`, { signal }),
  getChunk: (id: string, signal?: AbortSignal) => request<KnowledgeChunk>(`/chunks/${idPath(id)}`, { signal }),
  originalUrl: (id: string, pageIndex?: number) => `${root}/documents/${idPath(id)}/original${pageIndex === undefined ? '' : `#page=${pageIndex + 1}`}`,
}
