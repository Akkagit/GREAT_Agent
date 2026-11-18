import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { ApiError, api } from '../api/client'
import type { ConversationMessage, ExecutionPreview, ExperimentSpec, ExperimentView, Proposal, RevisionIdentity, ValidationResult } from '../api/types'
import type { ResearchMaterial } from '../api/research'
import { reconcileMessages } from '../components/conversationMessages'
import { ChatPanel, type ChatMessage, type ChatProgress } from '../components/ChatPanel'
import { PlanSummary } from '../components/PlanSummary'
import { ProposalCard, type ProposalCardState } from '../components/ProposalCard'
import { ResizableWorkbench, type WorkbenchPane } from '../components/ResizableWorkbench'
import { ArtifactBrowser } from '../components/ArtifactBrowser'
import { ExecutionReviewPage } from './ExecutionReviewPage'
import { RunDashboard } from './RunDashboard'
import { ExperimentMonitorProvider } from '../monitoring/ExperimentMonitor'
import { useExperimentMonitor } from '../monitoring/useExperimentMonitor'
import './ExperimentWorkspace.css'
import './WorkbenchWorkspace.css'
import { Alert, Button, Tabs, Tag } from 'antd'

export type WorkbenchView = 'plan' | 'review' | 'plots' | 'run'
export interface WorkbenchSession { messages: ChatMessage[]; cards: Record<string,ProposalCardState>; pendingId: string | null; revision: number }
interface Props {
  experimentId: string; history?: ReactNode; view?: WorkbenchView; previewId?: string | null
  onViewChange?: (view: WorkbenchView) => void; onPrepared?: (preview: ExecutionPreview) => void; onRun?: () => void; onChanged?: () => void
  session?: WorkbenchSession; onSessionChange?: (session: WorkbenchSession) => void
  onResearch?: (material: Extract<ResearchMaterial, { kind: 'experiment' }>) => void
}
const proposalFailureMessages={
  schema:'模型提案失败：模型返回的计划未通过字段校验，请重试或更具体地描述修改。当前计划未改变。',
  invalid_json:'模型提案失败：模型返回的内容无法解析为计划，请重试。当前计划未改变。',
  timeout:'模型提案失败：模型响应超时，请稍后重试。当前计划未改变。',
  provider:'模型提案失败：模型服务暂时不可用，请稍后重试。当前计划未改变。',
}
const proposalFailureFallback='模型提案失败：请检查本地服务或网络连接后重试。当前计划未改变。'
function errorCode(error: unknown) { return typeof error==='object' && error!==null && 'code' in error ? String(error.code) : undefined }

export function ExperimentWorkspace({ experimentId,history,view,previewId,onViewChange,onPrepared,onRun,onChanged,session,onSessionChange,onResearch }: Props) {
  return <ExperimentMonitorProvider experimentId={experimentId}><MonitoredExperimentWorkspace key={experimentId} experimentId={experimentId} history={history} view={view} previewId={previewId} onViewChange={onViewChange} onPrepared={onPrepared} onRun={onRun} onChanged={onChanged} session={session} onSessionChange={onSessionChange} onResearch={onResearch} /></ExperimentMonitorProvider>
}
function MonitoredExperimentWorkspace({ experimentId,history,view,previewId,onViewChange,onPrepared,onRun,onChanged,session,onSessionChange,onResearch }: Props) {
  const monitor = useExperimentMonitor()
  const [initialSession]=useState(()=>session)
  const initialExperiment=useRef(experimentId)
  const [experiment,setExperiment]=useState<ExperimentView|null>(null)
  const tasks = monitor.tasks
  const [validation,setValidation]=useState<ValidationResult|null>(null)
  const [checkPhase,setCheckPhase]=useState<'idle'|'checking'|'complete'|'failed'|'stale'>('idle')
  const checkGeneration=useRef(0), currentRevision=useRef<RevisionIdentity|null>(null), ownerExperiment=useRef(experimentId)
  const [messages,setMessages]=useState<ChatMessage[]>(session?.messages ?? [])
  const [cards,setCards]=useState<Record<string,ProposalCardState>>(session?.cards ?? {})
  const [pendingId,setPendingId]=useState<string|null>(session?.pendingId ?? null)
  const [busy,setBusy]=useState(true),[error,setError]=useState<string|null>(null)
  const [mobilePane,setMobilePane]=useState<WorkbenchPane>('center')
  const [localView,setLocalView]=useState<WorkbenchView>('plan'),[localPreviewId,setLocalPreviewId]=useState<string|null>(null)
  const [chatProgress,setChatProgress]=useState<ChatProgress|null>(null)
  const request=useRef<AbortController|null>(null),executionRequest=useRef<AbortController|null>(null),started=useRef(0),action=useRef(false),sequence=useRef(0)
  const conversation=useRef<string|null>(null),historyVersion=useRef(0)
  const owner=useRef<object|null>(null),navigationVersion=useRef(0)
  const recoveryController=useRef({generation:0,localPendingId:session?.pendingId ?? null})
  const [proposalAuthority,setProposalAuthority]=useState<'unknown'|'loading'|'clear'|'pending'|'failed'>('unknown')
  const recovering=proposalAuthority!=='clear' || !monitor.snapshot
  const chosenView=view ?? localView, chosenPreviewId=previewId ?? localPreviewId
  const frozen=experiment?.status==='ready', readOnly=!!frozen || tasks.length>0 || !monitor.snapshot

  useEffect(()=>{navigationVersion.current++},[view,previewId])
  useEffect(()=>{
    owner.current={};action.current=false
    if(ownerExperiment.current!==experimentId){
      ownerExperiment.current=experimentId;currentRevision.current=null;conversation.current=null;historyVersion.current++
      checkGeneration.current++;setValidation(null);setCheckPhase('idle');setExperiment(null)
      setMessages([]);setCards({});setPendingId(null);setBusy(true);setError(null);setLocalView('plan');setLocalPreviewId(null);setChatProgress(null)
      recoveryController.current.localPendingId=null;recoveryController.current.generation++;setProposalAuthority('unknown')
    }
    return()=>{owner.current=null;request.current?.abort();request.current=null}
  },[experimentId])
  const invalidateCheck=useCallback(()=>{checkGeneration.current++;setValidation(null);setCheckPhase('idle');setLocalPreviewId(null)},[])
  const matchesCurrent=(identity:RevisionIdentity)=>identity.revision_id===currentRevision.current?.revision_id && identity.revision===currentRevision.current?.revision
  const validated=!!validation?.ok && validation.revision_id===experiment?.current_revision_id && validation.revision===experiment?.current_revision && checkPhase==='complete'

  useEffect(()=>{
    if(chatProgress?.status!=='generating')return
    const timer=window.setInterval(()=>setChatProgress(current=>current?.status==='generating'?{...current,elapsedMs:performance.now()-started.current}:current),250)
    return()=>window.clearInterval(timer)
  },[chatProgress?.status])
  useEffect(()=>{if(experiment)onSessionChange?.({messages,cards,pendingId,revision:experiment.current_revision})},[messages,cards,pendingId,experiment,onSessionChange])

  const loadExperiment=useCallback(async(preservePreview=false)=>{
    const token=owner.current
    const latest=await api.getExperiment(experimentId)
    if(token && owner.current===token){
      const sameRevision=currentRevision.current?.revision_id===latest.current_revision_id && currentRevision.current?.revision===latest.current_revision
      currentRevision.current={revision_id:latest.current_revision_id,revision:latest.current_revision};setExperiment(latest)
      if(!preservePreview || !sameRevision)invalidateCheck()
    }
    return latest
  },[experimentId,invalidateCheck])
  const offer=useCallback((candidate: ExperimentSpec,proposal: Proposal|null,text: string,baseline?:{base_revision_id:string;base_revision_number:number})=>{
    historyVersion.current++
    const base=proposal ?? baseline
    if(!base)return
    const id=proposal ? `proposal-${proposal.id}` : `card-${Date.now()}-${++sequence.current}`
    recoveryController.current.generation++
    recoveryController.current.localPendingId=id
    setProposalAuthority(proposal?'pending':'unknown')
    setCards(current=>({...Object.fromEntries(Object.entries(current).map(([key,card])=>[key,card.status==='pending'?{...card,status:'superseded' as const}:card])),[id]:{candidate,proposal,base_revision_id:base.base_revision_id,base_revision_number:base.base_revision_number,status:'pending',edited:false}}))
    setPendingId(id);invalidateCheck();setMessages(current=>[...current.filter(row=>row.cardId!==id),{role:'assistant',text,cardId:id}])
  },[invalidateCheck])
  const recoverProposals=useCallback(async()=>{
    const token=owner.current
    if(!token)return
    const generation=++recoveryController.current.generation
    setProposalAuthority('loading')
    try{
      const proposals=await api.listProposals(experimentId,'pending')
      if(owner.current!==token || recoveryController.current.generation!==generation)return
      const latestPending=proposals.find(proposal=>proposal.status==='pending')
      setProposalAuthority(latestPending?'pending':'clear')
      // A server query can establish authority without replacing an active local draft.
      if(latestPending && !recoveryController.current.localPendingId){
        offer(latestPending.candidate,latestPending,latestPending.explanation || '已恢复待确认的提案。')
      }
    }catch{
      if(owner.current!==token || recoveryController.current.generation!==generation)return
      setProposalAuthority('failed');setError('无法恢复待确认提案，请刷新后重试；仍可创建新的修改卡片。')
    }
  },[experimentId,offer])
  useEffect(()=>{
    let disposed=false
    Promise.resolve().then(()=>loadExperiment()).then(latest=>{
      if(disposed)return
      const restored=initialExperiment.current===experimentId?initialSession:undefined
      const stale=restored && restored.revision!==latest.current_revision
      if(stale){
        recoveryController.current.localPendingId=null
        setCards(current=>Object.fromEntries(Object.entries(current).map(([id,card])=>[id,card.status==='pending'?{...card,status:'stale' as const}:card])));setPendingId(null)
      }
      const token=owner.current,version=historyVersion.current
      void api.ensureConversation(experimentId).then(detail=>{
        if(disposed || owner.current!==token)return
        conversation.current=detail.id
        appendServerMessages(detail.messages,true,historyVersion.current===version)
      }).catch(()=>{if(!disposed && owner.current===token)setError('对话记录读取失败；发送时将重试连接。')}).finally(()=>{if(!disposed && owner.current===token && (!restored?.pendingId || stale))void recoverProposals()})
    })
      .catch(()=>{if(!disposed)setError('无法读取计划。确认本地服务正在运行。')}).finally(()=>{if(!disposed)setBusy(false)})
    return()=>{disposed=true}
  },[loadExperiment,initialSession,recoverProposals,experimentId])

  function changeView(next: WorkbenchView){navigationVersion.current++;setLocalView(next);onViewChange?.(next)}
  function changeMobilePane(pane:WorkbenchPane){navigationVersion.current++;setMobilePane(pane)}
  function viewXmlPreview(){changeView('review');setMobilePane('center')}
  function appendServerMessages(incoming:ConversationMessage[],restoring=false,hydrateCards=true){
    const rows:ChatMessage[]=[]
    for(const item of incoming){
      const text=item.blocks.filter(block=>block.type==='text').map(block=>block.text).join('\n\n')
      const proposal=item.blocks.find(block=>block.type==='proposal')?.proposal
      const cardId=proposal ? `proposal-${proposal.id}` : undefined
      if(proposal && cardId && (hydrateCards || (proposal.status!=='pending' && recoveryController.current.localPendingId===cardId))){
        const status=restoring && proposal.status==='pending' && proposal.base_revision_id!==currentRevision.current?.revision_id ? 'stale' : proposal.status
        setCards(current=>current[cardId]?.edited && status==='pending' ? current : {...current,[cardId]:{candidate:proposal.candidate,proposal,base_revision_id:proposal.base_revision_id,base_revision_number:proposal.base_revision_number,status,edited:false}})
        if(restoring && status!=='pending' && recoveryController.current.localPendingId===cardId){recoveryController.current.localPendingId=null;setPendingId(null);setProposalAuthority('clear')}
        if(status==='pending' && !recoveryController.current.localPendingId){recoveryController.current.localPendingId=cardId;setPendingId(cardId);setProposalAuthority('pending')}
      }
      rows.push({id:item.id,requestId:item.request_id,role:item.role,text,...(cardId?{cardId}:{})})
    }
    setMessages(current=>reconcileMessages(current,rows,restoring))
  }
  async function sendMessage(message: string){
    if(request.current || busy)return
    historyVersion.current++
    const token=owner.current,version=navigationVersion.current
    const executionPreviewId=chosenView==='review' && chosenPreviewId ? chosenPreviewId : undefined
    const controller=new AbortController();request.current=controller;started.current=performance.now()
    executionRequest.current=executionPreviewId?controller:null
    const requestId=crypto.randomUUID()
    setBusy(true);setError(null);setChatProgress({status:'generating',elapsedMs:0});setMessages(current=>[...current,{id:requestId,requestId,role:'user',text:message}])
    try{
      const detail=conversation.current ? null : await api.ensureConversation(experimentId)
      if(request.current!==controller || controller.signal.aborted || owner.current!==token)return
      if(detail){conversation.current=detail.id;appendServerMessages(detail.messages,true,false)}
      const next=executionPreviewId
        ? await api.sendConversationMessage(conversation.current!,message,requestId,controller.signal,executionPreviewId)
        : await api.sendConversationMessage(conversation.current!,message,requestId,controller.signal)
      if(request.current!==controller || controller.signal.aborted || owner.current!==token)return
      const proposal=next.assistant_message.blocks.find(block=>block.type==='proposal')?.proposal
      if(proposal && proposal.status==='pending'){
        // Only an actual proposal changes plan gates. Text-only turns preserve pending and validation.
        recoveryController.current.generation++
        offer(proposal.candidate,proposal,'')
        setMessages(current=>current.filter(row=>row.cardId!==`proposal-${proposal.id}`))
      }
      setMessages(current=>current.map(row=>row.id===requestId?{...row,id:next.user_message.id}:row))
      appendServerMessages([next.user_message,next.assistant_message])
      setChatProgress({status:'completed',elapsedMs:performance.now()-started.current,timings:next.timings,requestId:next.request_id})
      if(next.status==='completed' && next.assistant_message.status==='completed' && executionPreviewId && next.assistant_message.blocks.some(block=>block.type==='execution' && block.experiment_id===experimentId && block.preview_id===executionPreviewId))executionStarted(version===navigationVersion.current)
    }catch(caught){
      if(request.current!==controller || controller.signal.aborted || owner.current!==token)return
      const diagnostics=caught instanceof ApiError && caught.code==='model_proposal_error' && caught.status===502?caught:undefined
      const errorMessage=executionPreviewId && !diagnostics
        ? caught instanceof ApiError ? caught.message : '回复未完成，请检查连接，并查看实验进度确认执行结果。'
        : (diagnostics?.category?proposalFailureMessages[diagnostics.category]:undefined) ?? proposalFailureFallback
      setChatProgress({status:'failed',elapsedMs:performance.now()-started.current,errorMessage,requestId:diagnostics?.requestId});setError(errorMessage)
      request.current=null;void recoverProposals()
      if(executionPreviewId)refreshExecutionStatus(true)
    }finally{if(owner.current===token && (request.current===controller || request.current===null)){if(request.current===controller)request.current=null;setBusy(false)}}
  }
  function cancelChat(){const controller=request.current;if(!controller)return;request.current=null;controller.abort();setChatProgress({status:'cancelled',elapsedMs:performance.now()-started.current});setBusy(false);void recoverProposals();if(executionRequest.current===controller)refreshExecutionStatus(true)}

  async function resolveCard(id: string,accept: boolean){
    const card=cards[id]
    if(!experiment || !card || card.status!=='pending' || id!==pendingId || action.current || busy || readOnly)return
    action.current=true;setBusy(true);setError(null)
    const token=owner.current
    try{
      const proposal=(card.edited || !card.proposal) ? await api.previewProposal(experimentId,{source:'card',candidate:card.candidate,base_revision_id:card.base_revision_id,...(card.proposal?{source_proposal_id:card.proposal.id}:{})}) : card.proposal
      if(owner.current!==token)return
      recoveryController.current.generation++;setProposalAuthority('pending')
      setCards(current=>({...current,[id]:{...current[id],candidate:proposal.candidate,proposal,edited:false}}))
      if(!accept)return
      const revision=await api.acceptRevision(experimentId,{proposal_id:proposal.id,expected_revision:proposal.base_revision_number})
      if(owner.current!==token)return
      recoveryController.current.generation++;recoveryController.current.localPendingId=null;setProposalAuthority('clear')
      currentRevision.current={revision_id:revision.id,revision:revision.number}
      setExperiment(current=>current?{...current,current_revision:revision.number,current_revision_id:revision.id,spec:revision.spec}:current)
      setCards(current=>({...current,[id]:{...current[id],candidate:revision.spec,proposal,status:'accepted',edited:false,accepted_revision_id:revision.id,accepted_revision_number:revision.number}}));setPendingId(null);invalidateCheck();onChanged?.()
      await checkInputs({revision_id:revision.id,revision:revision.number})
    }catch(caught){
      if(owner.current!==token)return
      if(errorCode(caught)==='revision_conflict'){
        setError('计划已被更新，已重新载入最新 revision。请重新预览修改。')
        recoveryController.current.localPendingId=null
        setProposalAuthority('unknown')
        setCards(current=>({...current,[id]:{...current[id],status:'stale'}}));setPendingId(null)
        try{await loadExperiment();if(owner.current===token)void recoverProposals()}
        catch{if(owner.current===token){setProposalAuthority('failed');setError('计划已改变，但重新读取失败。请刷新后重试。')}}
      }else setError('计划字段校验或接受修改失败，当前计划没有被覆盖。请检查卡片后重试。')
    }finally{if(owner.current===token){action.current=false;setBusy(false)}}
  }
  async function rejectCard(id: string){
    const card=cards[id]
    if(!card || card.status!=='pending' || id!==pendingId || action.current || busy || readOnly)return
    const token=owner.current
    action.current=true;setBusy(true);setError(null)
    try{
      if(card.proposal)await api.rejectProposal(experimentId,card.proposal.id)
      if(owner.current!==token)return
      recoveryController.current.generation++;recoveryController.current.localPendingId=null
      setCards(current=>({...current,[id]:{...current[id],status:'rejected'}}));setPendingId(null);setValidation(null)
      if(card.proposal)setProposalAuthority('clear')
      else void recoverProposals()
    }catch{if(owner.current===token)setError('放弃提案未保存，请检查连接后重试。提案仍待确认。')}
    finally{if(owner.current===token){action.current=false;setBusy(false)}}
  }
  // Called inside the acceptance lock with the returned immutable identity.
  async function checkInputs(identity:RevisionIdentity){
    const token=owner.current,generation=++checkGeneration.current
    setValidation(null);setCheckPhase('checking')
    const active=()=>!!token && owner.current===token && checkGeneration.current===generation && matchesCurrent(identity)
    try{
      const result=await api.validateExperiment(experimentId,{expected_revision_id:identity.revision_id,expected_revision:identity.revision})
      if(!active())return
      if(result.revision_id!==identity.revision_id || result.revision!==identity.revision){setCheckPhase('stale');return}
      setValidation(result);setCheckPhase('complete')
    }catch(caught){
      if(!active())return
      if(errorCode(caught)==='revision_conflict'){
        setCheckPhase('stale');setError('检查已失效，计划版本已改变，请重新读取并检查。')
        try{await loadExperiment();if(owner.current===token){setCheckPhase('stale');void recoverProposals()}}catch{ /* Keep the accepted receipt and stale gate. */ }
      }else {setCheckPhase('failed');setError('输入检查未完成，请检查服务后重试。')}
    }
  }
  async function validate(){
    if(busy||action.current||recovering||pendingId||tasks.length||!monitor.snapshot||!currentRevision.current)return
    const token=owner.current
    action.current=true;setBusy(true);setError(null)
    try{await checkInputs({...currentRevision.current})}
    finally{if(owner.current===token){action.current=false;setBusy(false)}}
  }
  function prepared(preview: ExecutionPreview,navigate=true){if(!owner.current)return;setLocalPreviewId(preview.id);setExperiment(current=>current?{...current,status:'ready'}:current);if(navigate){setLocalView('review');setMobilePane('center');onPrepared?.(preview)}onChanged?.()}
  function refreshExecutionStatus(preservePreview=false){
    const token=owner.current
    if(!token)return
    monitor.refresh()
    void loadExperiment(preservePreview).catch(()=>{if(owner.current===token)setError('任务状态读取失败，请查看实验进度或刷新确认执行结果。')})
    onChanged?.()
  }
  function executionStarted(navigate=true){
    if(!owner.current)return
    setLocalPreviewId(null);setValidation(null)
    refreshExecutionStatus()
    if(navigate){setLocalView('run');setMobilePane('center');if(onRun)onRun();else onViewChange?.('run')}
  }
  async function prepareExecution(){
    if(!validated || !experiment || !monitor.snapshot || recovering || pendingId || tasks.length || busy || action.current)return
    action.current=true;setBusy(true);setError(null)
    const token=owner.current,version=navigationVersion.current,generation=checkGeneration.current,identity=currentRevision.current
    try{const preview=await api.prepareExecution(experimentId,{expected_revision:experiment.current_revision});if(owner.current===token && generation===checkGeneration.current && identity && matchesCurrent(identity))prepared(preview,version===navigationVersion.current)}
    catch(caught){if(owner.current===token){
      if(errorCode(caught)==='pending_proposal'){setError('还有待确认的服务端提案，已重新读取。请先接受或放弃。');void recoverProposals()}
      else setError(caught instanceof Error?caught.message:'XML 生成失败，请重新检查输入。')
      try{await loadExperiment()}catch{if(owner.current===token)setError('XML 生成未完成，重新读取计划失败；请刷新确认冻结状态。')}
    }}
    finally{if(owner.current===token){action.current=false;setBusy(false)}}
  }
  function handoff(card:ProposalCardState){
    if(card.status!=='accepted')return null
    const current=card.accepted_revision_id===experiment?.current_revision_id && card.accepted_revision_number===experiment?.current_revision
    const blocked=busy||recovering||!!pendingId||tasks.length>0||!current
    return <section className="proposal-input-handoff" aria-label="输入检查交接">
      {!current || checkPhase==='stale'?<p>检查已失效，计划版本已改变。</p>
      :checkPhase==='checking'?<p role="status">输入检查中…</p>
      :checkPhase==='failed'?<><p>输入检查未完成，接受修改已保存。</p><Button disabled={blocked} onClick={()=>void validate()}>重试检查</Button></>
      :validated?<><p>输入检查通过</p><p>生成 XML 将冻结计划；实际执行仍需确认。</p>{localPreviewId?<Button onClick={viewXmlPreview}>查看 XML 预览</Button>:<Button type="primary" disabled={blocked} onClick={()=>void prepareExecution()}>生成 XML 并预览</Button>}</>
      :validation?<><p>缺少 {validation.missing.length} 项输入</p><ul>{validation.missing.map((item,index)=><li key={index}>{item.kind} · {item.year} / DOY {item.doy} · {item.station ?? '公共输入'}<br/><code>{item.path ?? '路径未提供'}</code> · {item.state ?? 'missing'}</li>)}</ul><Button disabled={blocked} onClick={()=>void validate()}>重新检查输入</Button></>
      :<><p>检查已失效，请重新检查输入。</p><Button disabled={blocked} onClick={()=>void validate()}>重新检查输入</Button></>}
    </section>
  }
  function renderCard(id: string){const card=cards[id];if(!card)return null
    return <ProposalCard card={card} handoff={handoff(card)} busy={busy||readOnly} onChange={candidate=>{setCards(current=>({...current,[id]:{...current[id],candidate,edited:true}}));invalidateCheck()}}
      onPreview={()=>void resolveCard(id,false)} onAccept={()=>void resolveCard(id,true)} onReject={()=>void rejectCard(id)} />
  }
  let content: ReactNode
  if(!experiment)content=<div className="workbench-loading" role={error?'alert':'status'}>{error ?? '正在读取实验计划…'}</div>
  else if(chosenView==='review')content=chosenPreviewId
    ? <ExecutionReviewPage key={chosenPreviewId} experimentId={experimentId} previewId={chosenPreviewId} onBack={()=>changeView('plan')} onStarted={executionStarted} />
    : tasks.length ? <ArtifactBrowser key={`${experimentId}-xml`} experimentId={experimentId} mode="xml" />
    : <div className="view-empty"><h2>尚未生成执行 XML</h2><p>先接受计划修改并检查输入，再点击“生成 XML 并预览”。查看此页不会启动解算。</p><Button onClick={()=>changeView('plan')}>返回参数解析</Button></div>
  else if(chosenView==='plots')content=<ArtifactBrowser key={`${experimentId}-images`} experimentId={experimentId} mode="images" onResearch={onResearch} />
  else if(chosenView==='run')content=<RunDashboard experimentId={experimentId} onPrepared={prepared} onPlan={()=>changeView('plan')} onResearch={onResearch} />
  else content=<><PlanSummary spec={experiment.spec} disabled={busy||readOnly} onEdit={()=>offer(experiment.spec,null,'可以直接在卡片中调整参数，预览或接受后才会更新计划。',{base_revision_id:experiment.current_revision_id,base_revision_number:experiment.current_revision})} />
    {pendingId&&<Alert className="pending-proposal-note" type="info" role="note" title="右侧对话有待确认的修改，请先接受或放弃。" />}
    <footer className="preflight-bar"><div><strong>执行门禁</strong><span>{tasks.length?'已有执行记录，只读查看；失败任务从监控页复核重试':pendingId?'存在未接受的修改，请先审核提案':recovering?'正在恢复待确认提案':checkPhase==='checking'?'正在检查本地输入':checkPhase==='failed'?'输入检查未完成，请重试':checkPhase==='stale'?'输入检查已失效':validated?'全部输入已就绪':validation?`缺少 ${validation.missing.length} 项：${validation.missing[0]?.station ?? validation.missing[0]?.kind}`:frozen?'计划已冻结，可重新检查并生成预览':'尚未检查本地输入'}</span></div><div className="gate-actions"><Button disabled={busy||recovering||!!pendingId||tasks.length>0} onClick={()=>void validate()}>检查输入</Button><Button type="primary" disabled={busy||recovering||!!pendingId||tasks.length>0||!validated} onClick={()=>void prepareExecution()}>生成 XML 并预览</Button></div></footer></>
  return <ResizableWorkbench mobilePane={mobilePane} onMobilePaneChange={changeMobilePane} history={history ?? <div className="workspace-history-placeholder">当前实验 {experimentId}</div>}
    center={<div className="unified-workspace"><header className="unified-workspace-header"><p>实验工作台 / {experiment?.spec.date_segments.map(s=>`${s.year} / DOY ${s.start_doy}${s.start_doy===s.end_doy?'':`–${s.end_doy}`}`).join('、') ?? experimentId}</p><div><h1>实验内容工作区</h1>{experiment&&<Tag>Revision {experiment.current_revision}</Tag>}</div></header>
      {error&&experiment&&<Alert className="error-banner" type="error" role="alert" title={error} />}
      {monitor.error && chosenView==='plan' && <Alert type="warning" role="alert" title={monitor.error} />}
      <Tabs className="workspace-tabs" aria-label="实验内容视图" activeKey={chosenView} destroyOnHidden styles={{body:{flex:1,minHeight:0,overflow:'hidden'},content:{height:'100%'}}} onChange={key=>changeView(key as WorkbenchView)} items={([['plan','参数解析'],['review','XML 预览'],['plots','图片预览'],['run','实验进度']] as const).map(([key,label])=>({key,label,style:{height:'100%',overflowX:'hidden',overflowY:'auto'},children:key===chosenView?<div className="workspace-canvas">{content}</div>:undefined}))} />
      <footer className="workspace-safety">接受提案、检查输入与生成 XML 都不会启动 GREAT；真实执行需要你明确确认。</footer></div>}
    chat={<ChatPanel messages={messages} busy={busy} readOnly={readOnly} executionReady={chosenView==='review' && !!chosenPreviewId} onSend={sendMessage} progress={chatProgress} onCancel={cancelChat} renderCard={renderCard} />} />
}
