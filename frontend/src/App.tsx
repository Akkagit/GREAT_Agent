import { useCallback, useEffect, useRef, useState } from 'react'
import './App.css'
import { GlobalConversation } from './components/GlobalConversation'
import { Alert, Button, Tag } from 'antd'
import './components/ExecutionPreview.css'
import { api } from './api/client'
import type { ExecutionPreview, ExperimentSpec, ExperimentSummary } from './api/types'
import { ExperimentWorkspace, type WorkbenchView, type WorkbenchSession } from './pages/ExperimentWorkspace'
import { ResizableWorkbench } from './components/ResizableWorkbench'
import { KnowledgeWorkspace } from './pages/KnowledgeWorkspace'
import { ResearchWorkspace } from './pages/ResearchWorkspace'
import { researchApi, type ResearchMaterial } from './api/research'

/* ── 默认实验规格 ────────────────────────────────────────────── */

const initialSpec: ExperimentSpec = {
  schema_version: '1.0',
  workflow: 'pppflt',
  scenario: { mode: 'static', frequency: 'DF', ambiguity: 'float', reset_hours: 0 },
  date_segments: [{ year: 2024, start_doy: 350, end_doy: 350 }],
  excluded_dates: [],
  station_group_id: 'default_15',
  stations: [
    'AHB0', 'BJB0', 'GDA4', 'GSE4', 'GXC4',
    'HBB2', 'HJE2', 'HNB1', 'JL18', 'LNC1',
    'QHA4', 'SCI5', 'SD10', 'SXE4', 'YNJ8',
  ],
  input_profile_id: 'local_default',
  solver_profile_id: 'great_local',
  template_profile_id: 'pppflt_base',
  concurrency: 1,
  outputs: ['flt', 'log'],
}

/* ── 工具函数 ────────────────────────────────────────────────── */

function readLocation() {
  const p = new URLSearchParams(window.location.search)
  const view = p.get('view')
  return {
    workspace: (p.get('workspace') === 'knowledge' ? 'knowledge' : p.get('workspace') === 'research' ? 'research' : 'workbench') as 'workbench' | 'knowledge' | 'research',
    researchSessionId: p.get('research_session') ?? undefined,
    knowledgeBaseId: p.get('knowledge_base') ?? undefined,
    knowledgeChatId: p.get('knowledge_chat') ?? undefined,
    experimentId: p.get('experiment'),
    view: (view === 'run' || view === 'review' || view === 'plots' ? view : 'plan') as WorkbenchView,
    previewId: p.get('preview'),
  }
}

function writeLocation(location: ReturnType<typeof readLocation>) {
  const params = new URLSearchParams()
  if (location.experimentId) params.set('experiment', location.experimentId)
  if (location.experimentId && location.view !== 'plan') params.set('view', location.view)
  if (location.experimentId && location.previewId) params.set('preview', location.previewId)
  if (location.workspace === 'knowledge') {
    params.set('workspace', 'knowledge')
    if (location.knowledgeBaseId) params.set('knowledge_base', location.knowledgeBaseId)
    if (location.knowledgeChatId) params.set('knowledge_chat', location.knowledgeChatId)
  }
  if (location.workspace === 'research') {
    params.set('workspace', 'research')
    if (location.researchSessionId) params.set('research_session', location.researchSessionId)
  }
  window.history.pushState({}, '', params.size ? `?${params}` : window.location.pathname)
}

function dateLabel(item: ExperimentSummary) {
  return item.spec.date_segments
    .map(s => {
      const start = String(s.start_doy).padStart(3, '0')
      const end = s.start_doy === s.end_doy ? '' : `–${String(s.end_doy).padStart(3, '0')}`
      return `${s.year} / DOY ${start}${end}`
    })
    .join('、')
}

const labels: Record<string, string> = {
  not_started: '未启动',
  pending: '等待执行',
  running: '运行中',
  succeeded: '已完成',
  failed: '有失败任务',
  interrupted: '已中断',
  cancelled: '已取消',
}

/* ── 主组件 ──────────────────────────────────────────────────── */

function App() {
  /* 路由 & 数据状态 */
  const [route, setRoute] = useState(() => ({ ...readLocation(), generation: 0 }))
  const [knowledgeVisited, setKnowledgeVisited] = useState(() => readLocation().workspace === 'knowledge')
  const [workbenchVisited, setWorkbenchVisited] = useState(() => readLocation().workspace === 'workbench')
  const [researchVisited, setResearchVisited] = useState(() => readLocation().workspace === 'research')
  const [researchHandoffError, setResearchHandoffError] = useState<string | null>(null)
  const researchHandoff = useRef(false)
  const routeRef = useRef(route)
  useEffect(() => { routeRef.current = route }, [route])
  const [experiments, setExperiments] = useState<ExperimentSummary[]>([])
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [historyError, setHistoryError] = useState(false)
  const [sessions, setSessions] = useState(() => new Map<string, WorkbenchSession>())

  /* 并发版本号，防止过期异步写入 */
  const refreshVersion = useRef(0)
  const navigationGeneration = useRef(0)

  /* 会话记忆 */
  const remember = useCallback(
    (id: string, value: WorkbenchSession) =>
      setSessions(current => { const next = new Map(current); next.set(id, value); return next }),
    [],
  )
  const rememberCurrent = useCallback(
    (value: WorkbenchSession) => { if (route.experimentId) remember(route.experimentId, value) },
    [route.experimentId, remember],
  )

  /* 刷新实验列表 */
  const refresh = useCallback(async () => {
    const version = ++refreshVersion.current
    try {
      const items = await api.listExperiments()
      if (version === refreshVersion.current) { setExperiments(items); setHistoryError(false) }
    } catch {
      if (version === refreshVersion.current) setHistoryError(true)
    }
  }, [])

  /* 浏览器前进/后退 */
  useEffect(() => {
    const pop = () => {
      const location = readLocation(), previous = routeRef.current
      const next = { ...location, researchSessionId: location.workspace === 'research' ? location.researchSessionId : previous.researchSessionId, knowledgeBaseId: location.workspace === 'knowledge' ? location.knowledgeBaseId : previous.knowledgeBaseId, knowledgeChatId: location.workspace === 'knowledge' ? location.knowledgeChatId : previous.knowledgeChatId, generation: ++navigationGeneration.current }
      if (next.workspace === 'knowledge') setKnowledgeVisited(true)
      else if (next.workspace === 'research') setResearchVisited(true)
      else setWorkbenchVisited(true)
      routeRef.current = next; setRoute(next)
    }
    window.addEventListener('popstate', pop)
    return () => window.removeEventListener('popstate', pop)
  }, [])

  /* 路由变化时拉取实验列表 */
  useEffect(() => {
    let disposed = false
    const version = ++refreshVersion.current
    api.listExperiments()
      .then(items => { if (!disposed && version === refreshVersion.current) { setExperiments(items); setHistoryError(false) } })
      .catch(() => { if (!disposed && version === refreshVersion.current) setHistoryError(true) })
    return () => { disposed = true }
  }, [route.experimentId])

  /* 有活跃实验时定时轮询 */
  const active = experiments.some(e => e.run_status === 'running' || e.run_status === 'pending')
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => void refresh(), 4000)
    return () => window.clearInterval(timer)
  }, [active, refresh])

  /* ── 导航 ──────────────────────────────────────────────────── */

  function navigate(
    experimentId: string | null,
    view: WorkbenchView = 'plan',
    previewId: string | null = experimentId === route.experimentId ? route.previewId : null,
  ) {
    const next = { ...routeRef.current, workspace: 'workbench' as const, experimentId, view, previewId, generation: ++navigationGeneration.current }
    writeLocation(next); routeRef.current = next; setRoute(next); setWorkbenchVisited(true)
  }

  function selectWorkspace(workspace: 'workbench' | 'knowledge' | 'research') {
    if (routeRef.current.workspace === workspace) return
    const next = { ...routeRef.current, workspace, generation: ++navigationGeneration.current }
    writeLocation(next); routeRef.current = next; setRoute(next)
    if (workspace === 'knowledge') setKnowledgeVisited(true)
    else if (workspace === 'research') setResearchVisited(true)
    else setWorkbenchVisited(true)
  }

  function navigateResearch(sessionId?: string) {
    const previous = routeRef.current
    const next = { ...previous, researchSessionId: sessionId, generation: previous.workspace === 'research' ? ++navigationGeneration.current : previous.generation }
    if (previous.workspace === 'research') writeLocation(next)
    routeRef.current = next; setRoute(next)
  }

  async function bringToResearch(material: Extract<ResearchMaterial, { kind: 'experiment' }>) {
    if (researchHandoff.current) return
    researchHandoff.current = true; setResearchHandoffError(null)
    const generation = navigationGeneration.current
    try {
      const session = await researchApi.create({ title: '实验结果分析', materials: [material], model_profile_id: 'default', mode: 'quick' })
      if (generation !== navigationGeneration.current) return
      const next = { ...routeRef.current, workspace: 'research' as const, researchSessionId: session.id, generation: ++navigationGeneration.current }
      writeLocation(next); routeRef.current = next; setRoute(next); setResearchVisited(true)
    } catch (error) { setResearchHandoffError(error instanceof Error ? error.message : '无法创建科研课题，请重试。') }
    finally { researchHandoff.current = false }
  }

  function navigateKnowledge(baseId?: string, chatId?: string) {
    const previous = routeRef.current
    const next = { ...previous, knowledgeBaseId: baseId, knowledgeChatId: chatId, generation: previous.workspace === 'knowledge' ? ++navigationGeneration.current : previous.generation }
    // Delayed discovery may remember a library while the user is on the experiment page.
    if (previous.workspace === 'knowledge') writeLocation(next)
    routeRef.current = next; setRoute(next)
  }

  function prepared(preview: ExecutionPreview) {
    if (route.workspace === 'workbench' && navigationGeneration.current === route.generation) navigate(preview.experiment_id, 'review', preview.id)
  }

  function started() {
    if (route.workspace === 'workbench' && navigationGeneration.current === route.generation) navigate(route.experimentId, 'run', null)
  }

  async function create() {
    setCreating(true)
    setError(null)
    try {
      const value = await api.createExperiment(initialSpec)
      navigate(value.id)
    } catch {
      setError('无法连接本地 API。请确认 GREAT Agent 服务正在运行。')
    } finally {
      setCreating(false)
    }
  }

  /* ── 侧边栏：实验历史 ─────────────────────────────────────── */

  const history = (
    <>
      <header className="history-header">
        <div>
          <h2>实验记录</h2>
          <p>历史与正在进行的实验</p>
        </div>
        <Button onClick={() => navigate(null)}>＋ 新建</Button>
      </header>

      <div className="history-experiments">
        {historyError && (
          <p className="sidebar-note" role="status">历史记录读取失败，请检查本地服务。</p>
        )}
        {experiments.length === 0 && !historyError && (
          <p className="sidebar-note">尚无实验，创建后会保存在这里。</p>
        )}
        {experiments.map(item => (
          <Button
            key={item.id}
            aria-label={`查看实验 ${dateLabel(item)} ${item.id}`}
            className={`sidebar-experiment ${item.id === route.experimentId ? 'selected' : ''}`}
            onClick={() => navigate(item.id, item.task_count ? 'run' : 'plan', null)}
          >
            <div>
              <small>Revision {item.current_revision}</small>
              <Tag
                color={
                  item.run_status === 'succeeded' ? 'success'
                    : item.run_status === 'failed' || item.run_status === 'interrupted' ? 'error'
                      : item.run_status === 'running' || item.run_status === 'pending' ? 'blue'
                        : undefined
                }
              >
                {labels[item.run_status] ?? item.run_status}
              </Tag>
            </div>
            <strong>{dateLabel(item)}</strong>
            <span>
              {item.spec.scenario.mode === 'static' ? '静态' : '动态'}
              {' · '}
              {item.spec.scenario.frequency}
              {' · '}
              {item.spec.scenario.ambiguity === 'float' ? '浮点解' : '固定解'}
              {' · '}
              {item.spec.stations.length} 站
            </span>
            {item.task_count > 0 && (
              <p className="history-count">
                已成功 {item.succeeded_count} / {item.task_count} 个日任务
              </p>
            )}
          </Button>
        ))}
      </div>

      <footer className="history-safety">
        <strong>模型只提出修改</strong>
        <p>核对实际 XML、程序、输入与输出并明确确认后，才允许启动 GREAT。</p>
        <small>正式目录：F:\GREAT_Agent</small>
      </footer>
    </>
  )

  /* ── 首页主体 ──────────────────────────────────────────────── */

  const home = (
    <main className="experiment-home">
      <p className="home-eyebrow">GREAT Agent · PPP-FLT</p>
      <h1>建立首个 PPP-FLT 实验</h1>
      <p>
        从已配置的 2024 年 DOY 350 与 15 个测站开始。
        创建只保存计划，不会启动 GREAT；已有实验可从左侧查看。
      </p>

      {error && <Alert className="error-banner" type="error" role="alert" title={error} />}

      <Button type="primary" disabled={creating} onClick={() => void create()}>
        {creating ? '正在创建…' : '创建实验并进入规划台'}
      </Button>

      <div className="home-workflow">
        <h2>从提案到执行，每一步都可复核</h2>
        <p>
          接受提案 → 检查输入 → 生成 XML → 执行预览 → 明确确认 → 解算 → 运行监控
        </p>
        <dl>
          <div>
            <dt>默认方案</dt>
            <dd>静态 · DF · Float</dd>
          </div>
          <div>
            <dt>测站</dt>
            <dd>15 个</dd>
          </div>
          <div>
            <dt>真实执行</dt>
            <dd>等待你的明确确认</dd>
          </div>
        </dl>
      </div>
    </main>
  )

  /* ── 渲染 ──────────────────────────────────────────────────── */

  return (
    <div className="application">
      {/* 顶栏 */}
      <header className="application-header">
        <Button className="application-brand" onClick={() => navigate(null)} aria-label="实验首页">
          <span className="brand-symbol" aria-hidden="true">G</span>
          <span>
            <strong>GREAT Agent</strong>
            <small>PPP-FLT 本地实验工作台</small>
          </span>
        </Button>
        <nav className="application-workspace-tabs" role="tablist" aria-label="工作空间">
          <button id="workspace-workbench-tab" role="tab" aria-selected={route.workspace === 'workbench'} aria-controls="workspace-workbench-panel" onClick={() => selectWorkspace('workbench')}>实验工作台</button>
          <button id="workspace-knowledge-tab" role="tab" aria-selected={route.workspace === 'knowledge'} aria-controls="workspace-knowledge-panel" onClick={() => selectWorkspace('knowledge')}>文献知识库</button>
          <button id="workspace-research-tab" role="tab" aria-selected={route.workspace === 'research'} aria-controls="workspace-research-panel" onClick={() => selectWorkspace('research')}>科研分析</button>
        </nav>
        <span className="application-local">
          <i />{route.workspace === 'research' ? '只读科研 · 证据与持续追问' : route.workspace === 'knowledge' ? '本地文献 · 按所选模型回答' : '本机模式 · 模型辅助咨询与规划'}
        </span>
      </header>
      {researchHandoffError && <Alert role="alert" type="error" title={researchHandoffError} closable onClose={() => setResearchHandoffError(null)} />}

      {/* 主内容区 */}
      <div className="application-workspace" id="workspace-workbench-panel" role="tabpanel" aria-labelledby="workspace-workbench-tab" hidden={route.workspace !== 'workbench'}>
      {workbenchVisited && (route.experimentId ? (
        <ExperimentWorkspace
          key={route.experimentId}
          experimentId={route.experimentId}
          history={history}
          view={route.view}
          previewId={route.previewId}
          onViewChange={view => navigate(route.experimentId, view)}
          onPrepared={prepared}
          onRun={started}
          onChanged={() => void refresh()}
          session={sessions.get(route.experimentId)}
          onSessionChange={rememberCurrent}
          onResearch={material => void bringToResearch(material)}
        />
      ) : (
        <ResizableWorkbench history={history} center={home} chat={<GlobalConversation onOpenExperiment={id=>navigate(id)} onExperimentCreated={()=>void refresh()}/>} />
      ))}
      </div>
      <div className="application-workspace application-knowledge-content" id="workspace-knowledge-panel" role="tabpanel" aria-labelledby="workspace-knowledge-tab" hidden={route.workspace !== 'knowledge'}>
        {knowledgeVisited && <KnowledgeWorkspace baseId={route.knowledgeBaseId} chatId={route.knowledgeChatId} onLocationChange={navigateKnowledge} />}
      </div>
      <div className="application-workspace" id="workspace-research-panel" role="tabpanel" aria-labelledby="workspace-research-tab" hidden={route.workspace !== 'research'}>
        {researchVisited && <ResearchWorkspace sessionId={route.researchSessionId} onLocationChange={navigateResearch} />}
      </div>
    </div>
  )
}

export default App
