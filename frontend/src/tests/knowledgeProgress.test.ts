/// <reference types="node" />
import { ReadableStream as NodeReadableStream } from 'node:stream/web'
import { afterEach, expect, it, vi } from 'vitest'
import { publicRetrieval, watchKnowledgeProgress } from '../monitoring/knowledgeProgress'

const ReadableStream = NodeReadableStream as unknown as typeof globalThis.ReadableStream

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('honors Retry-After before reconnecting and cancels pending retries with its owner', async () => {
  vi.useFakeTimers(); vi.spyOn(Math, 'random').mockReturnValue(.5)
  const fetchMock = vi.fn().mockResolvedValue(new Response('', { status: 429, headers: { 'Retry-After': '2' } }))
  vi.stubGlobal('fetch', fetchMock)
  const owner = new AbortController()
  watchKnowledgeProgress('chat-a', { baseId: 'base-a', requestId: 'request-a', signal: owner.signal, onProgress() {} })
  await vi.advanceTimersByTimeAsync(1999)
  expect(fetchMock).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(1)
  expect(fetchMock).toHaveBeenCalledTimes(2)
  owner.abort()
  await vi.advanceTimersByTimeAsync(30000)
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

it('replays from its cursor without letting a historical terminal event stop the current turn', async () => {
  vi.useFakeTimers(); vi.spyOn(Math, 'random').mockReturnValue(.5)
  const headers: string[] = []
  const updates: string[] = []
  let stream!: ReadableStreamDefaultController<Uint8Array>
  let connections = 0
  vi.stubGlobal('fetch', vi.fn(async (_input: string, init: RequestInit) => {
    headers.push((init.headers as Record<string, string>)['Last-Event-ID'])
    connections++
    return new Response(new ReadableStream({ start(controller) { stream = controller } }), { headers: { 'Content-Type': 'text/event-stream' } })
  }))
  const owner = new AbortController()
  watchKnowledgeProgress('chat-a', { baseId: 'base-a', requestId: 'new', signal: owner.signal, onProgress: stage => updates.push(stage) })
  await vi.advanceTimersByTimeAsync(0)
  stream.enqueue(new TextEncoder().encode('id: 1\nevent: completed\ndata: {"request_id":"old"}\n\nid: 2\nevent: retrieving\ndata: {"request_id":"new"}\n\n'))
  await vi.advanceTimersByTimeAsync(0)
  expect(updates).toEqual(['retrieving'])
  stream.close()
  await vi.advanceTimersByTimeAsync(499)
  expect(connections).toBe(1)
  await vi.advanceTimersByTimeAsync(1)
  expect(headers).toEqual(['0', '2'])
  stream.enqueue(new TextEncoder().encode('id: 3\nevent: failed\ndata: {"request_id":"new"}\n\n'))
  await vi.advanceTimersByTimeAsync(30000)
  expect(updates).toEqual(['retrieving', 'failed'])
  expect(connections).toBe(2)
  owner.abort()
})

it('projects only public authorized metadata and rejects another base or invalid counts', () => {
  const summary = { base_id: 'a', base_name: '科研库', scope_mode: 'selected', document_count: 2, hit_count: 5, evidence_count: 1, queries: ['private'], messages: [{ role: 'system', text: 'internal' }] }
  expect(publicRetrieval(summary, 'a')).toEqual({ base_id: 'a', base_name: '科研库', scope_mode: 'selected', document_count: 2, hit_count: 5, evidence_count: 1 })
  expect(publicRetrieval({ ...summary, available: false }, 'a')).toEqual({ base_id: 'a', base_name: '科研库', scope_mode: 'selected', document_count: 2, available: false })
  expect(publicRetrieval(summary, 'other')).toBeUndefined()
  expect(publicRetrieval({ ...summary, document_count: -1 }, 'a')).toBeUndefined()
})
