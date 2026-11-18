import { useEffect, useState } from 'react'
import { Alert, Button, Select, Spin } from 'antd'
import { api } from '../api/client'
import type { ResultFile } from '../api/types'
import type { ResearchMaterial } from '../api/research'
import { analysisLabels } from '../api/monitor'
import { ExperimentMonitorProvider } from '../monitoring/ExperimentMonitor'
import { useExperimentMonitor, useOptionalExperimentMonitor } from '../monitoring/useExperimentMonitor'
import { XmlCode } from './XmlCode'

const summaryPlots = [
  { type: 'enu', label: '全局 ENU' },
  { type: 'convergence', label: '收敛时间' },
  { type: 'fix_rate', label: '固定率' },
] as const

function plotLabel(file: ResultFile) {
  return summaryPlots.find(plot => plot.type === file.plot_type)?.label
}

interface Props { experimentId: string; mode: 'images' | 'xml'; onResearch?: (material: Extract<ResearchMaterial, { kind: 'experiment' }>) => void }
export function ArtifactBrowser({ experimentId, mode, onResearch }: Props) {
  const shared = useOptionalExperimentMonitor()
  return shared ? <MonitoredArtifactBrowser key={experimentId} experimentId={experimentId} mode={mode} onResearch={onResearch} /> : <ExperimentMonitorProvider experimentId={experimentId}><MonitoredArtifactBrowser experimentId={experimentId} mode={mode} onResearch={onResearch} /></ExperimentMonitorProvider>
}
function MonitoredArtifactBrowser({ experimentId, mode, onResearch }: Props) {
  const monitor = useExperimentMonitor(), tasks = monitor.tasks
  const [chosenTaskId, setTaskId] = useState('')
  const detail = tasks.find(task => task.id === chosenTaskId) ?? tasks[0] ?? null
  const taskId = detail?.id ?? ''
  const [chosenAttemptId, setAttemptId] = useState('')
  const attemptId = chosenAttemptId || detail?.active_attempt_id || detail?.attempts.at(-1)?.id || ''
  const selectedAttempt = detail?.attempts.find(attempt => attempt.id === attemptId)
  const artifactVersion = selectedAttempt?.artifact_version ?? (detail?.active_attempt_id === attemptId ? detail?.artifact_version : 0) ?? 0
  const analysisStatus = selectedAttempt?.analysis_status ?? (detail?.active_attempt_id === attemptId ? detail?.analysis_status : 'unknown') ?? 'unknown'
  const loadKey = JSON.stringify([taskId, attemptId, mode])
  const [loadedKey, setLoadedKey] = useState<string | null>(null)
  const [loadedImages, setImages] = useState<ResultFile[]>([])
  const [imageUrl, setImageUrl] = useState('')
  const [loadedXml, setXml] = useState<{ xml_path: string; xml_content: string } | null>(null)
  const images = loadedKey === loadKey ? loadedImages : []
  const xml = loadedKey === loadKey ? loadedXml : null
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [imageError, setImageError] = useState(false)
  useEffect(() => {
    if (!taskId || !attemptId) return
    let disposed = false
    const load = mode === 'images' ? api.getTaskResults(taskId, attemptId) : api.getTaskXml(taskId, attemptId)
    load.then(value => {
      if (disposed) return
      if (mode === 'images') {
        const files = summaryPlots.flatMap(plot => {
          const file = (value as ResultFile[]).find(file => file.plot_type === plot.type)
          return file ? [file] : []
        })
        setImages(files); setImageUrl(current => files.some(file => file.url === current) ? current : files[0]?.url ?? ''); setImageError(false)
      } else setXml(value as { xml_path: string; xml_content: string })
      setLoadedKey(loadKey); setError(null); setLoading(false)
    }).catch(() => { if (!disposed) { setLoadedKey(loadKey); setImages([]); setXml(null); setError(mode === 'xml' ? '该 attempt 没有可读取的归档 XML，或文件已不可用。' : '无法读取结果图片，请重选任务或检查本地服务。'); setLoading(false) } })
    return () => { disposed = true }
  }, [taskId, attemptId, mode, artifactVersion, analysisStatus, loadKey])
  function selectTask(id: string) { setTaskId(id); setAttemptId(''); setImages([]); setImageUrl(''); setXml(null); setError(null); setImageError(false); setLoading(true) }
  function selectAttempt(id: string) { setAttemptId(id); setImages([]); setImageUrl(''); setXml(null); setError(null); setImageError(false); setLoading(true) }
  const selected = images.find(file => file.url === imageUrl)
  const [path, hash] = imageUrl.split('#')
  const versionedUrl = imageUrl && artifactVersion > 0 ? `${path}${path.includes('?') ? '&' : '?'}artifact_version=${artifactVersion}${hash ? `#${hash}` : ''}` : imageUrl
  const isLoading = !monitor.snapshot || (!!attemptId && (loading || loadedKey !== loadKey))
  const currentError = loadedKey === loadKey ? error : null
  return <section className="artifact-browser"><header><h2>{mode === 'images' ? '结果图片预览' : '历史 XML · 只读'}</h2><p>只读取所选实验与 attempt 的真实文件，不生成新结果，不重新执行。</p></header>
    {tasks.length > 0 && <div className="artifact-selectors"><label>日期任务<Select aria-label="日期任务" value={taskId} onChange={selectTask} virtual={false} options={tasks.map(task => ({ value: task.id, label: `${task.year} / DOY ${task.doy} · ${task.status}` }))} /></label><label>结果 Attempt<Select aria-label="结果 Attempt" value={attemptId || undefined} onChange={selectAttempt} virtual={false} options={detail?.attempts.map(attempt => ({ value: attempt.id, label: `#${attempt.number} · ${attempt.status}` }))} /></label></div>}
    {onResearch && mode === 'images' && <Button disabled={!taskId || !selectedAttempt} onClick={() => { if (taskId && selectedAttempt) onResearch({ kind: 'experiment', experiment_id: experimentId, task_id: taskId, attempt_id: selectedAttempt.id, role: 'baseline' }) }}>带入科研分析</Button>}
    {currentError && <Alert type="error" title={currentError} role="alert" />}
    {monitor.error && <Alert type="warning" role="alert" title={monitor.error} />}
    {mode === 'images' && detail && <p role="status">后处理：{analysisLabels[analysisStatus]}</p>}
    {isLoading && <div className="artifact-empty" role="status"><Spin size="small" /> 正在读取真实产物…</div>}
    {!isLoading && !currentError && mode === 'images' && (images.length ? <><label className="artifact-file-label">图片文件<Select aria-label="图片文件" value={imageUrl} onChange={value => { setImageUrl(value); setImageError(false) }} virtual={false} options={images.map(file => ({ value: file.url, label: plotLabel(file) }))} /></label><figure className="artifact-figure">{imageError ? <Alert type="error" title="图片加载失败，可重选图片或打开原文件检查。" role="alert" /> : <img key={versionedUrl} src={versionedUrl} alt={selected ? plotLabel(selected) : '结果图片'} onError={() => setImageError(true)} />}<figcaption>{selected && plotLabel(selected)} · {selected?.size_bytes.toLocaleString()} B · <Button type="link" size="small" href={versionedUrl} target="_blank" rel="noreferrer">打开原图</Button></figcaption></figure></> : <p className="artifact-empty">尚无可预览的图片。请先完成解算及自动绘图，或选择有结果的 attempt。</p>)}
    {!isLoading && !currentError && mode === 'xml' && (xml ? <><p className="archived-xml-path">{xml.xml_path}</p><XmlCode content={xml.xml_content} /><p className="artifact-empty">历史配置仅供查阅；如需再次解算，请新建实验或从失败任务复核重试。</p></> : <p className="artifact-empty">尚无归档 XML，请先检查输入并生成执行预览。</p>)}
  </section>
}
