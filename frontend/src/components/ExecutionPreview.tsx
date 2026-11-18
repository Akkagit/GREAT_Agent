import { useEffect, useState } from 'react'
import type { ExecutionPreview as Preview } from '../api/types'
import { WorkflowSteps } from './WorkflowSteps'
import { XmlCode } from './XmlCode'
import './ExecutionPreview.css'
import { Alert, Button, Checkbox, Descriptions, Select, Table, Tag } from 'antd'

interface Props { preview: Preview; busy: boolean; error?: string | null; onConfirm: () => void; onClose: () => void }

export function ExecutionPreview({ preview, busy, error, onConfirm, onClose }: Props) {
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [accepted, setAccepted] = useState(false)
  const [now, setNow] = useState(Date.now)
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer) }, [])
  const job = preview.jobs[selectedIndex] ?? preview.jobs[0]
  const expired = now >= Date.parse(preview.expires_at)
  const scenario = preview.spec.scenario
  const autoPlots = scenario.mode === 'static' && scenario.frequency === 'DF' && scenario.ambiguity === 'float'
  return <div className="execution-review">
    <div className="review-title"><div><p className="review-breadcrumb">实验工作台 / 执行复核</p><h1>执行前，请核对这份配置</h1><p>计划已冻结，解算尚未开始。预览不代表批准执行。</p></div><Tag color="blue">Revision {preview.revision} · {preview.intent === 'retry' ? '重试复核' : '尚未启动'}</Tag></div>
    <WorkflowSteps current={3} />
    <Alert className="review-notice" type="info" role="note" title={`确认将授权下列 ${preview.jobs.length} 个日任务。程序、模板、XML 或输入发生变化后，必须重新生成预览。`} />
    {(error || expired) && <Alert className="error-banner" role="alert" type="error" title={expired ? '执行预览已过期，请返回计划重新检查输入并生成 XML。' : error} />}
    <div className="execution-grid">
      <section className="execution-panel"><header><h2>本次执行清单</h2><span>{preview.jobs.length} 个日任务</span></header><div className="execution-body">
        <Descriptions className="execution-facts" size="small" column={2} layout="vertical" items={[
          {key:'date',label:'日期',children:`${job.year} / ${String(job.doy).padStart(3,'0')}`}, {key:'stations',label:'测站',children:`${preview.spec.stations.length} 个`},
          {key:'concurrency',label:'并发',children:preview.spec.concurrency}, {key:'scenario',label:'解算方案',children:`${scenario.mode==='static'?'静态':'动态'} · ${scenario.frequency} · ${scenario.ambiguity==='float'?'Float':'Fixed'}`},
          {key:'attempt',label:'尝试编号',children:String(job.attempt_number).padStart(4,'0')}, {key:'reset',label:'重置',children:`${scenario.reset_hours} 小时`},
        ]} />
        <h3>实际执行程序 <small>完整路径</small></h3><p className="execution-path executable-path">{preview.executable_path}</p><p className="execution-hash">SHA256: {preview.executable_sha256}</p>
        <h3>输出目录</h3><p className="execution-path">{job.output_dir}</p>
        <details className="execution-extra"><summary>模板、工作目录与 XML 路径</summary><h3>模板</h3><p className="execution-path">{preview.template_path}</p><p className="execution-hash">SHA256: {preview.template_sha256}</p><h3>工作目录</h3><p className="execution-path">{job.cwd}</p><h3>实际执行的 XML</h3><p className="execution-path">{job.xml_path}</p><p className="execution-hash">XML SHA256: {job.xml_sha256}</p></details>
        <h3>输入检查报告 <small>{job.inputs.length} 项已就绪</small></h3>
        <Table className="execution-inputs" size="small" pagination={false} tableLayout="fixed" dataSource={job.inputs} rowKey={item=>`${item.kind}-${item.station}-${item.path}`} columns={[
          {title:'类别 / 测站',key:'kind',width:'24%',render:(_,item)=><>{item.kind}<br/>{item.station}</>},
          {title:'文件路径',key:'path',render:(_,item)=><>{item.path}<small>{item.size_bytes.toLocaleString()} B</small></>},
          {title:'状态',key:'status',width:'20%',render:()=> <Tag color="success">就绪</Tag>},
        ]} />
      </div></section>
      <section className="execution-panel xml-panel"><header><h2>XML 配置预览</h2><span>只读 · 完整文件</span></header><div className="execution-xml-toolbar"><label>查看日期 XML<Select aria-label="查看日期 XML" virtual={false} value={selectedIndex} disabled={busy} onChange={value=>{setSelectedIndex(value);setAccepted(false)}} options={preview.jobs.map((item,index)=>({value:index,label:`${item.year} / DOY ${String(item.doy).padStart(3,'0')} · Attempt ${item.attempt_number}`}))} /></label><span>config.xml</span></div><XmlCode content={job.xml_content} /><p className="execution-xml-note">rinexo 每站一行；filter 参数纵向排列。执行时使用这里展示的同一份 XML，不重新渲染。</p></section>
    </div>
    <section className="execution-command"><h3>将执行的命令</h3><pre>{job.command.map((arg) => JSON.stringify(arg)).join(' ')}</pre><details><summary>查看准确参数数组</summary><pre>{JSON.stringify(job.command, null, 2)}</pre></details></section>
    <p className="execution-analysis">{autoPlots ? '后处理：当前静态 DF Float 成功后会自动分析并绘图；独立绘图开关尚待接入。' : '后处理：此场景需选定静态 DF Float 参考实验后单独分析。'} 有效期至 {new Date(preview.expires_at).toLocaleString()}。</p>
    <footer className="execution-confirm"><div><Checkbox checked={accepted} disabled={busy || expired} onChange={event=>setAccepted(event.target.checked)}>我已核对 XML、执行程序、输入文件和输出目录</Checkbox><p>确认后才会入队并启动真实解算。关闭预览或查看历史不会执行。</p></div><div className="execution-actions"><Button disabled={busy} onClick={onClose}>返回查看计划</Button><Button type="primary" disabled={!accepted || busy || expired} onClick={() => { setAccepted(false); onConfirm() }}>{busy ? '正在提交确认…' : '确认并开始解算'}</Button></div></footer>
  </div>
}
