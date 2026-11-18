import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Drawer, Input, Select, Spin, Tag } from 'antd'
import { knowledgeApi } from '../api/knowledge'
import type { KnowledgeAccepted, KnowledgeBase, KnowledgeChunk, KnowledgeDocument, KnowledgeParser } from '../api/knowledge'
import './KnowledgeLibrary.css'

const running = new Set(['queued', 'parsing', 'indexing'])
const statusLabels: Record<string, string> = {
  queued: '等待入库', parsing: '解析中', indexing: '建立索引中',
  completed: '可检索', ready: '可检索', completed_with_warnings: '可检索 · 请核对原文', failed: '入库失败',
}
const qualityLabels: Record<string, string> = {
  reading_order_heuristic: '阅读顺序由自动提取确定，请核对原文',
  section_detection_heuristic: '章节由自动识别确定，请核对原文',
  formula_unverified: '公式尚未核验', formulas_unverified: '公式尚未核验',
  table_unverified: '表格尚未核验', tables_unverified: '表格尚未核验',
  table_structure_unverified: '表格结构和数值尚未核验',
  figure_unverified: '图形数值尚未核验', figures_unverified: '图形数值尚未核验',
  image_content_not_extracted: '图片内容尚未提取，请查看原图',
  ocr_required: '扫描件需要 OCR',
}
function errorText(error: unknown) { return error instanceof Error ? error.message : '请求失败，请检查本地服务。' }
function titleOf(document: KnowledgeDocument) { return document.title || document.filename }
function chunkId(chunk: KnowledgeChunk) { return chunk.chunk_id ?? chunk.id ?? '' }
function QualityNotes({ flags }: { flags?: string[] }) {
  return flags?.length ? <p className="knowledge-quality">{[...new Set(flags.map(flag => qualityLabels[flag] ?? flag))].join('；')}</p> : null
}

export function KnowledgeLibrary({ open, onClose }: { open: boolean; onClose: () => void }) {
  return <Drawer title="本地知识库" open={open} onClose={onClose} size="large" className="knowledge-library" styles={{ wrapper: { width: 'min(1040px, 100vw)' } }}>
    {open && <LibraryContent />}
  </Drawer>
}

function LibraryContent() {
  const [bases, setBases] = useState<KnowledgeBase[]>([])
  const [baseId, setBaseId] = useState('')
  const [name, setName] = useState('')
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const lifetime = useRef<AbortController | null>(null)
  useEffect(() => {
    const controller = new AbortController(); lifetime.current = controller
    knowledgeApi.listBases(controller.signal).then(value => {
      if (controller.signal.aborted) return
      setBases(value.items); setBaseId(value.items[0]?.id ?? ''); setLoading(false)
    }).catch(error => { if (!controller.signal.aborted) { setError(errorText(error)); setLoading(false) } })
    return () => controller.abort()
  }, [])
  async function create() {
    if (loading || !name.trim() || creating) return
    const signal = lifetime.current!.signal
    setCreating(true); setError(null)
    try {
      const base = await knowledgeApi.createBase(name.trim(), signal)
      if (!signal.aborted) { setBases(current => [...current, base]); setBaseId(base.id); setName('') }
    } catch (error) { if (!signal.aborted) setError(errorText(error)) }
    finally { if (!signal.aborted) setCreating(false) }
  }
  return <div className="knowledge-content">
    <p className="knowledge-intro">文献保存在本地。当前使用词法检索；公式、表格和图形数值请回到原页核对。</p>
    {error && <Alert type="error" role="alert" title={error} />}
    <div className="knowledge-base-toolbar">
      <label>知识库<Select aria-label="知识库" value={baseId || undefined} onChange={setBaseId} loading={loading} virtual={false} options={bases.map(base => ({ value: base.id, label: base.name }))} placeholder="选择知识库" /></label>
      <form onSubmit={event => { event.preventDefault(); void create() }} className="knowledge-create">
        <Input aria-label="新知识库名称" placeholder="新知识库名称" value={name} maxLength={200} onChange={event => setName(event.target.value)} />
        <Button htmlType="submit" loading={creating} disabled={loading || !name.trim()}>新建知识库</Button>
      </form>
    </div>
    {baseId ? <BaseWorkspace key={baseId} baseId={baseId} /> : !loading && <p className="knowledge-empty">新建知识库后即可上传文献。</p>}
  </div>
}

export function BaseWorkspace({ baseId, onDocumentsChange }: { baseId: string; onDocumentsChange?: (documents: KnowledgeDocument[]) => void }) {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [busyDocuments, setBusyDocuments] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [pollError, setPollError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [results, setResults] = useState<KnowledgeChunk[] | null>(null)
  const [chunkPage, setChunkPage] = useState<{ documentId: string; versionId: string; offset: number; total: number; items: KnowledgeChunk[] } | null>(null)
  const [chunkDetail, setChunkDetail] = useState<KnowledgeChunk | null>(null)
  const [chunksLoading, setChunksLoading] = useState(false)
  const lifetime = useRef<AbortController | null>(null)
  const searchRequest = useRef<AbortController | null>(null)
  const searchGeneration = useRef(0)
  const chunkGeneration = useRef(0)

  useEffect(() => { if (!loading) onDocumentsChange?.(documents) }, [documents, loading, onDocumentsChange])

  useEffect(() => {
    const controller = new AbortController(); lifetime.current = controller
    knowledgeApi.listDocuments(baseId, controller.signal).then(value => {
      if (!controller.signal.aborted) { setDocuments(value.items); setLoading(false) }
    }).catch(error => { if (!controller.signal.aborted) { setError(errorText(error)); setLoading(false) } })
    return () => { controller.abort(); searchRequest.current?.abort() }
  }, [baseId])

  // One serial polling loop per set of active jobs. Its controller and timer belong to this library view.
  const activeJobsKey = JSON.stringify(documents.filter(document => document.latest_job && running.has(document.latest_job.status)).map(document => [document.id, document.latest_job!.id]).sort())
  useEffect(() => {
    const jobs = JSON.parse(activeJobsKey) as [string, string][]
    if (!jobs.length) return
    const controller = new AbortController()
    let timer: number | undefined
    async function poll() {
      const outcomes = await Promise.allSettled(jobs.map(async ([documentId, jobId]) => {
        const job = await knowledgeApi.getJob(jobId, controller.signal)
        // The job is authoritative for ingestion status, even if a detail refresh fails.
        const fresh = !running.has(job.status) ? await knowledgeApi.getDocument(documentId, controller.signal).catch(() => null) : null
        const published = job.status === 'completed' || job.status === 'completed_with_warnings'
        if (controller.signal.aborted) return
        setDocuments(current => current.map(document => document.id === documentId && document.latest_job?.id === jobId
          ? { ...document,
            active_version_id: fresh?.active_version_id ?? (job.status === 'completed' || job.status === 'completed_with_warnings' ? job.version_id : null) ?? document.active_version_id,
            title: fresh?.title ?? document.title,
            page_count: fresh ? fresh.page_count : !published || job.version_id === document.active_version_id ? document.page_count : undefined,
            chunk_count: fresh ? fresh.chunk_count : !published || job.version_id === document.active_version_id ? document.chunk_count : undefined,
            status: job.status, latest_job: { ...job, warnings: job.warnings ?? fresh?.latest_job?.warnings },
          } : document))
      }))
      if (controller.signal.aborted) return
      setPollError(outcomes.some(item => item.status === 'rejected') ? '入库状态暂时无法读取，正在重试。' : null)
      timer = window.setTimeout(() => void poll(), 1500)
    }
    void poll()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [activeJobsKey])

  function accept(value: KnowledgeAccepted) {
    const document = { ...value.document, latest_job: value.job, status: value.job.status }
    setDocuments(current => [document, ...current.filter(item => item.id !== document.id)])
    setNotice(value.duplicate ? '此文献已在库中，已显示现有入库状态。' : '文件已接收，可在下方查看入库进度。')
  }
  async function upload(file: File) {
    const signal = lifetime.current!.signal
    setUploading(true); setError(null); setNotice(null)
    try { const value = await knowledgeApi.upload(baseId, file, signal); if (!signal.aborted) accept(value) }
    catch (error) { if (!signal.aborted) setError(errorText(error)) }
    finally { if (!signal.aborted) setUploading(false) }
  }
  async function toggle(document: KnowledgeDocument) {
    const signal = lifetime.current!.signal
    setError(null); setBusyDocuments(current => [...current, document.id])
    try {
      const updated = await knowledgeApi.setEnabled(document.id, !document.enabled, signal)
      if (signal.aborted) return
      setDocuments(current => current.map(item => item.id === document.id ? { ...item, enabled: updated.enabled } : item))
      if (!updated.enabled) {
        searchGeneration.current++; searchRequest.current?.abort(); setSearching(false)
        chunkGeneration.current++; setChunksLoading(false)
        setResults(current => current?.filter(item => item.document_id !== document.id) ?? null)
        setChunkPage(current => current?.documentId === document.id ? null : current)
        setChunkDetail(current => current?.document_id === document.id ? null : current)
      }
    } catch (error) { if (!signal.aborted) setError(errorText(error)) }
    finally { if (!signal.aborted) setBusyDocuments(current => current.filter(id => id !== document.id)) }
  }
  async function reparse(document: KnowledgeDocument, parser: KnowledgeParser) {
    const signal = lifetime.current!.signal
    setError(null); setBusyDocuments(current => [...current, document.id])
    try { const value = await knowledgeApi.reparse(document.id, parser, crypto.randomUUID(), signal); if (!signal.aborted) accept(value) }
    catch (error) { if (!signal.aborted) setError(errorText(error)) }
    finally { if (!signal.aborted) setBusyDocuments(current => current.filter(id => id !== document.id)) }
  }
  async function search() {
    if (!query.trim()) return
    const generation = ++searchGeneration.current
    searchRequest.current?.abort()
    const controller = new AbortController(); searchRequest.current = controller
    setSearching(true); setResults(null); setError(null)
    try {
      const value = await knowledgeApi.search(baseId, query.trim(), controller.signal)
      if (!controller.signal.aborted && generation === searchGeneration.current) setResults(value.items)
    } catch (error) { if (!controller.signal.aborted && generation === searchGeneration.current) setError(errorText(error)) }
    finally { if (!controller.signal.aborted && generation === searchGeneration.current) setSearching(false) }
  }
  async function browse(document: KnowledgeDocument, offset = 0) {
    if (!document.active_version_id) return
    const generation = ++chunkGeneration.current, signal = lifetime.current!.signal
    setChunksLoading(true); setChunkPage(null); setChunkDetail(null); setError(null)
    try {
      const value = await knowledgeApi.listChunks(document.id, document.active_version_id, offset, signal)
      if (!signal.aborted && generation === chunkGeneration.current) setChunkPage({ documentId: document.id, versionId: document.active_version_id, offset, ...value })
    } catch (error) { if (!signal.aborted && generation === chunkGeneration.current) setError(errorText(error)) }
    finally { if (!signal.aborted && generation === chunkGeneration.current) setChunksLoading(false) }
  }
  async function inspect(chunk: KnowledgeChunk) {
    const generation = ++chunkGeneration.current, signal = lifetime.current!.signal
    setChunksLoading(true); setChunkDetail(null); setError(null)
    try {
      const value = await knowledgeApi.getChunk(chunkId(chunk), signal)
      if (!signal.aborted && generation === chunkGeneration.current) setChunkDetail(value)
    } catch (error) { if (!signal.aborted && generation === chunkGeneration.current) setError(errorText(error)) }
    finally { if (!signal.aborted && generation === chunkGeneration.current) setChunksLoading(false) }
  }
  const enabledIds = new Set(documents.filter(document => document.enabled).map(document => document.id))
  const currentResults = results?.filter(chunk => enabledIds.has(chunk.document_id))
  const pageDocument = documents.find(document => document.id === chunkPage?.documentId)
  return <div className="knowledge-workspace">
    {error && <Alert type="error" title={error} role="alert" closable onClose={() => setError(null)} />}
    {pollError && <Alert type="warning" title={pollError} role="alert" />}
    {notice && <p role="status" className="knowledge-notice">{notice}</p>}
    <section aria-label="文献管理">
      <div className="knowledge-section-header"><h2>文献</h2><label className={`knowledge-upload${uploading ? ' is-busy' : ''}`}>
        {uploading ? '正在上传…' : '上传文献'}<input aria-label="上传文献" type="file" accept=".pdf,.md,.txt,application/pdf,text/plain,text/markdown" disabled={uploading || loading} onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void upload(file) }} />
      </label></div>
      <p className="knowledge-hint">支持 PDF、Markdown 和 TXT，单个文件最多 50 MiB。上传后将自动入库。</p>
      {loading ? <div role="status"><Spin size="small" /> 正在读取文献…</div> : documents.length === 0 ? <p className="knowledge-empty">此知识库还没有文献</p> : <div className="knowledge-documents">
        {documents.map(document => <DocumentRow key={document.id} document={document} busy={busyDocuments.includes(document.id)} onToggle={() => void toggle(document)} onReparse={parser => void reparse(document, parser)} onBrowse={() => void browse(document)} />)}
      </div>}
    </section>
    <section aria-label="文献检索">
      <h2>检索证据</h2>
      <form className="knowledge-search" onSubmit={event => { event.preventDefault(); void search() }}>
        <Input aria-label="检索文献" placeholder="输入关键词检索当前知识库" value={query} onChange={event => setQuery(event.target.value)} maxLength={2000} />
        <Button type="primary" htmlType="submit" aria-label="检索" disabled={!query.trim() || loading}>检索</Button>
      </form>
      {searching && <div role="status"><Spin size="small" /> 正在检索…</div>}
      {currentResults?.length === 0 && <p className="knowledge-empty">未找到匹配的已启用文献，请调整关键词。</p>}
      {currentResults?.map((chunk, index) => <article aria-label={`检索证据 ${index + 1}`} key={chunkId(chunk)} className="knowledge-evidence">
        <h3>{chunk.title || documents.find(document => document.id === chunk.document_id)?.filename}</h3>
        <ChunkText chunk={chunk} />
        <Button size="small" onClick={() => void inspect(chunk)}>查看完整切片</Button>
      </article>)}
    </section>
    {chunksLoading && <div role="status"><Spin size="small" /> 正在读取切片…</div>}
    {chunkPage && pageDocument?.enabled && <section aria-label="文献切片" className="knowledge-chunk-panel">
      <div className="knowledge-section-header"><h2>{titleOf(pageDocument)} · 文献切片</h2><Button size="small" onClick={() => setChunkPage(null)}>收起切片</Button></div>
      {chunkPage.items.map(chunk => <article className="knowledge-evidence" key={chunkId(chunk)}><ChunkText chunk={chunk} /></article>)}
      {!chunkPage.items.length && <p className="knowledge-empty">此版本暂无切片。</p>}
      <div className="knowledge-pagination"><span>共 {chunkPage.total} 个切片</span><Button size="small" disabled={chunkPage.offset === 0} onClick={() => void browse({ ...pageDocument, active_version_id: chunkPage.versionId }, Math.max(0, chunkPage.offset - 20))}>上一页</Button><Button size="small" disabled={chunkPage.offset + 20 >= chunkPage.total} onClick={() => void browse({ ...pageDocument, active_version_id: chunkPage.versionId }, chunkPage.offset + 20)}>下一页</Button></div>
    </section>}
    {chunkDetail && enabledIds.has(chunkDetail.document_id) && <section aria-label="切片详情" className="knowledge-chunk-panel"><div className="knowledge-section-header"><h2>切片详情</h2><Button size="small" onClick={() => setChunkDetail(null)}>收起详情</Button></div><ChunkText chunk={chunkDetail} /></section>}
  </div>
}

function DocumentRow({ document, busy, onToggle, onReparse, onBrowse }: { document: KnowledgeDocument; busy: boolean; onToggle: () => void; onReparse: (parser: KnowledgeParser) => void; onBrowse: () => void }) {
  const [parser, setParser] = useState<KnowledgeParser>('pdfplumber')
  const title = titleOf(document), job = document.latest_job
  const status = job?.status ?? document.status
  const active = running.has(status)
  const color = status === 'failed' ? 'error' : status === 'completed_with_warnings' ? 'warning' : status === 'completed' || status === 'ready' ? 'success' : 'processing'
  return <article className={`knowledge-document${document.enabled ? '' : ' is-disabled'}`}>
    <div className="knowledge-document-heading"><h3>{title}</h3><Tag color={color}>{statusLabels[status] ?? '状态待确认'}</Tag>{!document.enabled && <Tag>已停用</Tag>}</div>
    {document.active_version_id && typeof document.page_count === 'number' && typeof document.chunk_count === 'number' && <p className="knowledge-hint">{document.page_count} 页 · {document.chunk_count} 个切片</p>}
    {document.filename !== title && <p className="knowledge-filename">{document.filename}</p>}
    {job?.error_message && <p className="knowledge-document-error">{job.error_message}</p>}
    <QualityNotes flags={job?.warnings} />
    <div className="knowledge-document-actions">
      <Button size="small" aria-label={`${document.enabled ? '停用' : '启用'} ${title}`} loading={busy} onClick={onToggle}>{document.enabled ? '停用' : '启用'}</Button>
      <Button size="small" aria-label={`查看切片 ${title}`} disabled={!document.enabled || !document.active_version_id} onClick={onBrowse}>查看切片</Button>
      {document.enabled && <Button type="link" size="small" href={knowledgeApi.originalUrl(document.id)} target="_blank" rel="noreferrer">原文</Button>}
      <Select aria-label={`重解析方式 ${title}`} size="small" value={parser} virtual={false} onChange={setParser} options={[{ value: 'pdfplumber', label: '文字提取' }, { value: 'docling', label: 'Docling（需本地模型）' }]} />
      <Button size="small" aria-label={`重解析 ${title}`} disabled={active || busy || !document.enabled} onClick={() => onReparse(parser)}>重解析</Button>
    </div>
    {status === 'failed' && document.active_version_id && <p className="knowledge-hint">本次入库失败，仍可查看此前的可用版本。</p>}
  </article>
}

function ChunkText({ chunk }: { chunk: KnowledgeChunk }) {
  return <>
    {chunk.section && <p className="knowledge-section-label">{chunk.section}</p>}
    <p className="knowledge-chunk-text">{chunk.text}</p>
    <QualityNotes flags={chunk.quality_flags} />
    <div className="knowledge-page-links">{[...new Map((chunk.locations ?? []).map(location => [location.page_index, location])).values()].map(location => <a key={location.page_index} href={knowledgeApi.originalUrl(chunk.document_id, location.page_index)} target="_blank" rel="noreferrer">原文第 {location.page_label || location.page_index + 1} 页</a>)}</div>
  </>
}
