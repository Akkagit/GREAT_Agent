import { useEffect, useState } from 'react'
import { Alert, Button, Tag } from 'antd'
import { researchApi, type ResearchEvidence } from '../api/research'
import { knowledgeApi } from '../api/knowledge'

export interface ResearchEvidenceSelection { sessionId: string; runId: string; alias: string }
const labels: Record<string, string> = { experiment_id: '实验', task_id: '日期任务', attempt_id: 'Attempt', path: '源码路径', source_id: '代码来源', version_id: '文献版本', document_id: '文献', page_index: 'PDF 页序', page_label: '印刷页码', line_start: '起始行', line_end: '结束行', start_line: '起始行', end_line: '结束行', unit: '单位', policy: '统计口径', reference: '参照', provenance: '运行版本关联', commit: '代码提交', build_options: '构建选项', dependencies: '依赖', chunk_id: '文献切片', section: '章节', json_pointers: '原始指标位置', algorithm_version: '计算方法版本' }
const kinds: Record<string, string> = { experiment: '实验指标', artifact: '实验产物', metric: '实验指标', metrics: '实验指标', code: '代码快照', log: '日志片段', literature: '文献摘录', derived: '派生指标' }
const qualities: Record<string, string> = { code_execution_link_unknown: '源码与历史运行程序的关联未知', code_execution_link_user_declared: '运行版本关联来自用户声明', static_call_analysis_incomplete: '静态调用分析覆盖不完整', partial_snapshot: '日志局部快照', formula_unverified: '公式尚未核验', formulas_unverified: '公式尚未核验', table_unverified: '表格尚未核验', tables_unverified: '表格尚未核验', unapplied_suggestion_only: '建议差异尚未应用' }
export function ResearchEvidencePanel({ selection, summary }: { selection: ResearchEvidenceSelection | null; summary: Record<string, unknown>[] }) {
  const [state, setState] = useState<{ key: string; evidence?: ResearchEvidence; error?: string } | null>(null)
  const key = selection ? `${selection.sessionId}/${selection.runId}/${selection.alias}` : ''
  useEffect(() => {
    if (!selection) return
    let disposed = false
    researchApi.evidence(selection.runId, selection.alias).then(evidence => { if (!disposed) setState({ key, evidence }) }).catch(error => { if (!disposed) setState({ key, error: error instanceof Error ? error.message : '证据无法读取' }) })
    return () => { disposed = true }
  }, [key, selection])
  const evidence = state?.key === key ? state.evidence : undefined
  const error = state?.key === key ? state.error : undefined
  const locator = evidence?.locator ?? {}
  const locations = Array.isArray(locator.locations) ? locator.locations.filter((value): value is { page_index: number; page_label?: string } => value && typeof value === 'object' && Number.isInteger(value.page_index) && value.page_index >= 0) : []
  const pageIndex = typeof locator.page_index === 'number' ? locator.page_index : locations[0]?.page_index
  return <section className="research-evidence"><header><p className="research-eyebrow">SOURCE RECORD</p><h2>证据查看器</h2></header>
    {!selection ? <div className="research-evidence-empty"><h3>从回答打开原始依据</h3><p>指标、代码、日志和文献均保留来源身份与版本。引用编号只在所属轮次内有效。</p><h3>当前材料摘要</h3>{summary.length ? summary.map((material, index) => <pre key={index}>{JSON.stringify(material, null, 2)}</pre>) : <p>尚无材料摘要。可先选择实验、源码或文献。</p>}</div> : <>
      <p className="research-hint">轮次 {selection.runId} · [{selection.alias}]</p>
      {error && <Alert type="error" role="alert" title={error} />}
      {!evidence && !error && <p role="status">正在核对证据…</p>}
      {evidence && <><Tag color="blue">{kinds[evidence.kind] ?? evidence.kind}</Tag><h3>{evidence.title}</h3>
        {!evidence.available && <Alert type="warning" title="该来源当前不可用，请重新核对材料权限与版本。" />}
        <dl className="research-locator">{Object.entries(locator).filter(([name]) => name !== 'locations').map(([name, value]) => <div key={name}><dt>{labels[name] ?? name}</dt><dd>{name === 'page_index' && typeof value === 'number' ? value + 1 : value === 'unknown' ? '未知' : typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl>
        {locations.map((location, index) => <p className="research-hint" key={index}>PDF 第 {location.page_index + 1} 页{location.page_label ? ` · 印刷页码 ${location.page_label}` : ''}</p>)}
        {evidence.quality_flags?.length > 0 && <Alert type="warning" title="来源质量提示" description={evidence.quality_flags.map(flag => qualities[flag] ?? flag).join(' · ')} />}
        {evidence.available && <pre className={`research-evidence-text research-evidence-${evidence.kind}`} aria-label="证据原文">{evidence.text}</pre>}
        <p className="research-hash">SHA256 {evidence.sha256}</p>
        {evidence.available && evidence.kind === 'literature' && typeof locator.document_id === 'string' && <Button href={knowledgeApi.originalUrl(locator.document_id, pageIndex)} target="_blank" rel="noreferrer">打开文献原页</Button>}
      </>}
    </>}
  </section>
}
