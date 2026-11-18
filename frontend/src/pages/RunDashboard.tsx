import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Modal } from 'antd'
import { api } from '../api/client'
import type { AnalysisJob, ExecutionPreview, ResultFile, TaskStatus, TaskSummary } from '../api/types'
import type { ResearchMaterial } from '../api/research'
import { ExperimentMonitorProvider } from '../monitoring/ExperimentMonitor'
import { useExperimentMonitor, useOptionalExperimentMonitor } from '../monitoring/useExperimentMonitor'
import { RecoveryNotice } from '../components/RecoveryNotice'
import { TaskBoard } from '../components/TaskBoard'
import { TaskLog } from '../components/TaskLog'
import './RunDashboard.css'

interface Props { experimentId: string; onPrepared?: (preview: ExecutionPreview) => void; onPlan?: () => void; onResearch?: (material: Extract<ResearchMaterial, { kind: 'experiment' }>) => void }
const order: TaskStatus[] = ['pending', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted']
const shortLabels: Record<TaskStatus, string> = { pending: '等待', running: '运行', succeeded: '成功', failed: '失败', cancelled: '取消', interrupted: '中断' }

export function RunDashboard({ experimentId, onPrepared, onPlan, onResearch }: Props) {
  const shared = useOptionalExperimentMonitor()
  return shared ? <MonitoredRunDashboard key={experimentId} experimentId={experimentId} onPrepared={onPrepared} onPlan={onPlan} onResearch={onResearch} /> : <ExperimentMonitorProvider experimentId={experimentId}><MonitoredRunDashboard experimentId={experimentId} onPrepared={onPrepared} onPlan={onPlan} onResearch={onResearch} /></ExperimentMonitorProvider>
}

function AnalysisRetry({ taskId, attemptId, eligible, onQueued }: { taskId: string; attemptId: string; eligible: boolean; onQueued: () => void }) {
  const [jobs, setJobs] = useState<AnalysisJob[] | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [notice, setNotice] = useState<{ text: string; type: 'info' | 'error' } | null>(null)
  const [retrying, setRetrying] = useState(false)
  const busy = useRef(false), owner = useRef<object | null>(null)
  useEffect(() => { owner.current = {}; return () => { owner.current = null } }, [])
  useEffect(() => {
    if (!eligible) return
    let disposed = false
    api.listAnalysisJobs(taskId).then(next => { if (!disposed) { setJobs(next); setLoadFailed(false) } })
      .catch(() => { if (!disposed) { setJobs(null); setLoadFailed(true) } })
    return () => { disposed = true }
  }, [taskId, eligible])
  const matching = jobs?.filter(job => job.task_id === taskId && job.attempt_id === attemptId)
  const failedJob = matching?.find(job => job.status === 'failed')
  const queued = matching?.some(job => job.status === 'pending' || job.status === 'running')

  async function retryAnalysis() {
    if (!eligible || !failedJob || busy.current || !owner.current) return
    const token = owner.current
    busy.current = true; setRetrying(true); setNotice(null)
    try {
      const next = await api.retryAnalysisJob(taskId, failedJob.id)
      if (owner.current !== token) return
      setJobs(current => current?.map(job => job.id === failedJob.id ? next : job) ?? [next])
      setNotice({ text: '已提交后处理重试，解算结果保留。', type: 'info' })
      onQueued()
    } catch (caught) {
      if (owner.current === token) setNotice({ text: caught instanceof Error ? caught.message : '无法提交后处理重试，请稍后再试。', type: 'error' })
    } finally {
      if (owner.current === token) { busy.current = false; setRetrying(false) }
    }
  }

  const message = loadFailed ? '暂时无法读取后处理作业，请重新选择任务。'
    : jobs === null ? '正在读取后处理作业…'
      : queued ? '后处理已排队或正在执行。'
        : failedJob ? '后处理分析失败，解算结果已保留。' : '该历史 attempt 暂无可重试的后处理作业。'
  return <>
    {notice && <Alert className="run-notice" role="status" type={notice.type} title={notice.text} />}
    {eligible && <Alert className="run-notice" role="status" type="warning" title={message} action={failedJob && !queued && <Button aria-label="重试后处理" loading={retrying} disabled={retrying} onClick={() => void retryAnalysis()}>重试后处理</Button>} />}
  </>
}

function MonitoredRunDashboard({ experimentId, onPrepared, onPlan, onResearch }: Props) {
  const monitor = useExperimentMonitor(), tasks = monitor.tasks
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const selected = tasks.find(task => task.id === selectedTaskId) ?? null
  const [manualAttemptId, setManualAttemptId] = useState<string | null>(null)
  const attemptId = manualAttemptId ?? selected?.active_attempt_id ?? selected?.attempts.at(-1)?.id ?? null
  const attemptKey = JSON.stringify([selectedTaskId, attemptId])
  const [loadedLog, setLog] = useState<{ key: string; value: string } | null>(null)
  const [loadedResults, setResults] = useState<{ key: string; value: ResultFile[] } | null>(null)
  const log = loadedLog?.key === attemptKey ? loadedLog.value : ''
  const results = loadedResults?.key === attemptKey ? loadedResults.value : []
  const [confirmStop, setConfirmStop] = useState(false); const [notice, setNotice] = useState<string | null>(null)
  const [preparing, setPreparing] = useState(false)
  const owner = useRef<object | null>(null)
  useEffect(() => { owner.current = {}; return () => { owner.current = null } }, [experimentId])

  const counts = useMemo(() => Object.fromEntries(order.map((status) => [status, tasks.filter((task) => task.status === status).length])) as Record<TaskStatus, number>, [tasks])

  const selectedAttempt = selected?.attempts.find(attempt => attempt.id === attemptId)
  const attemptStatus = selectedAttempt?.status
  const artifactVersion = selectedAttempt?.artifact_version ?? (attemptId === selected?.active_attempt_id ? selected?.artifact_version : 0)
  const analysisStatus = selectedAttempt?.analysis_status ?? (attemptId === selected?.active_attempt_id ? selected?.analysis_status : 'unknown')
  useEffect(() => {
    if (!selectedTaskId || !attemptId) return
    let disposed = false, refreshing = false
    async function refresh() {
      if (refreshing) return
      refreshing = true
      try {
        const nextLog = await api.getTaskLog(selectedTaskId!, attemptId!)
        if (!disposed) setLog({ key: attemptKey, value: nextLog })
      } catch { if (!disposed) setNotice('暂时无法刷新日志，请检查服务或重新选择任务。') }
      finally { refreshing = false }
    }
    void refresh()
    const timer = attemptStatus === 'running' ? window.setInterval(() => void refresh(), 2500) : undefined
    return () => { disposed = true; if (timer !== undefined) window.clearInterval(timer) }
  }, [selectedTaskId, attemptId, attemptStatus, attemptKey])
  useEffect(() => {
    if (!selectedTaskId || !attemptId) return
    let disposed = false
    api.getTaskResults(selectedTaskId, attemptId).then(next => { if (!disposed) setResults({ key: attemptKey, value: next }) }).catch(() => { if (!disposed) setNotice('暂时无法刷新结果文件。') })
    return () => { disposed = true }
  }, [selectedTaskId, attemptId, attemptStatus, artifactVersion, analysisStatus, attemptKey])
  function loadAttempt(id: string) { setManualAttemptId(id); setLog(null); setResults(null) }
  function selectTask(task: TaskSummary) { setSelectedTaskId(task.id); setManualAttemptId(null); setLog(null); setResults(null) }
  async function retry(task: TaskSummary) {
    if (preparing) return
    const token = owner.current
    setPreparing(true)
    try {
      const experiment = await api.getExperiment(experimentId)
      if (owner.current !== token) return
      const preview = await api.prepareExecution(experimentId, { expected_revision: experiment.current_revision, task_id: task.id })
      if (owner.current !== token) return
      if (onPrepared) onPrepared(preview)
      else window.location.assign(`?experiment=${experimentId}&view=review&preview=${preview.id}`)
    } catch (caught) { if (owner.current === token) setNotice(caught instanceof Error ? caught.message : '无法准备重试，请重新检查输入。') }
    finally { if (owner.current === token) setPreparing(false) }
  }
  async function stop() {
    try { await api.stopExperiment(experimentId); if (!owner.current) return; setConfirmStop(false); setNotice('已发送停止请求，等待 Worker 确认进程退出。'); monitor.refresh() }
    catch { if (owner.current) setNotice('停止请求未完成，请检查服务；不要假定任务已经停止。') }
  }

  return <main className="run-shell">
    <header className="run-header"><div><div><h1>运行仪表盘</h1><p>实时任务状态与归档结果 · 查看历史不会重新执行</p></div></div><div>{onResearch && <Button disabled={!selected || !selectedAttempt} onClick={() => { if (selected && selectedAttempt) onResearch({ kind: 'experiment', experiment_id: experimentId, task_id: selected.id, attempt_id: selectedAttempt.id, role: 'baseline' }) }}>带入科研分析</Button>}{onPlan && <Button onClick={onPlan}>返回查看计划</Button>}<Button danger onClick={() => setConfirmStop(true)}>停止实验</Button></div></header>
    {notice && <Alert className="run-notice" type="info" role="status" title={notice} />}
    {monitor.error && <Alert type={monitor.connection === 'stopped' ? 'error' : 'warning'} role="alert" title={monitor.error} />}
    {preparing && <Alert className="run-notice" type="info" role="status" title="正在生成下一次尝试的 XML，尚未提交重试。" />}
    <section className="progress-strip" aria-label="任务进度">{order.map((status) => <div key={status}><strong>{counts[status]}</strong><span>{shortLabels[status]}</span></div>)}</section>
    {selected && <RecoveryNotice key={`${selected.id}:${selected.active_attempt_id}:${selected.recovery.version}`} task={selected} />}
    {selected && attemptId && <AnalysisRetry key={JSON.stringify([experimentId, selected.id, attemptId])} taskId={selected.id} attemptId={attemptId} eligible={attemptStatus === 'succeeded' && analysisStatus === 'failed'} onQueued={monitor.refresh} />}
    <div className="run-grid"><TaskBoard tasks={tasks} selectedId={selected?.id ?? null} onSelect={selectTask} onRetry={retry} /><TaskLog detail={selected} attemptId={attemptId} log={log} results={results} onAttempt={loadAttempt} /></div>
    <Modal title="确认停止实验" open={confirmStop} onCancel={() => setConfirmStop(false)} onOk={() => void stop()} okText="确认停止" cancelText="继续运行" okButtonProps={{ danger: true }}><p>运行中的 GREAT_PVT 进程将收到终止信号，已完成结果会保留。</p></Modal>
  </main>
}
