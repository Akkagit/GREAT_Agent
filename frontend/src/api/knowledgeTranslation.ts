import { ApiError, browserConfig } from './client'

export type TranslationLanguage = 'zh-CN' | 'en'
export interface KnowledgeTranslation {
  original_text: string
  translated_text: string
  target_language: TranslationLanguage
  evidence_text_sha256: string
  model_profile: string
  translation_version: string
  cached: boolean
}

/** Translate one server-verified excerpt. Never send the original document or a browser key. */
export async function translateKnowledgeCitation(conversationId: string, messageId: string, sourceId: string, target: TranslationLanguage, original: string, signal: AbortSignal): Promise<KnowledgeTranslation> {
  const path = [conversationId, messageId, sourceId].map(encodeURIComponent)
  const response = await fetch(`${browserConfig.apiBaseUrl}/knowledge/conversations/${path[0]}/messages/${path[1]}/citations/${path[2]}/translation`, {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ target_language: target }),
  })
  const value = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = value?.error ?? value?.detail
    throw new ApiError(typeof detail === 'string' ? detail : detail?.message ?? `翻译失败（${response.status}）`, detail?.code ?? value?.code ?? 'knowledge_translation_error', response.status)
  }
  if (!value || value.original_text !== original || value.target_language !== target || typeof value.translated_text !== 'string' || !value.translated_text.trim() || !/^[a-f0-9]{64}$/i.test(value.evidence_text_sha256 ?? '')) {
    throw new Error('译文校验失败，请重新核验来源后重试。')
  }
  if (!globalThis.crypto?.subtle) throw new Error('译文校验失败：此浏览器环境无法验证原文，请通过本机 localhost 或 HTTPS 打开。')
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(original))
  const hex = Array.from(new Uint8Array(hash), value => value.toString(16).padStart(2, '0')).join('')
  if (value.evidence_text_sha256.toLowerCase() !== hex) throw new Error('译文校验失败，请重新核验来源后重试。')
  return value as KnowledgeTranslation
}
