import type { ExperimentSpec } from '../api/types'
import { Button, Descriptions, Tag } from 'antd'
export function PlanSummary({spec,disabled,onEdit}:{spec:ExperimentSpec;disabled:boolean;onEdit:()=>void}){
  return <section className="plan-summary"><header><div><h2>当前计划的参数解析</h2><p>这里展示已接受的计划，修改卡片位于右侧对话中。</p></div><Button disabled={disabled} onClick={onEdit}>手动调整参数</Button></header>
    <div className="summary-sheet"><h3>解算方案</h3><Descriptions bordered size="small" column={{xs:1,sm:2}} layout="vertical" items={[
      ['日期范围',spec.date_segments.map(s=>`${s.year} / DOY ${s.start_doy}${s.end_doy===s.start_doy?'':`–${s.end_doy}`}`).join('、')],
      ['模式',spec.scenario.mode==='static'?'静态':'动态'],['频率',`${spec.scenario.frequency==='DF'?'双频':'三频'} ${spec.scenario.frequency}`],
      ['模糊度',spec.scenario.ambiguity==='float'?'浮点解 Float':'固定解 Fixed'],['测站分组',`${spec.station_group_id} · ${spec.stations.length} 站`],
      ['并发数',String(spec.concurrency)],['重置周期',`${spec.scenario.reset_hours} 小时`],['排除日期',spec.excluded_dates.map(d=>`${d.year}/${d.doy}`).join('、')||'无'],
    ].map(([label,children])=>({key:label,label,children}))} /></div>
    <div className="summary-sheet"><h3>参与测站</h3><div className="summary-stations">{spec.stations.map(s=><Tag key={s}>{s}</Tag>)}</div></div>
    <Descriptions className="summary-profiles" size="small" column={1} items={[
      {key:'input',label:'输入',children:spec.input_profile_id}, {key:'template',label:'模板',children:spec.template_profile_id},
      {key:'solver',label:'程序配置',children:spec.solver_profile_id}, {key:'outputs',label:'输出',children:spec.outputs.join(' / ')},
    ]} />
  </section>
}
