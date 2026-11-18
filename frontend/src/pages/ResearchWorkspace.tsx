import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Select, Tag } from 'antd'
import { ApiError } from '../api/client'
import { researchApi, researchMaterialKey } from '../api/research'
import type { ResearchCatalog, ResearchItem, ResearchMaterial, ResearchReport, ResearchRun, ResearchRunInput, ResearchSession, ResearchSuggestionFields, ResearchTarget } from '../api/research'
import { ResizableWorkbench, type WorkbenchPane } from '../components/ResizableWorkbench'
import { ResearchMaterials } from '../components/ResearchMaterials'
import { ResearchEvidencePanel, type ResearchEvidenceSelection } from '../components/ResearchEvidencePanel'
import { MarkdownContent } from '../components/MarkdownContent'
import './ResearchWorkspace.css'

interface Props { sessionId?: string; onLocationChange: (sessionId?: string) => void }
interface RetryIntent { run_id: string; request_id: string; message: string }
interface Draft { text: string; target?: ResearchTarget; targetText?: string; pending?: ResearchRunInput; retry?: RetryIntent; staleRetryQuestion?: string }
interface MaterialDraft { materials: ResearchMaterial[]; model_profile_id: string; mode: 'quick' | 'deep' }
const emptyDraft: Draft = { text: '' }
function readLocal<T>(key: string, fallback: T): T { try { return JSON.parse(sessionStorage.getItem(key) ?? 'null') ?? fallback } catch { return fallback } }
function writeLocal(key: string, value: unknown) { try { if (value === null) sessionStorage.removeItem(key); else sessionStorage.setItem(key, JSON.stringify(value)) } catch { /* The workspace still works when browser storage is unavailable. */ } }
function errorText(error: unknown) { return error instanceof Error ? error.message : '请求失败，请检查本地服务。' }
function mergeReports(current: ResearchReport[], incoming: ResearchReport[]) {
  return [...new Map([...incoming, ...current].map(report => [report.id, report])).values()].sort((a, b) => (b.version ?? 0) - (a.version ?? 0))
}
const terminal = (status: string) => ['completed', 'partial', 'needs_input', 'cancelled', 'failed', 'interrupted', 'timed_out'].includes(status)
const retryable = (status: string) => ['failed', 'cancelled', 'interrupted', 'timed_out'].includes(status)
const stages: Record<string, string> = { queued: '排队等待', preparing: '固定本轮材料', running: '分析中', analyzing: '模型分析中', gathering: '收集证据', reading_materials: '读取材料', gathering_evidence: '收集证据', validating: '核对回答', completed: '分析完成', partial: '局部结论', needs_input: '等待补充材料', cancelled: '已停止', failed: '分析失败', interrupted: '分析已中断', timed_out: '预算时间已用尽' }
const itemLabels: Record<string, string> = { hypothesis: '假设', observation: '观察', suggestion: '建议', question: '待补充', open_question: '待补充', limitation: '限制', user_statement: '用户陈述' }
const itemStatus: Record<string, string> = { proposed: '待核查', support_increased: '支持增加', challenged: '存在反例', rejected: '已否定', superseded: '已有修订', user_provided: '用户提供' }

function ResearchSuggestionDetails({ suggestion }: { suggestion: ResearchSuggestionFields }) {
  return <div className="research-suggestion-details">
    <dl className="research-locator">{([['target', '目标位置'], ['rationale', '依据与原因'], ['impact', '预期影响'], ['validation', '人工验证步骤']] as const).map(([field, label]) => suggestion[field] ? <div key={field}><dt>{label}</dt><dd>{suggestion[field]}</dd></div> : null)}</dl>
    {suggestion.change && <><p className="research-hint">建议差异，尚未应用或执行。</p><pre className="research-suggestion-change" aria-label="未应用建议差异">{suggestion.change}</pre></>}
  </div>
}

function ResearchMessageResult({ run }: { run: ResearchRun }) {
  if (!run.result) return null
  const { answer_status: status, limitations = [], suggestions = [] } = run.result
  return <div className="research-message-result">
    {(status === 'partial' || status === 'needs_input') && <Tag color="warning">{stages[status]}</Tag>}
    {!!limitations.length && <section aria-label={`回答限制 ${run.id}`}><h3>该轮限制</h3><ul>{limitations.map((text, index) => <li key={index}>{text}</li>)}</ul></section>}
    {!!suggestions.length && <section aria-label={`该轮修改建议 ${run.id}`}><h3>该轮修改建议</h3>{suggestions.map((suggestion, index) => <article key={suggestion.id ?? index}><MarkdownContent>{suggestion.text}</MarkdownContent><ResearchSuggestionDetails suggestion={suggestion} /></article>)}</section>}
  </div>
}

function ResearchState({ items, onFollowup, onSource }: { items: ResearchItem[]; onFollowup: (target: ResearchTarget, text: string) => void; onSource: (runId: string, alias: string) => void }) {
  return <section className="research-items" aria-label="研究状态"><h2>观察与假设</h2>{items.map(item => <article key={item.id}>
    <div className="research-title-row"><Tag>{itemLabels[item.kind] ?? item.kind}</Tag><small>{itemStatus[item.status] ?? item.status}</small></div>
    <MarkdownContent>{item.text}</MarkdownContent>
    {item.kind === 'suggestion' && <ResearchSuggestionDetails suggestion={item} />}
    {item.quote && <p className="research-hint">用户原话：{item.quote}</p>}
    {item.unknowns?.length ? <p className="research-hint">待核查：{item.unknowns.join('；')}</p> : null}
    <div className="research-block-actions"><small>支持 {item.support?.length ?? 0} · 反对 {item.opposition?.length ?? 0}</small>
      {item.support_refs?.map(reference => <Button key={reference.evidence_id} size="small" aria-label={`支持证据 ${reference.alias}`} onClick={() => onSource(reference.run_id, reference.alias)}>支持 [{reference.alias}]</Button>)}
      {item.opposition_refs?.map(reference => <Button key={reference.evidence_id} size="small" aria-label={`反对证据 ${reference.alias}`} onClick={() => onSource(reference.run_id, reference.alias)}>反对 [{reference.alias}]</Button>)}
      {item.origin_run_id && <Button type="link" size="small" aria-label={`追问${itemLabels[item.kind] ?? item.kind} ${item.id}`} onClick={() => onFollowup({ run_id: item.origin_run_id!, item_id: item.id }, item.text)}>对此追问</Button>}
    </div>
  </article>)}</section>
}

function ResearchRunStatus({ run, active, stage, onRetry, retryDisabled }: { run: ResearchRun | null; active: boolean; stage?: string; onRetry?: () => void; retryDisabled?: boolean }) {
  const answerStatus = run?.result?.answer_status
  const label = answerStatus === 'partial' || answerStatus === 'needs_input' ? stages[answerStatus] : stages[run?.stage ?? stage ?? 'queued'] ?? run?.stage ?? '分析中'
  return <div className="research-run-status" role="status"><strong>{label}</strong>
    <span>{active ? '切换页面后继续运行，可编辑下一轮草稿。' : stages[run?.status ?? ''] ?? run?.status}</span>
    {run?.error_message && <p>{run.error_message}</p>}{run?.error_code && <small>{run.error_code}</small>}
    {run?.result?.limitations?.length ? <section aria-label="本轮限制"><h3>本轮限制</h3><ul>{run.result.limitations.map((limitation, index) => <li key={index}>{limitation}</li>)}</ul></section> : null}
    {run?.snapshot_summary?.length ? <details><summary>本轮固定材料</summary><pre>{JSON.stringify(run.snapshot_summary, null, 2)}</pre></details> : null}
    {!active && run && retryable(run.status) && onRetry && <div className="research-retry-actions"><Button disabled={retryDisabled} onClick={onRetry}>重试本轮分析</Button><p className="research-hint">使用原轮固定材料与配置重试，保留当前草稿。</p></div>}
  </div>
}

export function ResearchWorkspace({ sessionId, onLocationChange }: Props) {
  const [catalog, setCatalog] = useState<ResearchCatalog | null>(null), [sessions, setSessions] = useState<ResearchSession[]>([])
  const [error, setError] = useState<string | null>(null)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    Promise.allSettled([researchApi.catalog(), researchApi.list()]).then(([materials, history]) => {
      if (!alive.current) return
      if (materials.status === 'fulfilled') setCatalog(materials.value); else setError(errorText(materials.reason))
      if (history.status === 'fulfilled') setSessions(history.value.items); else setError(errorText(history.reason))
    })
    return () => { alive.current = false }
  }, [])
  const remember = useCallback((session: ResearchSession) => { if (alive.current) setSessions(current => [session, ...current.filter(row => row.id !== session.id)]) }, [])
  async function refreshCatalog() {
    try { const next = await researchApi.catalog(); if (alive.current) { setCatalog(next); setError(null) } } catch (error) { if (alive.current) setError(errorText(error)) }
  }
  return <main className="research-workspace" aria-label="科研分析工作区"><ResearchTopic key={sessionId ?? 'new'} sessionId={sessionId} onLocationChange={onLocationChange} catalog={catalog} sessions={sessions} onRemember={remember} discoveryError={error} onRefreshCatalog={() => void refreshCatalog()} /></main>
}

function ResearchTopic({ sessionId, onLocationChange, catalog, sessions, onRemember, discoveryError, onRefreshCatalog }: Props & { catalog: ResearchCatalog | null; sessions: ResearchSession[]; onRemember: (session: ResearchSession) => void; discoveryError: string | null; onRefreshCatalog: () => void }) {
  const draftKey = `great-agent-research-draft-v1:${sessionId ?? 'new'}`
  const materialKey = `great-agent-research-materials-v1:${sessionId ?? 'new'}`
  const [draft, setDraftState] = useState<Draft>(() => readLocal(draftKey, emptyDraft)), draftRef = useRef(draft)
  const [detail, setDetail] = useState<ResearchSession | null>(null), detailRef = useRef(detail)
  const [run, setRun] = useState<ResearchRun | null>(null)
  const [materials, setMaterials] = useState<ResearchMaterial[]>(() => readLocal<MaterialDraft | null>(materialKey, null)?.materials ?? [])
  const [modelId, setModelId] = useState('default'), [mode, setMode] = useState<'quick' | 'deep'>('quick')
  const [title, setTitle] = useState(''), [goal, setGoal] = useState('')
  const [loading, setLoading] = useState(!!sessionId), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null)
  const [reports, setReports] = useState<ResearchReport[]>([]), [reportId, setReportId] = useState(''), [view, setView] = useState<'chat' | 'reports'>('chat')
  const [source, setSource] = useState<ResearchEvidenceSelection | null>(null), [mobilePane, setMobilePane] = useState<WorkbenchPane>('center')
  const owner = useRef(true), lock = useRef(false), refreshGeneration = useRef(0)
  function updateDraft(value: Draft) { draftRef.current = value; writeLocal(draftKey, value); if (owner.current) setDraftState(value) }
  const acceptDetail = useCallback((value: ResearchSession) => {
    const previous = detailRef.current
    if (previous && (value.version < previous.version || (value.version === previous.version && value.state_revision < previous.state_revision))) return
    detailRef.current = value; setDetail(value); onRemember(value)
  }, [onRemember])
  const reload = useCallback(async () => {
    if (!sessionId) return null
    const generation = ++refreshGeneration.current, next = await researchApi.get(sessionId)
    if (owner.current && generation === refreshGeneration.current) acceptDetail(next)
    return next
  }, [sessionId, acceptDetail])
  useEffect(() => {
    owner.current = true
    if (sessionId) {
      researchApi.get(sessionId).then(next => {
        if (!owner.current) return
        acceptDetail(next)
        const saved = readLocal<MaterialDraft | null>(materialKey, null)
        setMaterials(saved?.materials ?? next.materials); setModelId(saved?.model_profile_id ?? next.settings.model_profile_id); setMode(saved?.mode ?? next.settings.mode)
      }).catch(error => { if (owner.current) setError(errorText(error)) }).finally(() => { if (owner.current) setLoading(false) })
      researchApi.reports(sessionId).then(value => { if (owner.current) { setReports(current => mergeReports(current, value.items)); setReportId(current => current || value.items[0]?.id || '') } }).catch(error => { if (owner.current) setError(errorText(error)) })
    }
    // This ref fences requests rather than referring to a rendered DOM node.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
    return () => { owner.current = false; refreshGeneration.current++ }
  }, [sessionId, materialKey, acceptDetail])
  const activeRunId = detail?.active_run_id
  const lastRunId = detail?.messages.at(-1)?.run_id
  useEffect(() => {
    if (activeRunId || !lastRunId) return
    let disposed = false
    researchApi.run(lastRunId).then(latest => { if (!disposed) setRun(latest) }).catch(() => { /* History remains readable if only the final status request fails. */ })
    return () => { disposed = true }
  }, [lastRunId, activeRunId])
  useEffect(() => {
    if (!activeRunId) return
    let disposed = false, polling = false
    const poll = async () => {
      if (polling) return
      polling = true
      try {
        const latest = await researchApi.run(activeRunId)
        if (disposed) return
        setRun(latest)
        const pending = draftRef.current.pending
        if (pending && latest.request_id === pending.request_id) {
          const current = draftRef.current
          updateDraft({ ...current, pending: undefined, ...(current.text.trim() === pending.message ? { text: '', target: undefined, targetText: undefined } : {}) })
        }
        if (draftRef.current.retry?.request_id === latest.request_id) updateDraft({ ...draftRef.current, retry: undefined, staleRetryQuestion: undefined })
        const next = await researchApi.get(sessionId!)
        if (disposed) return
        acceptDetail(next)
      } catch (error) { if (!disposed) setError(`状态读取失败，后台继续处理。${errorText(error)}`) }
      finally { polling = false }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 1500)
    return () => { disposed = true; window.clearInterval(timer) }
    // No cancellation: runs are durable server jobs independent of this subscriber.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRunId, sessionId, acceptDetail])
  const savedSettings = detail?.settings
  const materialsChanged = !!detail && researchMaterialKey(materials) !== researchMaterialKey(detail.materials)
  const settingsChanged = !!savedSettings && (modelId !== savedSettings.model_profile_id || mode !== savedSettings.mode)
  function stageMaterials(next: ResearchMaterial[], nextModel = modelId, nextMode = mode) {
    setMaterials(next); writeLocal(materialKey, { materials: next, model_profile_id: nextModel, mode: nextMode })
  }
  async function create() {
    if (lock.current || !title.trim()) return
    lock.current = true; setBusy(true); setError(null)
    try {
      const next = await researchApi.create({ title: title.trim(), goal: goal.trim(), materials, model_profile_id: modelId, mode })
      if (!owner.current) return
      onRemember(next); writeLocal(`great-agent-research-draft-v1:${next.id}`, draftRef.current); writeLocal(materialKey, null); onLocationChange(next.id)
    } catch (error) { if (owner.current) setError(errorText(error)) }
    finally { if (owner.current) { lock.current = false; setBusy(false) } }
  }
  async function update(kind: 'materials' | 'settings') {
    const current = detailRef.current
    if (!current || lock.current) return
    lock.current = true; setBusy(true); setError(null)
    try {
      const next = kind === 'materials' ? await researchApi.materials(current.id, { expected_version: current.version, materials }) : await researchApi.settings(current.id, { expected_version: current.version, model_profile_id: modelId, mode })
      if (!owner.current) return
      ++refreshGeneration.current; acceptDetail(next)
      // Keep edits made while the request was in flight; only acknowledgement changes the baseline.
      const staged = readLocal<MaterialDraft | null>(materialKey, null)
      if (staged && researchMaterialKey(staged.materials) === researchMaterialKey(next.materials) && staged.model_profile_id === next.settings.model_profile_id && staged.mode === next.settings.mode) writeLocal(materialKey, null)
    } catch (error) {
      if (!owner.current) return
      setError(errorText(error))
      if (error instanceof ApiError && error.status === 409) { try { await reload() } catch { /* The original conflict remains visible and drafts remain intact. */ } }
    } finally { if (owner.current) { lock.current = false; setBusy(false) } }
  }
  function acknowledge(input: ResearchRunInput, next: ResearchRun) {
    if (!owner.current) return
    const current = draftRef.current
    updateDraft({ ...current, pending: undefined, ...(current.text.trim() === input.message ? { text: '', target: undefined, targetText: undefined } : {}) })
    setRun(next)
    if (detailRef.current) acceptDetail({ ...detailRef.current, active_run_id: terminal(next.status) ? null : next.id })
  }
  async function send(recover = false) {
    const current = detailRef.current
    if (!current || lock.current || current.active_run_id || draftRef.current.retry || (!recover && (draftRef.current.pending || materialsChanged || settingsChanged))) return
    const input = recover ? draftRef.current.pending : { request_id: crypto.randomUUID(), expected_version: current.version, message: draftRef.current.text.trim(), ...(draftRef.current.target ? { followup_target: draftRef.current.target } : {}) }
    if (!input?.message) return
    lock.current = true; setBusy(true); setError(null)
    updateDraft({ ...draftRef.current, pending: input })
    try {
      const next = await researchApi.send(current.id, input)
      if (!owner.current) return
      acknowledge(input, next); await reload()
    } catch (error) {
      if (!owner.current) return
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        updateDraft({ ...draftRef.current, pending: undefined }); setError(errorText(error)); try { await reload() } catch { /* Preserve draft and the original error. */ }
      } else {
        setError(`提交结果尚未确认，原问题与请求身份已保留。${errorText(error)}`)
        try {
          const latest = await reload()
          if (!owner.current) return
          if (latest?.active_run_id) { const accepted = await researchApi.run(latest.active_run_id); if (accepted.request_id === input.request_id && owner.current) { acknowledge(input, accepted); setError(null) } }
        } catch { /* Explicit recovery replays the original request ID and exact payload. */ }
      }
    } finally { if (owner.current) { lock.current = false; setBusy(false) } }
  }
  function acknowledgeRetry(next: ResearchRun) {
    if (!owner.current) return
    updateDraft({ ...draftRef.current, retry: undefined, staleRetryQuestion: undefined }); setRun(next)
    if (detailRef.current) acceptDetail({ ...detailRef.current, active_run_id: terminal(next.status) ? null : next.id })
  }
  async function retryRun(recover = false) {
    if (!detailRef.current || detailRef.current.active_run_id || lock.current || draftRef.current.pending) return
    const intent = recover ? draftRef.current.retry : run && retryable(run.status) ? { run_id: run.id, request_id: crypto.randomUUID(), message: run.message } : undefined
    if (!intent || (!recover && draftRef.current.retry)) return
    lock.current = true; setBusy(true); setError(null); updateDraft({ ...draftRef.current, retry: intent, staleRetryQuestion: undefined })
    try {
      const next = await researchApi.retry(intent.run_id, intent.request_id)
      if (!owner.current) return
      acknowledgeRetry(next)
      try { await reload() } catch { if (owner.current) setError('重试已接受，暂时无法刷新消息；后台继续处理。') }
    } catch (error) {
      if (!owner.current) return
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        updateDraft({ ...draftRef.current, retry: undefined, ...(error.code === 'stale_retry' ? { staleRetryQuestion: intent.message } : {}) }); setError(errorText(error))
        try { await reload() } catch { /* Retain the original failure and the editable draft. */ }
      } else {
        setError(`重试结果尚未确认，原轮次和请求身份已保留。${errorText(error)}`)
        try {
          const latest = await reload()
          if (!owner.current) return
          const accepted = latest?.runs?.find(candidate => candidate.request_id === intent.request_id) ?? (latest?.active_run_id ? await researchApi.run(latest.active_run_id) : undefined)
          if (accepted?.request_id === intent.request_id && owner.current) { acknowledgeRetry(accepted); setError(null) }
        } catch { /* Recovery replays this exact run ID and request ID. */ }
      }
    } finally { if (owner.current) { lock.current = false; setBusy(false) } }
  }
  function continueOriginalQuestion() {
    const current = draftRef.current, question = current.staleRetryQuestion
    if (!question) return
    updateDraft({ ...current, text: current.text.trim() && current.text.trim() !== question.trim() ? `${current.text}\n\n${question}` : question, target: undefined, targetText: undefined, staleRetryQuestion: undefined })
    setError(null); setView('chat'); setMobilePane('center')
  }
  async function stop() {
    if (!detail?.active_run_id || lock.current) return
    lock.current = true; setBusy(true)
    try { const next = await researchApi.cancel(detail.active_run_id); if (owner.current) { setRun(next); await reload() } } catch (error) { if (owner.current) setError(errorText(error)) }
    finally { if (owner.current) { lock.current = false; setBusy(false) } }
  }
  function followup(target: ResearchTarget, text: string) { updateDraft({ ...draftRef.current, target, targetText: text }); setView('chat'); setMobilePane('center') }
  async function saveReport() {
    if (!detail || lock.current) return
    lock.current = true; setBusy(true); setError(null)
    try {
      const next = await researchApi.saveReport(detail.id, { title: `${detail.title.slice(0, 198)}报告` })
      if (!owner.current) return
      setReports(current => mergeReports([next, ...current.filter(row => row.id !== next.id)], [])); setReportId(next.id); setView('reports')
    } catch (error) { if (owner.current) setError(errorText(error)) }
    finally { if (owner.current) { lock.current = false; setBusy(false) } }
  }
  const selectedReport = reports.find(report => report.id === reportId) ?? reports[0]
  const runsById = new Map(detail?.runs?.map(historical => [historical.id, historical]) ?? [])
  if (run) runsById.set(run.id, run)
  const history = <div className="research-sidebar"><header><p className="research-eyebrow">RESEARCH NOTEBOOK</p><div className="research-title-row"><h2>课题与材料</h2><Button size="small" onClick={() => onLocationChange(undefined)}>新建课题</Button></div></header>
    <nav className="research-history" aria-label="科研课题历史">{sessions.map(session => <button key={session.id} aria-current={session.id === sessionId ? 'page' : undefined} onClick={() => onLocationChange(session.id)}><strong>{session.title || '未命名课题'}</strong><small>{session.active_run_id ? '分析进行中' : `材料修订 ${session.material_revision}`}</small></button>)}{!sessions.length && <p className="research-hint">课题与每轮回答保存在本机。</p>}</nav>
    <div className="research-material-section"><div className="research-title-row"><h3>材料范围</h3><Button type="link" size="small" onClick={onRefreshCatalog}>刷新目录</Button></div><ResearchMaterials catalog={catalog} value={materials} onChange={stageMaterials} historyRuns={detail?.runs} />{sessionId && <Button disabled={busy || loading || !detail} onClick={() => void update('materials')}>更新材料</Button>}</div>
  </div>
  const center = <div className="research-main">
    {(error || discoveryError) && <Alert type="error" role="alert" title={error ?? discoveryError} closable onClose={() => setError(null)} />}
    {loading ? <p className="research-empty" role="status">正在恢复课题与运行…</p> : sessionId && !detail ? <div className="research-empty"><p>课题无法读取。</p><Button onClick={() => void reload().catch(error => setError(errorText(error)))}>重新读取</Button></div> : <>
      <header className="research-main-header"><p className="research-eyebrow">READ · COMPARE · QUESTION</p><h1>{detail?.title ?? '建立科研课题'}</h1><p>{detail?.goal || '围绕实验结果、代码与文献，持续核对解释与研究假设。'}</p><div className="research-title-row"><Tag color="blue">只读诊断与建议</Tag>{detail && <small>材料 r{detail.material_revision} · 状态 r{detail.state_revision}</small>}</div></header>
      {!sessionId && <form className="research-create" onSubmit={event => { event.preventDefault(); void create() }}><label>课题标题<Input aria-label="课题标题" value={title} onChange={event => setTitle(event.target.value)} placeholder="如：TF 与 DF 收敛差异" maxLength={200} /></label><label>研究目标<Input.TextArea aria-label="研究目标" value={goal} onChange={event => setGoal(event.target.value)} rows={3} placeholder="要解释的现象、关注站点与约束" /></label><Button type="primary" htmlType="submit" disabled={busy || !title.trim()}>创建课题</Button><p className="research-hint">创建后核对材料，再发送首个问题。</p></form>}
      <div className="research-settings"><label>主模型<Select aria-label="科研主模型" virtual={false} value={modelId} onChange={id => { setModelId(id); stageMaterials(materials, id, mode) }} options={(catalog?.profiles ?? [{ id: 'default', name: '默认模型', configured: true }]).map(profile => ({ value: profile.id, label: `${profile.name}${profile.configured ? '' : '（未配置）'}`, disabled: !profile.configured }))} /></label><label>分析深度<Select aria-label="分析深度" virtual={false} value={mode} onChange={next => { setMode(next); stageMaterials(materials, modelId, next) }} options={[{ value: 'quick', label: '快速 · 3 分钟预算' }, { value: 'deep', label: '深入 · 8 分钟预算' }]} /></label>{detail && <Button disabled={busy || !settingsChanged} onClick={() => void update('settings')}>保存下轮配置</Button>}</div>
      {detail && <><nav className="research-tabs" role="tablist" aria-label="科研视图"><button id="research-chat-tab" role="tab" aria-controls="research-chat-panel" aria-selected={view === 'chat'} onClick={() => setView('chat')}>研究对话</button><button id="research-reports-tab" role="tab" aria-controls="research-reports-panel" aria-selected={view === 'reports'} onClick={() => setView('reports')}>报告 ({reports.length})</button><Button size="small" disabled={busy || !!activeRunId || !detail.messages.some(message => message.role === 'assistant' && ['completed', 'partial', 'needs_input'].includes(message.status))} onClick={() => void saveReport()}>保存分析报告</Button></nav>
        <section id="research-chat-panel" className="research-chat-body" role="tabpanel" aria-labelledby="research-chat-tab" hidden={view !== 'chat'}>
          <div className="research-messages" aria-live="polite">{!detail.messages.length && <div className="research-empty"><h2>先从一个具体问题开始</h2><p>例如：只比较相同站点与日期，解释收敛时间差异，并列出缺失依据。</p></div>}{detail.messages.map(message => <article className={`research-message research-message-${message.role}`} aria-label={`科研消息 ${message.id}`} key={message.id}><div className="research-message-heading"><strong>{message.role === 'user' ? '你' : '科研分析'}</strong><small>{message.role === 'assistant' ? stages[message.status] ?? message.status : ''}</small></div>{message.blocks?.length ? message.blocks.map(block => <div className="research-answer-block" key={block.id}><MarkdownContent>{block.text}</MarkdownContent><div className="research-block-actions">{block.evidence_ids?.map(alias => <Button key={alias} size="small" aria-label={`查看证据 ${alias}`} onClick={() => { setSource({ sessionId: detail.id, runId: message.run_id, alias }); setMobilePane('chat') }}>[{alias}]</Button>)}{message.role === 'assistant' && <Button type="link" size="small" aria-label={`追问回答 ${block.id}`} onClick={() => followup({ run_id: message.run_id, message_id: message.id, block_id: block.id }, block.text)}>对此追问</Button>}</div></div>) : <MarkdownContent>{message.text}</MarkdownContent>}{message.role === 'assistant' && detail.runs && runsById.get(message.run_id) && <ResearchMessageResult run={runsById.get(message.run_id)!} />}</article>)}</div>
          {!!detail.items.length && <ResearchState items={detail.items} onFollowup={followup} onSource={(runId, alias) => { setSource({ sessionId: detail.id, runId, alias }); setMobilePane('chat') }} />}
          {(activeRunId || run) && <ResearchRunStatus run={run} active={!!activeRunId} stage={detail.stage} onRetry={() => void retryRun()} retryDisabled={busy || !!draft.retry || !!draft.pending} />}
        </section>
        <section id="research-reports-panel" className="research-report-view" role="tabpanel" aria-labelledby="research-reports-tab" hidden={view !== 'reports'}>{reports.length ? <><Select aria-label="已保存报告" virtual={false} value={selectedReport?.id} onChange={setReportId} options={reports.map(report => ({ value: report.id, label: report.title }))} /><h2>{selectedReport?.title}</h2><p className="research-hint">已保存的固定版本 · {selectedReport?.created_at ?? selectedReport?.id}</p><MarkdownContent>{selectedReport?.content ?? ''}</MarkdownContent>{selectedReport?.snapshot?.evidence?.length ? <section className="research-report-sources" aria-label="报告证据"><h3>报告来源</h3>{selectedReport.snapshot.evidence.map(evidence => <div key={`${evidence.run_id}-${evidence.alias}`}><span>{evidence.title} · 轮次 {evidence.run_id}</span>{evidence.available ? <Button size="small" aria-label={`查看报告证据 ${evidence.run_id} ${evidence.alias}`} onClick={() => { setSource({ sessionId: detail.id, runId: evidence.run_id, alias: evidence.alias }); setMobilePane('chat') }}>[{evidence.alias}]</Button> : <Tag>来源当前不可用</Tag>}</div>)}</section> : null}</> : <p className="research-empty">完成分析后，可保存并在这里查看报告。</p>}</section>
        <form className="research-composer" onSubmit={event => { event.preventDefault(); void send() }}>{draft.target && <div className="research-followup"><strong>对此追问</strong><p>{draft.targetText}</p><Button type="text" size="small" onClick={() => updateDraft({ ...draftRef.current, target: undefined, targetText: undefined })}>取消追问目标</Button></div>}<Input.TextArea aria-label="科研问题" value={draft.text} onChange={event => updateDraft({ ...draftRef.current, text: event.target.value })} rows={3} placeholder="输入问题、质疑或补充约束…" /><div className="research-composer-actions"><span>{materialsChanged || settingsChanged ? '请先保存材料或配置，再发送。' : `本轮使用 ${mode === 'quick' ? '快速' : '深入'}模式 · 材料 r${detail.material_revision}`}</span><div>{draft.pending && <Button disabled={busy || !!activeRunId} onClick={() => void send(true)}>恢复提交</Button>}{draft.retry && <Button disabled={busy || !!activeRunId} onClick={() => void retryRun(true)}>恢复重试</Button>}{draft.staleRetryQuestion && <Button disabled={busy} onClick={continueOriginalQuestion}>以原问题继续追问</Button>}{activeRunId && <Button danger disabled={busy} onClick={() => void stop()}>停止分析</Button>}<Button type="primary" htmlType="submit" disabled={busy || !!activeRunId || !!draft.pending || !!draft.retry || !draft.text.trim() || materialsChanged || settingsChanged}>发送问题</Button></div></div></form>
      </>}
    </>}
  </div>
  return <ResizableWorkbench idPrefix="research" layoutKey="great-agent-research-layout-v1" labels={{ history: '课题', center: '对话', chat: '证据' }} mobilePane={mobilePane} onMobilePaneChange={setMobilePane} history={history} center={center} chat={<ResearchEvidencePanel selection={source} summary={detail?.material_summary ?? []} />} />
}
