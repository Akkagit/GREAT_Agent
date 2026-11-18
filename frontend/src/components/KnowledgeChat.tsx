import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Select, Tag } from 'antd'
import { ApiError } from '../api/client'
import { knowledgeChatApi, uniqueCitationPages } from '../api/knowledgeChat'
import type { KnowledgeBinding, KnowledgeCitation, KnowledgeConversation, KnowledgeFormulaTranscription, KnowledgeMessage, KnowledgeModelProfile, KnowledgeRetrievalSummary, KnowledgeSourceSelection } from '../api/knowledgeChat'
import { publicRetrieval, retrievalLabel, terminalKnowledgeStages, watchKnowledgeProgress } from '../monitoring/knowledgeProgress'
import { MarkdownContent } from './MarkdownContent'

const stageLabels: Record<string, string> = { queued: '等待处理', started: '正在准备检索', retrieving: '正在检索文献', retrieval: '正在检索文献', generating: '模型正在生成回答', generation: '模型正在生成回答', completed: '已完成', failed: '生成失败', cancelled: '已停止', interrupted: '回答已中断', timed_out: '回答超时' }
const messageOf = (error: unknown) => error instanceof Error ? error.message : '请求失败，请重试。'
const draftStore = new Map<string, string>()
function readDraft(key: string) { try { return sessionStorage.getItem(`knowledge-draft:${key}`) ?? draftStore.get(key) ?? '' } catch { return draftStore.get(key) ?? '' } }
function writeDraft(key: string, value: string) { draftStore.set(key, value); try { sessionStorage.setItem(`knowledge-draft:${key}`, value) } catch { /* Draft stays in memory when storage is unavailable. */ } }

function validFormula(value: unknown, citation: KnowledgeCitation): value is KnowledgeFormulaTranscription {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const formula = value as Record<string, unknown>
  return typeof formula.latex === 'string' && Boolean(formula.latex.trim()) && formula.latex.length <= 12000
    && typeof formula.equation_number === 'string' && Boolean(formula.equation_number.trim().replace(/^\((.*)\)$/, '$1').trim())
    && Number.isSafeInteger(formula.page_index) && Number(formula.page_index) >= 0
    && Array.isArray(formula.bbox) && formula.bbox.length === 4 && formula.bbox.every(item => typeof item === 'number' && Number.isFinite(item)) && formula.bbox[2] > formula.bbox[0] && formula.bbox[3] > formula.bbox[1]
    && typeof formula.source_sha256 === 'string' && /^[a-f\d]{64}$/i.test(formula.source_sha256) && typeof citation.source_sha256 === 'string' && formula.source_sha256.toLowerCase() === citation.source_sha256.toLowerCase()
    && typeof formula.image_sha256 === 'string' && /^[a-f\d]{64}$/i.test(formula.image_sha256)
    && typeof formula.model === 'string' && Boolean(formula.model.trim())
    && (formula.status === 'locally_reviewed' || formula.status === 'machine_transcription')
}

function messageFormulas(message: KnowledgeMessage) {
  const formulas = new Map<string, { formula: KnowledgeFormulaTranscription; citation: KnowledgeCitation; number: string }>()
  if (message.role !== 'assistant') return []
  for (const citation of message.blocks) {
    if (citation.type !== 'citation' || citation.available === false || !Array.isArray(citation.formula_transcriptions)) continue
    for (const formula of citation.formula_transcriptions) {
      if (!validFormula(formula, citation)) continue
      const number = formula.equation_number.trim().replace(/^\((.*)\)$/, '$1').trim()
      const key = `${formula.source_sha256.toLowerCase()}:${formula.page_index}:${number}`
      const previous = formulas.get(key)
      if (!previous || (previous.formula.status !== 'locally_reviewed' && formula.status === 'locally_reviewed')) formulas.set(key, { formula, citation, number })
    }
  }
  return [...formulas.entries()]
}

interface Props {
  baseId: string; baseName?: string; chatId?: string; binding: Omit<KnowledgeBinding, 'binding_revision'>
  profiles: KnowledgeModelProfile[]; onModelChange: (id: string) => void
  onRestoreBinding: (binding: KnowledgeBinding) => void; onCreated: (conversation: KnowledgeConversation) => void
  onSource: (source: KnowledgeSourceSelection | null) => void
}
export function KnowledgeChat({ baseId, baseName, chatId, binding, profiles, onModelChange, onRestoreBinding, onCreated, onSource }: Props) {
  const [conversation, setConversation] = useState<KnowledgeConversation | null>(null)
  const [draft, setDraft] = useState(() => readDraft(`${baseId}:${chatId ?? 'new'}`))
  const [loading, setLoading] = useState(Boolean(chatId))
  const [busy, setBusy] = useState(false)
  const [pendingQuestion, setPendingQuestion] = useState<{ text: string; requestId: string; baseId: string; chatId?: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [stage, setStage] = useState('')
  const [retrieval, setRetrieval] = useState<KnowledgeRetrievalSummary>()
  const [mode, setMode] = useState<string | undefined>()
  const [testNotice, setTestNotice] = useState('')
  const [testing, setTesting] = useState(false)
  const identity = useRef('')
  const current = useRef<KnowledgeConversation | null>(null)
  const scope = useRef<AbortController | null>(null)
  const turn = useRef<{ controller: AbortController; requestId: string; conversationId: string; message: string } | null>(null)
  const retry = useRef<{ message: string; requestId: string; binding: string } | null>(null)
  const draftKey = useRef(`${baseId}:${chatId ?? 'new'}`)
  const profile = profiles.find(item => item.id === binding.model_profile_id)
  const validScope = binding.scope_mode === 'all_enabled' || binding.document_ids.length > 0

  useEffect(() => {
    const key = `${baseId}:${chatId ?? 'new'}`
    // A newly created conversation becomes the routed conversation without interrupting its first turn.
    if (chatId && current.current?.id === chatId && current.current.binding.base_id === baseId) {
      setPendingQuestion(value => value ? { ...value, chatId } : value)
      if (draftKey.current !== key) { writeDraft(key, readDraft(draftKey.current)); writeDraft(draftKey.current, ''); draftKey.current = key }
      const controller = new AbortController(); scope.current = controller
      return () => controller.abort()
    }
    identity.current = key
    const controller = new AbortController(); scope.current = controller
    if (turn.current && !readDraft(draftKey.current)) writeDraft(draftKey.current, turn.current.message)
    turn.current?.controller.abort(); turn.current = null; retry.current = null
    current.current = null; setConversation(null); setError(null); setBusy(false); setPendingQuestion(null); setMode(undefined); setStage(''); setRetrieval(undefined)
    draftKey.current = key; setDraft(readDraft(key)); setLoading(Boolean(chatId))
    if (chatId) knowledgeChatApi.get(chatId, controller.signal).then(value => {
      if (controller.signal.aborted) return
      if (value.binding.base_id !== baseId) { setError('此对话不属于当前文献库，请从当前库的历史对话重新选择。'); setLoading(false); return }
      current.current = value; setConversation(value); onRestoreBinding(value.binding)
      setMode(value.retrieval_mode); setStage(value.stage ?? ''); setRetrieval(publicRetrieval(value.retrieval_summary, baseId)); setLoading(false)
    }).catch(error => { if (!controller.signal.aborted) { setError(messageOf(error)); setLoading(false) } })
    return () => { controller.abort() }
  }, [baseId, chatId, onRestoreBinding])
  useEffect(() => () => { if (turn.current && !readDraft(draftKey.current)) writeDraft(draftKey.current, turn.current.message); scope.current?.abort(); turn.current?.controller.abort() }, [])

  const activeId = conversation?.active_run_id
  const conversationId = conversation?.id
  useEffect(() => {
    if (!conversationId || (!busy && !activeId)) return
    const controller = new AbortController(); let timer: number | undefined
    const requestId = turn.current?.requestId
    const close = watchKnowledgeProgress(conversationId, { baseId, requestId, runId: activeId ?? undefined, signal: controller.signal, onProgress: (nextStage, summary) => {
      if (controller.signal.aborted) return
      setStage(nextStage)
      if (summary) { setRetrieval(summary); if (summary.mode) setMode(summary.mode) }
      if (terminalKnowledgeStages.has(nextStage)) {
        window.clearTimeout(timer)
        if (!busy && current.current) { const value = { ...current.current, active_run_id: null, stage: nextStage }; current.current = value; setConversation(value) }
        const owner = identity.current, signal = scope.current?.signal
        void knowledgeChatApi.get(conversationId, signal).then(value => {
          if (signal?.aborted || identity.current !== owner || (turn.current && turn.current.requestId !== requestId)) return
          if (!busy) { current.current = { ...value, active_run_id: null }; setConversation(current.current) }
          if (value.retrieval_summary) setRetrieval(publicRetrieval(value.retrieval_summary, baseId))
        }).catch(() => {})
      }
    } })
    async function poll() {
      try {
        const value = await knowledgeChatApi.get(conversationId!, controller.signal)
        if (controller.signal.aborted) return
        if (activeId && value.active_run_id && value.active_run_id !== activeId) return
        // A pre-send historical snapshot must not overwrite this turn's events.
        if (requestId && !value.messages.some(item => item.request_id === requestId)) { timer = window.setTimeout(() => void poll(), 1500); return }
        setStage(value.stage ?? '')
        if (value.retrieval_summary) setRetrieval(publicRetrieval(value.retrieval_summary, baseId))
        if (!busy && !value.active_run_id) { current.current = value; setConversation(value); setMode(value.retrieval_mode); return }
      } catch { if (controller.signal.aborted) return }
      timer = window.setTimeout(() => void poll(), 1500)
    }
    timer = window.setTimeout(() => void poll(), 1500)
    return () => { close(); controller.abort(); window.clearTimeout(timer) }
  }, [baseId, conversationId, activeId, busy])

  function edit(value: string) { setDraft(value); writeDraft(draftKey.current, value) }
  function restoreQuestion(message: string) { if (!readDraft(draftKey.current)) edit(message) }
  async function send() {
    if (loading || busy || conversation?.active_run_id || !draft.trim() || !validScope || !profile?.configured) return
    const controller = new AbortController(), owner = identity.current, message = draft.trim()
    const bindingKey = JSON.stringify(binding)
    const requestId = retry.current?.message === message && retry.current.binding === bindingKey ? retry.current.requestId : crypto.randomUUID()
    retry.current = { message, requestId, binding: bindingKey }
    turn.current = { controller, requestId, conversationId: current.current?.id ?? '', message }
    setBusy(true); setPendingQuestion({ text: message, requestId, baseId, chatId }); edit(''); setError(null); setStage('started'); setRetrieval(undefined)
    let chat = current.current
    try {
      if (!chat) {
        chat = await knowledgeChatApi.create(binding, controller.signal)
        if (controller.signal.aborted || identity.current !== owner) return
        current.current = chat; setConversation(chat); onCreated(chat)
      } else if (chat.binding.scope_mode !== binding.scope_mode || chat.binding.model_profile_id !== binding.model_profile_id || JSON.stringify([...chat.binding.document_ids].sort()) !== JSON.stringify([...binding.document_ids].sort())) {
        chat = await knowledgeChatApi.bind(chat.id, { expected_revision: chat.binding.binding_revision, scope_mode: binding.scope_mode, document_ids: binding.document_ids, model_profile_id: binding.model_profile_id }, controller.signal)
        if (controller.signal.aborted || identity.current !== owner) return
        current.current = chat; setConversation(chat); onSource(null)
      }
      turn.current = { controller, requestId, conversationId: chat.id, message }
      const result = await knowledgeChatApi.send(chat.id, message, requestId, controller.signal)
      if (controller.signal.aborted || identity.current !== owner || result.conversation_id !== chat.id || result.request_id !== requestId) return
      const updated = { ...chat, active_run_id: null, stage: result.status, retrieval_summary: result.retrieval_summary, messages: [...chat.messages.filter(item => item.request_id !== requestId && item.id !== result.user_message?.id && item.id !== result.assistant_message?.id), ...[result.user_message, result.assistant_message].filter(item => item != null)] }
      current.current = updated; setConversation(updated); setStage(result.status); setMode(result.retrieval_mode ?? (typeof result.timings?.retrieval_mode === 'string' ? result.timings.retrieval_mode : undefined))
      if (result.retrieval_summary) setRetrieval(publicRetrieval(result.retrieval_summary, baseId))
      if (result.status === 'completed') { retry.current = null }
      else { restoreQuestion(message); if (['failed', 'interrupted', 'cancelled', 'timed_out'].includes(result.status)) retry.current = null; setError(stageLabels[result.status] ?? '回答未完成，请检查状态后重试。') }
      if (!result.retrieval_summary) void knowledgeChatApi.get(chat.id, controller.signal).then(value => {
        if (controller.signal.aborted || identity.current !== owner) return
        if (value.retrieval_summary) setRetrieval(publicRetrieval(value.retrieval_summary, baseId))
      }).catch(() => {})
    } catch (error) {
      if (!controller.signal.aborted && identity.current === owner) {
        // Only the server's matching terminal model error closes this request; a proxy 502 remains uncertain.
        if (error instanceof ApiError && error.status === 502 && error.code === 'model_proposal_error' && error.requestId === requestId) retry.current = null
        restoreQuestion(message)
        setError(messageOf(error))
      }
    }
    finally { if (!controller.signal.aborted && identity.current === owner) { turn.current = null; setPendingQuestion(null); setBusy(false) } }
  }
  async function cancel() {
    const owner = identity.current, signal = scope.current?.signal
    const owned = turn.current
    const id = owned?.conversationId ?? conversation?.id
    const requestId = owned?.requestId ?? [...(conversation?.messages ?? [])].reverse().find(item => item.role === 'user')?.request_id
    if (!id || !requestId) { setError('正在恢复请求信息，请稍后重试停止。'); return }
    try {
      await knowledgeChatApi.cancel(id, requestId, signal)
      if (signal?.aborted || identity.current !== owner) return
      if (owned) restoreQuestion(owned.message)
      owned?.controller.abort(); turn.current = null; retry.current = null; setPendingQuestion(null); setBusy(false); setStage('cancelled')
      if (current.current) { const value = { ...current.current, active_run_id: null }; current.current = value; setConversation(value) }
    } catch (error) { if (!signal?.aborted && identity.current === owner) setError(messageOf(error)) }
  }
  async function testModel() {
    const owner = identity.current, signal = scope.current?.signal
    setTesting(true); setTestNotice('')
    try {
      const result = await knowledgeChatApi.testModel(binding.model_profile_id, signal)
      if (!signal?.aborted && identity.current === owner) setTestNotice(result.ok === true || result.status === 'ok' ? '模型连接测试通过。' : result.message ?? '模型连接未通过，请检查服务器配置。')
    } catch (error) { if (!signal?.aborted && identity.current === owner) setTestNotice(messageOf(error)) }
    finally { if (!signal?.aborted && identity.current === owner) setTesting(false) }
  }
  return <section className="kw-chat" aria-label="文献对话">
    <div className="kw-model-row"><label>回答模型<Select aria-label="回答模型" value={binding.model_profile_id} virtual={false} onChange={onModelChange} options={profiles.map(item => ({ value: item.id, label: `${item.name}（${item.location === 'cloud' ? '云端' : '本地'}${item.configured ? '' : ' · 未配置'}）` }))} /></label><Button size="small" loading={testing} disabled={!profile} onClick={() => void testModel()}>测试连接</Button></div>
    {testNotice && <p role="status">{testNotice}</p>}
    <div className="kw-binding"><Tag color="blue">{binding.scope_mode === 'selected' ? '仅所选文献' : '整个库中已启用文献'}</Tag><span>{binding.scope_mode === 'selected' ? `已选 ${binding.document_ids.length} 篇` : '按已启用范围检索'}{conversation ? ` · 绑定修订 ${conversation.binding.binding_revision}` : ''}</span></div>
    <p className="kw-hint">范围与模型更改用于下一轮；历史回答保留当时的文献版本。</p>
    {!profile?.configured && <Alert type="warning" title="请在服务器配置模型凭据，再刷新模型列表。页面不显示或保存 API Key。" />}
    {!validScope && <p className="kw-warning">请勾选至少一篇可检索文献。</p>}
    {error && <Alert type="error" title={error} role="alert" />}
    <div className="kw-messages" aria-live="polite" aria-busy={loading || busy}>
      {loading && <p role="status">正在恢复文献对话…</p>}
      {!loading && !pendingQuestion && !conversation?.messages.length && <div className="kw-chat-empty"><h2>从文献证据开始提问</h2><p>选择可检索文献，提问后可点击引用核对原文、页码与解析质量。</p></div>}
      {conversation?.messages.map(message => <article key={message.id} className={`kw-message kw-message-${message.role}`} aria-label={message.role === 'user' ? '你的问题' : '文献回答'}>{message.role !== 'user' && <h3>文献回答{message.status !== 'completed' && <Tag>{stageLabels[message.status] ?? message.status}</Tag>}</h3>}{message.blocks.map((block, index) => block.type === 'text' ? message.role === 'user' ? <p className="kw-literal" key={index}>{block.text}</p> : <MarkdownContent key={index} className={index === 0 && block.text.startsWith("核心结论：") ? "kw-core-conclusion" : undefined}>{block.text}</MarkdownContent> : <button className="kw-citation" key={index} onClick={() => onSource({ conversationId: conversation.id, messageId: message.id, citation: block })}><span>[{block.source_id}]</span><span>{block.title || block.filename}<small>{uniqueCitationPages(block.locations).map(location => `第 ${location.page_label || location.page_index + 1} 页`).join('、') || '文字来源'} · 查看原文证据</small></span></button>)}{messageFormulas(message).map(([key, { formula, citation, number }]) => <section key={key} className="kw-original-formula" aria-label={`原文公式 (${number})`}>
        <p className={formula.status === 'locally_reviewed' ? 'kw-hint' : 'kw-warning'}>原文公式 ({number}) · {formula.status === 'locally_reviewed' ? '已本地核对' : formula.model === 'local_pdf_text_v1' ? '本地提取，需核对原页' : '机器转录，需核对原页'}</p>
        <MarkdownContent>{`$$\n${formula.latex}\n$$`}</MarkdownContent>
        <Button size="small" type="link" onClick={() => onSource({ conversationId: conversation.id, messageId: message.id, citation, pageIndex: formula.page_index })}>[{citation.source_id}] · 第 {formula.page_index + 1} 页 · 核对原文</Button>
      </section>)}</article>)}
      {pendingQuestion && pendingQuestion.baseId === baseId && pendingQuestion.chatId === chatId && !conversation?.messages.some(message => message.role === 'user' && message.request_id === pendingQuestion.requestId) && <article className="kw-message kw-message-user" aria-label="你的问题"><p className="kw-literal">{pendingQuestion.text}</p><small className="kw-hint">等待回答</small></article>}
    </div>
    {(busy || conversation?.active_run_id || retrieval) && <p role="status" className={busy || conversation?.active_run_id ? 'kw-progress' : 'kw-hint kw-retrieval-summary'}>{retrieval ? `${busy || conversation?.active_run_id ? stage === 'generating' ? '正在生成回答，已检索' : '正在检索' : '本轮检索'}${retrievalLabel(retrieval)}` : `${stageLabels[stage] ?? '正在处理文献问题'}${baseName ? `「${baseName}」` : ''}…`}</p>}
    {mode && <p className="kw-hint">{mode === 'hybrid' ? '本轮使用混合检索' : mode === 'dense' ? '本轮使用语义检索' : mode === 'lexical_degraded' ? '语义索引暂不可用，本轮使用关键词检索' : '本轮使用关键词检索（词法模式）'}</p>}
    <form className="kw-composer" onSubmit={event => { event.preventDefault(); void send() }}>
      <Input.TextArea aria-label="文献问题" placeholder="就选中的文献提问…" value={draft} maxLength={2000} autoSize={{ minRows: 3, maxRows: 7 }} onChange={event => edit(event.target.value)} />
      <div><span className="kw-hint">{draft.length} / 2000</span>{busy || conversation?.active_run_id ? <Button danger onClick={() => void cancel()}>停止生成</Button> : <Button aria-label="发送" type="primary" htmlType="submit" disabled={loading || !validScope || !profile?.configured || !draft.trim()}>发送</Button>}</div>
    </form>
    <p className="kw-privacy">{profile?.location === 'cloud' ? '问题和所选证据会发送至云端模型；原件与索引保存在本机。' : '原件与索引保存在本机；回答使用所选的服务器模型配置。'}</p>
  </section>
}
