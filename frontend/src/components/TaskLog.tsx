import type { ResultFile, TaskDetail } from '../api/types'

interface Props { detail: TaskDetail | null; attemptId: string | null; log: string; results: ResultFile[]; onAttempt: (id: string) => void }
function size(bytes: number) { return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB` }

export function TaskLog({ detail, attemptId, log, results, onAttempt }: Props) {
  if (!detail) return <section className="task-detail empty"><strong>选择一个日期任务</strong><p>查看 attempt、日志和结果文件。</p></section>
  const attempt = detail.attempts.find((item) => item.id === attemptId)
  return <section className="task-detail" aria-labelledby="task-detail-heading">
    <header><div><h2 id="task-detail-heading">{detail.year}-{detail.doy}</h2><p>任务详情</p></div><label>Attempt<select aria-label="Attempt" value={attemptId ?? ''} onChange={(e) => onAttempt(e.target.value)}>{detail.attempts.map((item) => <option value={item.id} key={item.id}>#{item.number} · {item.status}</option>)}</select></label></header>
    {attempt?.error_code && <div className="attempt-error"><strong>{attempt.error_code}</strong><span>退出码 {attempt.exit_code ?? '—'}</span></div>}
    <div className="log-block"><h3>运行日志</h3><pre>{log || '暂无日志'}</pre></div>
    <div className="results-block"><h3>结果文件</h3>{results.length === 0 ? <p className="empty-results">该 attempt 没有结果文件</p> : <ul>{results.map((item) => <li key={`${item.station}-${item.filename}`}><div><strong>{item.filename}</strong><span>{item.station ?? '全局'} · {size(item.size_bytes)}</span></div><a href={item.url} target="_blank" rel="noreferrer">打开 / 下载</a></li>)}</ul>}</div>
  </section>
}
