import { useMemo, useState } from 'react'
import type { TaskStatus, TaskSummary } from '../api/types'

const labels: Record<TaskStatus, string> = { pending: '等待', running: '运行中', succeeded: '成功', failed: '失败', cancelled: '已取消', interrupted: '已中断' }

interface Props { tasks: TaskSummary[]; selectedId: string | null; onSelect: (task: TaskSummary) => void; onRetry: (task: TaskSummary) => void }

export function TaskBoard({ tasks, selectedId, onSelect, onRetry }: Props) {
  const [status, setStatus] = useState('all'); const [query, setQuery] = useState('')
  const visible = useMemo(() => tasks.filter((task) => (status === 'all' || task.status === status) && `${task.year}-${task.doy}`.includes(query.trim())), [tasks, status, query])
  return <section className="task-board" aria-labelledby="task-board-heading">
    <header><div><h2 id="task-board-heading">每日任务</h2><p>{visible.length} / {tasks.length} 个日期</p></div>
      <div className="task-filters"><label>日期/DOY<input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="2024-350" /></label><label>状态<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="all">全部</option>{Object.entries(labels).map(([value,label]) => <option value={value} key={value}>{label}</option>)}</select></label></div>
    </header>
    <div className="task-list">{visible.map((task) => <article className={task.id === selectedId ? 'task-row selected' : 'task-row'} key={task.id}>
      <button className="task-main" aria-label={`查看 ${task.year}-${task.doy}`} onClick={() => onSelect(task)}><strong>{task.year}-{task.doy}</strong><span className={`status ${task.status}`}>{labels[task.status]}</span></button>
      {(task.status === 'failed' || task.status === 'interrupted') && <button className="retry-button" aria-label={`重试 ${task.year}-${task.doy}`} onClick={() => onRetry(task)}>重试</button>}
    </article>)}</div>
  </section>
}
