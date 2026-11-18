import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Checkbox, Input, Select } from 'antd'
import { knowledgeApi } from '../api/knowledge'
import type { KnowledgeBase, KnowledgeDocument } from '../api/knowledge'
import { knowledgeChatApi } from '../api/knowledgeChat'
import type { KnowledgeBinding, KnowledgeConversation, KnowledgeModelProfile, KnowledgeSourceSelection } from '../api/knowledgeChat'
import { BaseWorkspace } from '../components/KnowledgeLibrary'
import { KnowledgeChat } from '../components/KnowledgeChat'
import { KnowledgeSourcePanel } from '../components/KnowledgeSourcePanel'
import './KnowledgeWorkspace.css'

export interface KnowledgeWorkspaceProps { baseId?: string; chatId?: string; onLocationChange: (baseId?: string, chatId?: string) => void }
export function KnowledgeWorkspace({ baseId, chatId, onLocationChange }: KnowledgeWorkspaceProps) {
  const [bases, setBases] = useState<KnowledgeBase[]>([])
  const [selectedBase, setSelectedBase] = useState(baseId ?? '')
  const [profiles, setProfiles] = useState<KnowledgeModelProfile[]>([])
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [scopeMode, setScopeMode] = useState<KnowledgeBinding['scope_mode']>('selected')
  const [modelId, setModelId] = useState('default')
  const [view, setView] = useState('chat')
  const [source, setSource] = useState<KnowledgeSourceSelection | null>(null)
  const [conversations, setConversations] = useState<KnowledgeConversation[]>([])
  const [query, setQuery] = useState('')
  const [baseName, setBaseName] = useState('')
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const routeBase = useRef(baseId); routeBase.current = baseId
  useEffect(() => {
    const controller = new AbortController()
    Promise.allSettled([knowledgeApi.listBases(controller.signal), knowledgeChatApi.models(controller.signal)]).then(results => {
      if (controller.signal.aborted) return
      const [baseResult, modelResult] = results
      if (baseResult.status === 'fulfilled') {
        setBases(baseResult.value.items)
        if (!routeBase.current && baseResult.value.items.length) { setSelectedBase(baseResult.value.items[0].id); onLocationChange(baseResult.value.items[0].id, undefined) }
      } else setError(baseResult.reason instanceof Error ? baseResult.reason.message : '知识库无法读取。')
      if (modelResult.status === 'fulfilled') setProfiles(modelResult.value.items)
      else setError(modelResult.reason instanceof Error ? modelResult.reason.message : '模型配置无法读取。')
      setLoading(false)
    })
    return () => controller.abort()
    // Initial server discovery; route changes are handled separately below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => { if (baseId) setSelectedBase(baseId) }, [baseId])
  useEffect(() => {
    setDocuments([]); setSelectedIds([]); setScopeMode('selected'); setSource(null); setQuery(''); setConversations([])
    if (!selectedBase) return
    const controller = new AbortController()
    knowledgeChatApi.list(selectedBase, controller.signal).then(value => { if (!controller.signal.aborted) setConversations(value.items) }).catch(error => { if (!controller.signal.aborted) setError(error instanceof Error ? error.message : '对话列表无法读取。') })
    return () => controller.abort()
  }, [selectedBase])
  useEffect(() => { setSource(null) }, [chatId])
  const updateDocuments = useCallback((items: KnowledgeDocument[]) => {
    setDocuments(items)
    setSelectedIds(current => current.filter(id => items.some(document => document.id === id && document.enabled && document.active_version_id)))
  }, [])
  const restoreBinding = useCallback((binding: KnowledgeBinding) => { setSelectedIds(binding.document_ids); setScopeMode(binding.scope_mode); setModelId(binding.model_profile_id) }, [])
  function changeBase(id: string) { setSelectedBase(id); onLocationChange(id, undefined) }
  async function createBase() {
    if (!baseName.trim() || creating || loading) return
    setCreating(true); setError(null)
    try { const value = await knowledgeApi.createBase(baseName.trim()); setBases(current => [...current, value]); setBaseName(''); changeBase(value.id) }
    catch (error) { setError(error instanceof Error ? error.message : '知识库创建失败。') }
    finally { setCreating(false) }
  }
  const visible = documents.filter(document => `${document.title ?? ''} ${document.filename}`.toLowerCase().includes(query.toLowerCase()))
  const binding = { base_id: selectedBase, scope_mode: scopeMode, document_ids: scopeMode === 'selected' ? selectedIds : [], model_profile_id: modelId }
  return <main className="kw-page" aria-label="文献知识库">
    <aside className="kw-library" aria-label="文献范围"><details open={!window.matchMedia('(max-width: 760px)').matches}><summary>文献库</summary><div className="kw-library-body">
      <Select aria-label="当前知识库" value={selectedBase || undefined} onChange={changeBase} virtual={false} loading={loading} placeholder="选择知识库" options={bases.map(base => ({ value: base.id, label: base.name }))} />
      <form className="kw-create-base" onSubmit={event => { event.preventDefault(); void createBase() }}><Input aria-label="新知识库名称" value={baseName} placeholder="新知识库名称" maxLength={200} onChange={event => setBaseName(event.target.value)} /><Button htmlType="submit" loading={creating} disabled={loading || !baseName.trim()}>新建</Button></form>
      <Button className="kw-upload-link" onClick={() => setView('parsing')} disabled={!selectedBase}>上传与管理文献</Button>
      <Input aria-label="筛选文献" value={query} placeholder="搜索文献标题" onChange={event => setQuery(event.target.value)} />
      <label className="kw-scope-label">检索范围<Select aria-label="检索范围" value={scopeMode} virtual={false} onChange={setScopeMode} options={[{ value: 'selected', label: '仅所选文献' }, { value: 'all_enabled', label: '整个库中已启用文献' }]} /></label>
      <p className="kw-hint">已选 {selectedIds.length} 篇</p>
      <div className="kw-doc-list">{visible.map(document => {
        const ready = document.enabled && Boolean(document.active_version_id)
        return <article className={`kw-doc ${selectedIds.includes(document.id) ? 'is-selected' : ''}`} key={document.id}><Checkbox aria-label={`选择 ${document.title || document.filename}`} disabled={!ready || scopeMode === 'all_enabled'} checked={selectedIds.includes(document.id)} onChange={event => setSelectedIds(current => event.target.checked ? [...current, document.id] : current.filter(id => id !== document.id))}>{document.title || document.filename}</Checkbox><p>{!document.enabled ? '已停用' : document.active_version_id ? '可检索' : ({ queued: '等待入库', parsing: '解析中', indexing: '建立索引中', failed: '入库失败' }[document.latest_job?.status ?? document.status] ?? '等待解析')}</p>{document.active_version_id && typeof document.page_count === 'number' && typeof document.chunk_count === 'number' && <p>{document.page_count} 页 · {document.chunk_count} 个切片</p>}{document.latest_job?.warnings?.length ? <p className="kw-warning">解析有质量提示，请核对原文</p> : null}</article>
      })}</div>
      <h3>文献对话</h3><Button onClick={() => onLocationChange(selectedBase, undefined)} disabled={!chatId}>新对话</Button>
      <nav className="kw-history" aria-label="历史文献对话">{conversations.map(chat => <button key={chat.id} aria-current={chat.id === chatId ? 'page' : undefined} onClick={() => onLocationChange(selectedBase, chat.id)}>{chat.title || '文献对话'}</button>)}</nav>
      <p className="kw-local-note">原件与索引保存在本机。仅发送本轮问题与获准的文献证据。</p>
    </div></details></aside>
    <section className="kw-center" aria-label="文献工作区"><div className="kw-tabs" role="tablist" aria-label="文献工作区视图"><button role="tab" id="kw-chat-tab" aria-controls="kw-chat-panel" aria-selected={view === 'chat'} onClick={() => setView('chat')}>文献对话</button><button role="tab" id="kw-parsing-tab" aria-controls="kw-parsing-panel" aria-selected={view === 'parsing'} onClick={() => setView('parsing')}>文档与解析</button></div>
      {error && <Alert type="error" title={error} role="alert" closable onClose={() => setError(null)} />}
      {selectedBase ? <><div id="kw-chat-panel" role="tabpanel" aria-labelledby="kw-chat-tab" hidden={view !== 'chat'}><KnowledgeChat baseId={selectedBase} baseName={bases.find(base => base.id === selectedBase)?.name} chatId={chatId} binding={binding} profiles={profiles} onModelChange={setModelId} onRestoreBinding={restoreBinding} onCreated={chat => { onLocationChange(selectedBase, chat.id); setConversations(current => [chat, ...current.filter(item => item.id !== chat.id)]) }} onSource={setSource} /></div><div id="kw-parsing-panel" role="tabpanel" aria-labelledby="kw-parsing-tab" hidden={view !== 'parsing'}><div className="kw-parsing-intro"><h2>上传与解析</h2><p>保留原文与来源位置；文献可检索后再开始对话。当前文档检索使用词法模式，表格、公式和图形请核对原文。</p></div><BaseWorkspace key={selectedBase} baseId={selectedBase} onDocumentsChange={updateDocuments} /></div></> : !loading && <p className="kw-empty">新建知识库后即可上传文献。</p>}
    </section>
    <KnowledgeSourcePanel selection={source} documents={documents} />
  </main>
}
