import { useId, useMemo, useState } from 'react'
import { Button, Input, Select, Tag } from 'antd'
import type { MonitorTask, TaskStatus, TaskSummary } from '../api/types'

const labels: Record<TaskStatus, string> = { pending: '等待', running: '运行中', succeeded: '成功', failed: '失败', cancelled: '已取消', interrupted: '已中断' }
const colors: Record<TaskStatus, string> = { pending: 'default', running: 'processing', succeeded: 'success', failed: 'error', cancelled: 'default', interrupted: 'warning' }

interface Props { tasks: Array<TaskSummary & Partial<Pick<MonitorTask, 'recovery'>>>; selectedId: string | null; onSelect: (task: TaskSummary) => void; onRetry: (task: TaskSummary) => void }

export function TaskBoard({ tasks, selectedId, onSelect, onRetry }: Props) {
  const statusId = useId()
  const [status, setStatus] = useState('all'); const [query, setQuery] = useState('')
  const visible = useMemo(() => tasks.filter((task) => (status === 'all' || task.status === status) && `${task.year}-${task.doy}`.includes(query.trim())), [tasks, status, query])
  return <section className="task-board" aria-labelledby="task-board-heading">
    <header><div><h2 id="task-board-heading">每日任务</h2><p>{visible.length} / {tasks.length} 个日期</p></div>
      <div className="task-filters"><label>日期/DOY<Input aria-label="日期/DOY" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="2024-350" /></label><label htmlFor={statusId}>状态<Select id={statusId} aria-label="状态" value={status} onChange={setStatus} virtual={false} options={[{ value: 'all', label: '全部' }, ...Object.entries(labels).map(([value, label]) => ({ value, label }))]} /></label></div>
    </header>
    <div className="task-list">{visible.map((task) => <article className={task.id === selectedId ? 'task-row selected' : 'task-row'} key={task.id}>
      <Button type="text" className="task-main" aria-label={`查看 ${task.year}-${task.doy}`} onClick={() => onSelect(task)}><strong>{task.year}-{task.doy}</strong><Tag color={colors[task.status]}>{labels[task.status]}</Tag></Button>
      {(task.recovery?.state === 'observing' || task.recovery?.state === 'blocked') && <span title={task.recovery.reason ?? undefined}>{task.recovery.state === 'observing' ? '观察遗留进程' : '恢复受阻'}{task.recovery.reason ? ` · ${task.recovery.reason}` : ''}</span>}
      {(task.status === 'failed' || task.status === 'interrupted') && <Button className="retry-button" aria-label={`重试 ${task.year}-${task.doy}`} onClick={() => onRetry(task)}>重试</Button>}
    </article>)}</div>
  </section>
}
