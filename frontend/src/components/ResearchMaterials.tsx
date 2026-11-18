import { useState } from 'react'
import { Button, Checkbox, Input, Select, Tag } from 'antd'
import type { ResearchCatalog, ResearchMaterial, ResearchRun } from '../api/research'
import { researchMaterialKey } from '../api/research'

function materialLabel(material: ResearchMaterial, catalog: ResearchCatalog | null): string {
  if (material.kind === 'experiment') {
    const experiment = catalog?.experiments.find(row => row.id === material.experiment_id)
    const task = experiment?.tasks.find(row => row.id === material.task_id)
    const attempt = task?.attempts.find(row => row.id === material.attempt_id)
    return `${experiment?.title ?? material.experiment_id} · ${task ? `${task.year}/${task.doy}` : material.task_id} · ${attempt ? `#${attempt.number}` : material.attempt_id}`
  }
  if (material.kind === 'code') return `${catalog?.sources.find(row => row.id === material.source_id)?.name ?? material.source_id} · ${material.paths.join('、')}${material.snapshot_run_id ? ` · 历史轮次 ${material.snapshot_run_id}` : ' · 当前源码'}`
  return `${catalog?.documents.find(row => row.id === material.document_id)?.title ?? material.document_id} · 版本 ${material.version_id}`
}
export function ResearchMaterials({ catalog, value, onChange, historyRuns = [] }: { catalog: ResearchCatalog | null; value: ResearchMaterial[]; onChange: (value: ResearchMaterial[]) => void; historyRuns?: ResearchRun[] }) {
  const [source, setSource] = useState(''), [paths, setPaths] = useState('')
  function toggle(material: ResearchMaterial, selected: boolean) {
    const key = researchMaterialKey(material)
    onChange(selected ? [...value, material] : value.filter(row => researchMaterialKey(row) !== key))
  }
  function addCode() {
    const selectedSource = source || catalog?.sources[0]?.id
    const selectedPaths = paths.split(/[\n,]/).map(path => path.trim()).filter(Boolean)
    if (!selectedSource || !selectedPaths.length) return
    onChange([...value, { kind: 'code', source_id: selectedSource, paths: selectedPaths, provenance: 'unknown' }]); setPaths('')
  }
  return <section className="research-materials" aria-label="材料选择">
    <h3>本课题材料 <Tag>{value.length}</Tag></h3>
    <p className="research-hint">更新只影响后续轮次。历史回答使用各自的材料快照。</p>
    <div className="research-selected-materials">{value.map((material, index) => <article key={`${index}-${JSON.stringify(material)}`}>
      <strong>{materialLabel(material, catalog)}</strong>
      {material.kind === 'experiment' && <><small>task {material.task_id} · attempt {material.attempt_id}</small><Select aria-label={`实验角色 ${material.attempt_id}`} size="small" virtual={false} value={material.role} onChange={role => onChange(value.map((row, i) => i === index ? { ...material, role } : row))} options={[{ value: 'baseline', label: '基准' }, { value: 'comparison', label: '对照' }]} /></>}
      {material.kind === 'code' && <small>运行版本关联：{material.provenance === 'unknown' ? '未知' : material.provenance}</small>}
      <Button type="text" size="small" aria-label={`移除材料 ${index + 1}`} onClick={() => onChange(value.filter((_, i) => i !== index))}>移除</Button>
    </article>)}</div>
    <details open><summary>添加实验 attempt</summary><div className="research-material-options">{catalog?.experiments.flatMap(experiment => experiment.tasks.flatMap(task => task.attempts.map(attempt => {
      const existing = value.find(material => material.kind === 'experiment' && material.attempt_id === attempt.id)
      const material: ResearchMaterial = { kind: 'experiment', experiment_id: experiment.id, task_id: task.id, attempt_id: attempt.id, role: value.some(row => row.kind === 'experiment') ? 'comparison' : 'baseline' }
      return <Checkbox key={attempt.id} checked={!!existing} onChange={event => existing ? onChange(value.filter(row => row !== existing)) : toggle(material, event.target.checked)}>{experiment.title} · {task.year}/{task.doy} · #{attempt.number}</Checkbox>
    }))) ?? null}{catalog && !catalog.experiments.length && <p className="research-hint">暂无实验材料</p>}</div></details>
    <details open><summary>添加代码范围</summary><div className="research-material-options"><label>注册源码<Select aria-label="代码来源" virtual={false} value={source || catalog?.sources[0]?.id} onChange={setSource} options={catalog?.sources.map(row => ({ value: row.id, label: row.name }))} /></label><Input.TextArea aria-label="代码路径" value={paths} rows={2} onChange={event => setPaths(event.target.value)} placeholder="源内相对路径，每行一个" /><Button size="small" disabled={!paths.trim() || !catalog?.sources.length} onClick={addCode}>添加代码范围</Button><p className="research-hint">当前源码与历史运行程序的关联默认为未知。</p></div></details>
    <details open><summary>添加文献版本</summary><div className="research-material-options">{catalog?.documents.map(document => {
      const material: ResearchMaterial = { kind: 'literature', document_id: document.id, version_id: document.version_id }
      const selected = value.some(row => row.kind === 'literature' && row.document_id === document.id && row.version_id === document.version_id)
      const older = value.some(row => row.kind === 'literature' && row.document_id === document.id && row.version_id !== document.version_id)
      return <div key={`${document.id}-${document.version_id}`}><Checkbox checked={selected} onChange={event => toggle(material, event.target.checked)}>{document.title} · 版本 {document.version_id}</Checkbox>{older && <p className="research-hint">有新版本可加入下一轮；已选历史版本保留。</p>}</div>
    })}{catalog && !catalog.documents.length && <p className="research-hint">暂无可用文献版本</p>}</div></details>
    {!!historyRuns.length && <details><summary>添加历史材料版本</summary><div className="research-material-options"><p className="research-hint">从已保存轮次选择历史快照，可与当前代码或文献并列比较。</p>{historyRuns.flatMap(run => (run.snapshot_summary ?? []).flatMap(summary => {
      const locator = summary.locator && typeof summary.locator === 'object' ? summary.locator as Record<string, unknown> : {}
      const materialId = typeof summary.id === 'string' ? summary.id : typeof summary.material_id === 'string' ? summary.material_id : ''
      let material: ResearchMaterial
      let label: string
      if (summary.kind === 'code' && typeof locator.source_id === 'string' && typeof locator.path === 'string' && materialId) {
        material = { kind: 'code', source_id: locator.source_id, paths: [locator.path], provenance: locator.provenance === 'user_declared' ? 'user_declared' : 'unknown', snapshot_run_id: run.id, snapshot_material_id: materialId }
        label = `添加历史代码 ${run.id} ${locator.path}`
      } else if (summary.kind === 'literature' && typeof locator.document_id === 'string' && typeof locator.version_id === 'string') {
        material = { kind: 'literature', document_id: locator.document_id, version_id: locator.version_id }
        label = `添加历史文献 ${run.id} ${String(summary.title ?? locator.document_id)}`
      } else return []
      const selected = value.some(row => material.kind === 'code' && row.kind === 'code' ? row.source_id === material.source_id && row.snapshot_run_id === material.snapshot_run_id && row.snapshot_material_id === material.snapshot_material_id : material.kind === 'literature' && row.kind === 'literature' && row.document_id === material.document_id && row.version_id === material.version_id)
      return [<article className="research-historical-material" key={`${run.id}-${materialId}`}><strong>{String(summary.title ?? materialId)}</strong><small>轮次 {run.id}{typeof summary.sha256 === 'string' ? ` · ${summary.sha256.slice(0, 12)}` : ''}</small><Button size="small" aria-label={label} disabled={selected} onClick={() => onChange([...value, material])}>{selected ? '已选择此版本' : '添加此版本'}</Button></article>]
    }))}</div></details>}
  </section>
}
