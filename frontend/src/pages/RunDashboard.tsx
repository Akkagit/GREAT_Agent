import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api/client'
import type { ResultFile, TaskDetail, TaskEvent, TaskStatus, TaskSummary } from '../api/types'
import { TaskBoard } from '../components/TaskBoard'
import { TaskLog } from '../components/TaskLog'
import './RunDashboard.css'

interface Props { experimentId: string }
const order: TaskStatus[] = ['pending', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted']
const shortLabels: Record<TaskStatus, string> = { pending: '等待', running: '运行', succeeded: '成功', failed: '失败', cancelled: '取消', interrupted: '中断' }

export function RunDashboard({ experimentId }: Props) {
  const [tasks, setTasks] = useState<TaskSummary[]>([]); const [selected, setSelected] = useState<TaskDetail | null>(null)
  const [attemptId, setAttemptId] = useState<string | null>(null); const [log, setLog] = useState(''); const [results, setResults] = useState<ResultFile[]>([])
  const [confirmStop, setConfirmStop] = useState(false); const [notice, setNotice] = useState<string | null>(null)
  const seen = useRef(new Set<string>()); const lastIds = useRef(new Map<string, string>())

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect -- synchronize the dashboard with persisted tasks
    api.listTasks(experimentId).then(setTasks).catch(() => setNotice('无法读取任务列表。'))
  }, [experimentId])

  const taskIds = tasks.map((task) => task.id).join(',')
  useEffect(() => {
    const cleanups = new Map<string, () => void>(); const timers = new Set<number>(); let closed = false
    const connect = (taskId: string) => {
      const onEvent = (event: TaskEvent) => {
        const key = `${taskId}:${event.id}`; if (seen.current.has(key)) return
        seen.current.add(key); lastIds.current.set(taskId, event.id)
        if (event.event === 'task_status' && event.data.status) setTasks((current) => current.map((task) => task.id === taskId ? { ...task, status: event.data.status as TaskStatus } : task))
      }
      const onDisconnect = () => {
        if (closed) return
        const timer = window.setTimeout(() => { timers.delete(timer); if (!closed) cleanups.set(taskId, api.openTaskEvents(taskId, lastIds.current.get(taskId), onEvent, onDisconnect)) }, 0)
        timers.add(timer)
      }
      cleanups.set(taskId, api.openTaskEvents(taskId, lastIds.current.get(taskId), onEvent, onDisconnect))
    }
    for (const id of taskIds.split(',').filter(Boolean)) connect(id)
    return () => { closed = true; cleanups.forEach((cleanup) => cleanup()); timers.forEach(clearTimeout) }
  }, [taskIds])

  const counts = useMemo(() => Object.fromEntries(order.map((status) => [status, tasks.filter((task) => task.status === status).length])) as Record<TaskStatus, number>, [tasks])

  async function loadAttempt(taskId: string, id: string) {
    setAttemptId(id)
    const [nextLog, nextResults] = await Promise.all([api.getTaskLog(taskId, id), api.getTaskResults(taskId, id)])
    setLog(nextLog); setResults(nextResults)
  }
  async function selectTask(task: TaskSummary) {
    const detail = await api.getTask(task.id); setSelected(detail)
    const nextAttempt = detail.active_attempt_id ?? detail.attempts.at(-1)?.id ?? null
    if (nextAttempt) await loadAttempt(task.id, nextAttempt); else { setAttemptId(null); setLog(''); setResults([]) }
  }
  async function retry(task: TaskSummary) {
    await api.retryTask(task.id); setTasks((current) => current.map((item) => item.id === task.id ? { ...item, status: 'pending' } : item)); setNotice(`${task.year}-${task.doy} 已回到等待队列。`)
  }
  async function stop() { await api.stopExperiment(experimentId); setConfirmStop(false); setNotice('已发送停止请求，运行中的任务将安全终止。') }

  return <main className="run-shell">
    <header className="run-header"><div><span className="brand-symbol" aria-hidden="true">G</span><div><h1>运行仪表盘</h1><p>实验 {experimentId}</p></div></div><button className="stop-button" onClick={() => setConfirmStop(true)}>停止实验</button></header>
    {notice && <div className="run-notice" role="status">{notice}</div>}
    <section className="progress-strip" aria-label="任务进度">{order.map((status) => <div key={status}><strong>{counts[status]}</strong><span>{shortLabels[status]}</span></div>)}</section>
    <div className="run-grid"><TaskBoard tasks={tasks} selectedId={selected?.id ?? null} onSelect={selectTask} onRetry={retry} /><TaskLog detail={selected} attemptId={attemptId} log={log} results={results} onAttempt={(id) => selected && void loadAttempt(selected.id, id)} /></div>
    {confirmStop && <div className="dialog-backdrop"><section className="stop-dialog" role="dialog" aria-modal="true" aria-label="确认停止实验"><h2>确认停止实验</h2><p>运行中的 GREAT_PVT 进程将收到终止信号，已完成结果会保留。</p><div><button className="text-button" onClick={() => setConfirmStop(false)}>继续运行</button><button className="stop-button" onClick={stop}>确认停止</button></div></section></div>}
  </main>
}
