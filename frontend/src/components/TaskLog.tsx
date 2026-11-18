import { Alert, Button, Empty, Select, Table } from 'antd'
import { useId } from 'react'
import type { ResultFile, TaskDetail } from '../api/types'
import { analysisLabels } from '../api/monitor'

interface Props { detail: TaskDetail | null; attemptId: string | null; log: string; results: ResultFile[]; onAttempt: (id: string) => void }
function size(bytes: number) { return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB` }

export function TaskLog({ detail, attemptId, log, results, onAttempt }: Props) {
  const attemptSelectId = useId()
  if (!detail) return <section className="task-detail empty"><Empty description={<><strong>选择一个日期任务</strong><p>查看 attempt、日志和结果文件。</p></>} /></section>
  const attempt = detail.attempts.find((item) => item.id === attemptId)
  return <section className="task-detail" aria-labelledby="task-detail-heading">
    <header><div><h2 id="task-detail-heading">{detail.year}-{detail.doy}</h2><p>任务详情</p></div><label htmlFor={attemptSelectId}>Attempt<Select id={attemptSelectId} aria-label="Attempt" value={attemptId ?? undefined} onChange={onAttempt} virtual={false} options={detail.attempts.map((item) => ({ value: item.id, label: `#${item.number} · ${item.status}` }))} /></label></header>
    {attempt?.error_code && <Alert className="attempt-error" type="error" title={attempt.error_code} description={`退出码 ${attempt.exit_code ?? '—'}`} />}
    {attempt && <p role="status">后处理：{analysisLabels[attempt.analysis_status ?? 'unknown']}</p>}
    <div className="log-block"><h3>运行日志</h3><pre>{log || '暂无日志'}</pre></div>
    <div className="results-block"><h3>结果文件</h3><Table<ResultFile> size="small" pagination={false} rowKey={(item) => `${item.station}-${item.filename}`} dataSource={results} locale={{ emptyText: '该 attempt 没有结果文件' }} columns={[
      { title: '文件', dataIndex: 'filename', render: (filename: string) => <strong>{filename}</strong> },
      { title: '测站 / 大小', key: 'metadata', render: (_, item) => `${item.station ?? '全局'} · ${size(item.size_bytes)}` },
      { title: '操作', key: 'download', render: (_, item) => <Button type="link" size="small" href={item.url} target="_blank" rel="noreferrer">打开 / 下载</Button> },
    ]} /></div>
  </section>
}
