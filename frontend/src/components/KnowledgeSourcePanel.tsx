import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Dropdown, Select } from 'antd'
import { knowledgeApi } from '../api/knowledge'
import { ApiError } from '../api/client'
import type { KnowledgeDocument } from '../api/knowledge'
import { citationBoxStyle, knowledgeChatApi, uniqueCitationPages } from '../api/knowledgeChat'
import type { KnowledgeCitation, KnowledgePage, KnowledgeSourceSelection } from '../api/knowledgeChat'
import { translateKnowledgeCitation } from '../api/knowledgeTranslation'
import type { KnowledgeTranslation, TranslationLanguage } from '../api/knowledgeTranslation'
import { MarkdownContent } from './MarkdownContent'
import { CitationExcerpt } from './CitationExcerpt'
import './KnowledgeSourcePanel.css'

const qualityLabels: Record<string, string> = {
  formula_unverified: '公式尚未核验', formulas_unverified: '公式尚未核验',
  table_unverified: '表格尚未核验', tables_unverified: '表格尚未核验',
  table_structure_unverified: '表头、单位和数值尚未核验',
  figure_unverified: '图形数值尚未核验', figures_unverified: '图形数值尚未核验',
  image_content_not_extracted: '图片内容尚未提取，请查看原图',
  reading_order_heuristic: '请核对阅读顺序', section_detection_heuristic: '请核对自动识别的章节',
  ocr_required: '扫描件需要 OCR',
}

type SourceView = 'original' | 'translation' | 'comparison'

function SourceTools({ citation, selection, available }: { citation: KnowledgeCitation; selection: KnowledgeSourceSelection; available: boolean }) {
  const [target, setTarget] = useState<TranslationLanguage>('zh-CN')
  const [translation, setTranslation] = useState<KnowledgeTranslation | null>(null)
  const [view, setView] = useState<SourceView>('original')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copyStatus, setCopyStatus] = useState<string | null>(null)
  const [manualText, setManualText] = useState<string | null>(null)
  const [unavailable, setUnavailable] = useState<string | null>(null)
  const activeRequest = useRef<AbortController | null>(null)
  const activeCopy = useRef(0)
  const canUse = available && !unavailable && !!citation.evidence_text.trim()
  useEffect(() => {
    // Cleanup must abort the latest request, rather than the one present at mount.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
    return () => { activeRequest.current?.abort(); activeCopy.current++ }
  }, [])
  useEffect(() => {
    activeRequest.current?.abort(); activeRequest.current = null
    activeCopy.current++
    // Availability and language changes invalidate all derived state immediately.
    // oxlint-disable-next-line react/set-state-in-effect
    setTranslation(null); setPending(false); setError(null); setView('original'); setCopyStatus(null); setManualText(null)
  }, [target, canUse])
  function cancelTranslation() {
    activeRequest.current?.abort(); activeRequest.current = null; setPending(false)
  }
  function showView(next: SourceView) {
    setView(next)
    if (next !== 'original' && !translation && canUse && !activeRequest.current) void translate(next)
  }
  async function translate(nextView: Exclude<SourceView, 'original'> = 'comparison') {
    if (!canUse || activeRequest.current) return
    setView(nextView)
    const controller = new AbortController()
    activeRequest.current = controller
    setPending(true); setError(null)
    try {
      const value = await translateKnowledgeCitation(selection.conversationId, selection.messageId, citation.source_id, target, citation.evidence_text, controller.signal)
      if (controller.signal.aborted || activeRequest.current !== controller) return
      setTranslation(value)
    } catch (error) {
      if (!controller.signal.aborted && activeRequest.current === controller) {
        if (error instanceof ApiError && error.code === 'knowledge_source_unavailable') setUnavailable(error.message)
        else setError(error instanceof Error ? error.message : '翻译失败，请重试。')
      }
    } finally {
      if (!controller.signal.aborted && activeRequest.current === controller) { setPending(false); activeRequest.current = null }
    }
  }
  async function copy(kind: string) {
    if (!canUse || (kind === 'translation' && !translation)) return
    const pages = uniqueCitationPages(citation.locations).map(location => `第 ${location.page_label || location.page_index + 1} 页（物理页 ${location.page_index + 1}）`).join('、')
    const text = kind === 'translation' ? translation!.translated_text : kind === 'reference'
      ? `${citation.evidence_text}\n\n出处：[${citation.source_id}] ${citation.title || citation.filename}（${citation.filename}），${pages || '页码未提供'}，版本 ${citation.version_id}`
      : citation.evidence_text
    const identity = ++activeCopy.current
    setCopyStatus(null); setManualText(null)
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable')
      await navigator.clipboard.writeText(text)
      if (identity === activeCopy.current) setCopyStatus(kind === 'translation' ? '已复制译文' : kind === 'reference' ? '已复制原文及出处' : '已复制原文')
    } catch {
      if (identity === activeCopy.current) setManualText(text)
    }
  }
  return <section className="kw-source-tools" aria-label="引用阅读工具">
    <h3>引用片段</h3>
    <div className="kw-source-tools-actions">
      <label>翻译为<Select aria-label="翻译目标语言" virtual={false} value={target} disabled={!canUse} onChange={setTarget} options={[{ value: 'zh-CN', label: '中文' }, { value: 'en', label: '英文' }]} /></label>
      <Button disabled={!canUse || pending} onClick={() => void translate()}>翻译片段</Button>
      {pending && <Button onClick={cancelTranslation}>取消翻译</Button>}
      <Dropdown trigger={['click']} menu={{ items: [{ key: 'original', label: '复制原文' }, { key: 'translation', label: '复制译文', disabled: !translation }, { key: 'reference', label: '复制并附文献/页码' }], onClick: ({ key }) => void copy(key) }}>
        <Button aria-label="复制引用" disabled={!canUse}>复制 ▾</Button>
      </Dropdown>
    </div>
    <div className="kw-source-tools-tabs" role="tablist" aria-label="引用阅读方式">
      {([{ key: 'original', label: '原文' }, { key: 'translation', label: '译文' }, { key: 'comparison', label: '上下对照' }] as const).map(item => <button key={item.key} type="button" role="tab" aria-selected={view === item.key} disabled={item.key !== 'original' && !canUse} onClick={() => showView(item.key)}>{item.label}</button>)}
    </div>
    {view !== 'translation' && <CitationExcerpt>{citation.evidence_text}</CitationExcerpt>}
    {view !== 'original' && translation && canUse && <div className="kw-source-tools-translation" aria-label="引用译文"><p>机器翻译，仅供阅读参考；核验请以原文为准。</p><MarkdownContent>{translation.translated_text}</MarkdownContent></div>}
    {pending && <p role="status">正在翻译所选片段…</p>}
    {view !== 'original' && !translation && !pending && !error && canUse && <p className="kw-hint">尚无译文，点击“译文”或“翻译片段”重试。</p>}
    {error && <Alert type="error" title={error} role="alert" />}
    {unavailable && <Alert type="warning" title={unavailable} role="alert" />}
    {copyStatus && <p className="kw-source-tools-status" role="status">{copyStatus}</p>}
    {manualText !== null && <><Alert type="warning" title="复制失败，请选中下方内容手动复制。" role="alert" /><textarea className="kw-source-tools-manual" aria-label="手动复制内容" readOnly value={manualText} onFocus={event => event.currentTarget.select()} /></>}
  </section>
}

export function KnowledgeSourcePanel({ selection, documents }: { selection: KnowledgeSourceSelection | null; documents: KnowledgeDocument[] }) {
  const [citation, setCitation] = useState<KnowledgeCitation | null>(null)
  const [page, setPage] = useState<KnowledgePage | null>(null)
  const [locationIndex, setLocationIndex] = useState(0)
  const [zoom, setZoom] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pdfError, setPdfError] = useState<string | null>(null)
  const [rendered, setRendered] = useState<{ width: number; height: number; boxes: ReturnType<typeof citationBoxStyle>[] } | null>(null)
  const canvas = useRef<HTMLCanvasElement | null>(null)
  const disclosure = useRef<HTMLDetailsElement | null>(null)
  const pages = citation ? uniqueCitationPages(citation.locations) : []
  const location = pages[locationIndex]
  const disabled = citation ? documents.some(document => document.id === citation.document_id && !document.enabled) : false
  const pageGeometryVerified = page?.coordinate_space === 'rotated_media_top_left' && page.to_pdf_transform?.length === 6 && page.to_pdf_transform.every(Number.isFinite)
  useEffect(() => {
    // Reset the old source before starting an independent asynchronous identity lookup.
    // oxlint-disable-next-line react/set-state-in-effect
    setCitation(null); setPage(null); setError(null); setRendered(null); setPdfError(null); setLocationIndex(0); setZoom(1)
    if (!selection) return
    if (disclosure.current) disclosure.current.open = true
    const controller = new AbortController(); setLoading(true)
    knowledgeChatApi.citation(selection.conversationId, selection.messageId, selection.citation.source_id, controller.signal).then(value => {
      if (controller.signal.aborted) return
      // Only refreshed citation locations authorize a target physical page.
      const target = Number.isSafeInteger(selection.pageIndex) && selection.pageIndex! >= 0
        ? uniqueCitationPages(value.locations).findIndex(item => item.page_index === selection.pageIndex) : -1
      setLocationIndex(Math.max(0, target)); setCitation(value); setLoading(false)
    }).catch(error => { if (!controller.signal.aborted) { setError(error instanceof Error ? error.message : '来源无法读取。'); setLoading(false) } })
    return () => controller.abort()
  }, [selection])
  useEffect(() => {
    // Page content belongs to the newly requested location, never the previous page.
    // oxlint-disable-next-line react/set-state-in-effect
    setPage(null); setRendered(null); setPdfError(null)
    if (!citation || citation.available === false || disabled || !location) return
    const controller = new AbortController()
    knowledgeChatApi.page(citation.document_id, citation.version_id, location.page_index, controller.signal).then(value => { if (!controller.signal.aborted) setPage(value) }).catch(error => { if (!controller.signal.aborted) setPdfError(error instanceof Error ? error.message : '原页无法读取。') })
    return () => controller.abort()
  }, [citation, location, disabled])
  useEffect(() => {
    if (!citation || citation.available === false || disabled || !location || !page || !citation.filename.toLowerCase().endsWith('.pdf')) return
    let disposed = false
    let destroy: (() => void) | undefined
    let cancel: (() => void) | undefined
    // Clear old highlight geometry while the asynchronously rendered viewport changes.
    // oxlint-disable-next-line react/set-state-in-effect
    setRendered(null); setPdfError(null)
    async function renderPage() {
      try {
        const [pdfjs, worker] = await Promise.all([import('pdfjs-dist'), import('pdfjs-dist/build/pdf.worker.min.mjs?url')])
        if (disposed) return
        pdfjs.GlobalWorkerOptions.workerSrc = worker.default
        const task = pdfjs.getDocument({ url: knowledgeApi.originalUrl(citation!.document_id) })
        destroy = () => { void task.destroy() }
        const pdf = await task.promise
        if (disposed) return
        const pdfPage = await pdf.getPage(location!.page_index + 1)
        if (disposed || !canvas.current) return
        const viewport = pdfPage.getViewport({ scale: zoom })
        const dpr = window.devicePixelRatio || 1
        const element = canvas.current, context = element.getContext('2d')
        if (!context) throw new Error('浏览器无法显示原页，请打开原文核对。')
        element.width = Math.ceil(viewport.width * dpr); element.height = Math.ceil(viewport.height * dpr)
        element.style.width = `${viewport.width}px`; element.style.height = `${viewport.height}px`
        const renderTask = pdfPage.render({ canvasContext: context, canvas: element, viewport, transform: dpr === 1 ? undefined : [dpr, 0, 0, dpr, 0, 0] })
        cancel = () => renderTask.cancel()
        await renderTask.promise
        if (disposed) return
        const boxes = pageGeometryVerified ? citation!.locations.filter(item => item.page_index === location!.page_index && item.bbox?.length === 4).map(item => citationBoxStyle(item.bbox!, page!.height, viewport, page!.to_pdf_transform)) : []
        setRendered({ width: viewport.width, height: viewport.height, boxes })
      } catch (error) { if (!disposed) setPdfError(error instanceof Error ? error.message : 'PDF 原页无法显示，请打开原文核对。') }
    }
    void renderPage()
    return () => { disposed = true; cancel?.(); destroy?.() }
  }, [citation, page, location, zoom, disabled, pageGeometryVerified])
  const available = citation && citation.available !== false && !disabled
  const reason = citation?.reason ?? (citation?.unavailable_reason === 'source_unavailable_or_scope_revoked' ? '来源不可用或文献范围已撤销。旧引用保留，不替换为新版本。' : citation?.unavailable_reason)
  return <aside className="kw-source" aria-label="来源核对"><details ref={disclosure} open={!window.matchMedia('(max-width: 760px)').matches}><summary>来源核对{citation && <span>[{citation.source_id}]</span>}</summary><div className="kw-source-body">
    {!selection && <div className="kw-source-empty"><h3>从回答回到原文</h3><p>点击回答下方的引用，核对证据片段、文献版本和原页位置。</p><p>表格、公式与图形数值需要结合原文检查。</p></div>}
    {loading && <p role="status">正在核验来源可用性…</p>}
    {error && <Alert type="error" title={error} role="alert" />}
    {citation && <><h2>{citation.title || citation.filename}</h2><p className="kw-hint">{citation.filename}</p><p className="kw-version">版本 {citation.version_id}</p>
      {selection && <SourceTools key={`${selection.conversationId}/${selection.messageId}/${citation.source_id}/${citation.version_id}/${citation.evidence_text}`} citation={citation} selection={selection} available={!!available} />}
      {!available ? <Alert type="warning" title={disabled ? '文献已停用' : reason || '来源不可用。旧引用保留，不替换为新版本。'} /> : <>
        {pages.length > 0 && <div className="kw-source-controls"><label>原文位置<Select aria-label="引用页码" virtual={false} value={locationIndex} onChange={setLocationIndex} options={pages.map((item, index) => ({ value: index, label: `第 ${item.page_label || item.page_index + 1} 页（物理页 ${item.page_index + 1}）` }))} /></label>{citation.filename.toLowerCase().endsWith('.pdf') && <label>缩放<Select aria-label="原页缩放" virtual={false} value={zoom} onChange={setZoom} options={[0.5, 0.75, 1, 1.5, 2].map(value => ({ value, label: `${value * 100}%` }))} /></label>}</div>}
        {pdfError && <Alert type="warning" title={pdfError} />}
        {page && citation.filename.toLowerCase().endsWith('.pdf') && !pageGeometryVerified && <Alert type="warning" title="此版本尚未核验页面坐标，暂不框选引用区域；重新解析后可补充定位。" />}
        {location && citation.filename.toLowerCase().endsWith('.pdf') && <div className="kw-pdf-scroll"><div className="kw-pdf-page" style={rendered ? { width: rendered.width, height: rendered.height } : undefined}><canvas ref={canvas} aria-label={`PDF 原文第 ${location.page_index + 1} 页`} />{rendered?.boxes.map((box, index) => <div key={index} className="kw-pdf-highlight" style={box} aria-label="引用原文区域" />)}</div></div>}
        {page && !citation.filename.toLowerCase().endsWith('.pdf') && <details><summary>页面文字</summary><p className="kw-literal">{page.raw_text}</p></details>}
        {!!citation.quality_flags?.length && <Alert type="warning" title="解析内容需要核对" description={citation.quality_flags.map(flag => qualityLabels[flag] ?? '此片段含其他解析质量提示，请核对原文').join('；')} />}
        <Button block href={knowledgeApi.originalUrl(citation.document_id, location?.page_index)} target="_blank" rel="noreferrer">打开完整原文</Button>
      </>}
    </>}
  </div></details></aside>
}
