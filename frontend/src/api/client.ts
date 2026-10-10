import type { ExperimentSpec, ExperimentView, Proposal, Revision, ValidationResult } from './types'

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
}
