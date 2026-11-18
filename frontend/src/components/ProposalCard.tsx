import type { ReactNode } from 'react'
import type { ExperimentSpec, Proposal } from '../api/types'
import { Alert, Button, InputNumber, Select, Tag } from 'antd'

export interface ProposalCardState {
  candidate: ExperimentSpec; proposal: Proposal | null
  base_revision_id: string; base_revision_number: number
  accepted_revision_id?: string; accepted_revision_number?: number
  status: 'pending' | 'accepted' | 'rejected' | 'superseded' | 'stale'; edited: boolean
}
interface Props { handoff?: ReactNode; card: ProposalCardState; busy: boolean; onChange: (candidate: ExperimentSpec) => void; onPreview: () => void; onAccept: () => void; onReject: () => void }
export function ProposalCard({card,handoff,busy,onChange,onPreview,onAccept,onReject}:Props){
  const candidate=card.candidate
  const valid=candidate.date_segments.length>0&&candidate.date_segments.every(s=>{
    const leap=s.year%4===0&&(s.year%100!==0||s.year%400===0)
    return [s.year,s.start_doy,s.end_doy].every(Number.isInteger)&&s.year>0&&s.start_doy>=1&&s.end_doy<=(leap?366:365)&&s.end_doy>=s.start_doy
  })&&Number.isInteger(candidate.concurrency)&&candidate.concurrency>=1&&candidate.concurrency<=8
  const receipt={accepted:'已接受修改，尚未授权执行。',rejected:'已放弃这份提案，计划未改变。',superseded:'已由后续提案替代。',stale:'计划版本已改变，请重新生成提案。'}
  function scenario(key:keyof ExperimentSpec['scenario'],value:string|number){onChange({...candidate,scenario:{...candidate.scenario,[key]:value}})}
  return <section className="inline-proposal-card" aria-label="计划修改卡片"><header><strong>计划修改卡片</strong><Tag color={card.status==='accepted'?'success':'blue'}>{card.status==='pending'?'待你确认':card.status==='accepted'?'已接受':'未应用'}</Tag></header>
    {card.status!=='pending'?<p className={`proposal-receipt ${card.status}`}>{receipt[card.status]}</p>:<>
      <div className="inline-proposal-fields">
        <label>模式<Select aria-label="模式" virtual={false} disabled={busy} value={candidate.scenario.mode} onChange={value=>scenario('mode',value)} options={[{value:'static',label:'静态'},{value:'kinematic',label:'动态'}]} /></label>
        <label>频率<Select aria-label="频率" virtual={false} disabled={busy} value={candidate.scenario.frequency} onChange={value=>scenario('frequency',value)} options={[{value:'DF',label:'双频 DF'},{value:'TF',label:'三频 TF'}]} /></label>
        <label>模糊度<Select aria-label="模糊度" virtual={false} disabled={busy} value={candidate.scenario.ambiguity} onChange={value=>scenario('ambiguity',value)} options={[{value:'float',label:'浮点解'},{value:'fixed',label:'固定解'}]} /></label>
        <label>重置周期<Select aria-label="重置周期" virtual={false} disabled={busy} value={candidate.scenario.reset_hours} onChange={value=>scenario('reset_hours',value)} options={[{value:0,label:'不重置'},{value:4,label:'4 小时'},{value:8,label:'8 小时'}]} /></label>
      </div>
      {candidate.date_segments.map((segment,index)=><fieldset key={index} className="proposal-date-segment"><legend>日期段 {index+1}</legend><div>{(['year','start_doy','end_doy'] as const).map((key,k)=><label key={key}>{['年份','起始 DOY','结束 DOY'][k]}<InputNumber aria-label={['年份','起始 DOY','结束 DOY'][k]} disabled={busy} value={Number.isFinite(segment[key])?segment[key]:null} onChange={value=>onChange({...candidate,date_segments:candidate.date_segments.map((item,i)=>i===index?{...item,[key]:value??Number.NaN}:item)})}/></label>)}</div></fieldset>)}
      <div className="proposal-concurrency"><label>并发数<InputNumber aria-label="并发数" aria-valuemin={1} aria-valuemax={8} disabled={busy} value={Number.isFinite(candidate.concurrency)?candidate.concurrency:null} onChange={value=>onChange({...candidate,concurrency:value??Number.NaN})}/></label><small>{candidate.stations.length} 站 · {candidate.excluded_dates.length} 个排除日期，其他输入与输出配置保留。</small></div>
      {!valid&&<Alert className="proposal-invalid" type="error" role="alert" title="日期或并发数无效，请检查后再接受。" />}
      {card.proposal&&!card.edited&&<div className="inline-proposal-diff">{card.proposal.diff.map(diff=><div key={diff.path}><strong>{diff.path.split('/').filter(Boolean).at(-1)}</strong><span>{typeof diff.before==='object'?JSON.stringify(diff.before):String(diff.before)} → {typeof diff.after==='object'?JSON.stringify(diff.after):String(diff.after)}</span></div>)}</div>}
      {card.edited&&<p className="proposal-edit-note">卡片已调整；接受时将先重新校验，再接受新的提案。</p>}
      <footer><Button type="primary" disabled={busy||!valid} onClick={onAccept}>接受修改</Button><Button type="text" disabled={busy||!valid} onClick={onPreview}>预览修改</Button><Button type="text" disabled={busy} onClick={onReject}>放弃提案</Button></footer>
    </>}
    {handoff}
  </section>
}
