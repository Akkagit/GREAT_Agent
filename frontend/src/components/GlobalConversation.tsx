import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { ConversationMessage, ExperimentCreatedReceipt } from '../api/types'
import { reconcileMessages } from './conversationMessages'
import { ChatPanel, type ChatMessage, type ChatProgress } from './ChatPanel'
import { ExperimentCreatedCard } from './ExperimentCreatedCard'

function rows(items:ConversationMessage[]):ChatMessage[]{return items.map(item=>({id:item.id,requestId:item.request_id,role:item.role,text:item.blocks.filter(block=>block.type==='text').map(block=>block.text).join('\n\n'),cardId:item.blocks.some(block=>block.type==='experiment_created')?`created-${item.id}`:undefined}))}
function receipts(items:ConversationMessage[]):Record<string,ExperimentCreatedReceipt>{
  return Object.fromEntries(items.flatMap(item=>item.blocks.filter(block=>block.type==='experiment_created').map(block=>[`created-${item.id}`,block])))
}

export function GlobalConversation({onOpenExperiment,onExperimentCreated}:{onOpenExperiment?:(id:string)=>void;onExperimentCreated?:()=>void}){
  const [messages,setMessages]=useState<ChatMessage[]>([]),[busy,setBusy]=useState(true),[progress,setProgress]=useState<ChatProgress|null>(null)
  const [created,setCreated]=useState<Record<string,ExperimentCreatedReceipt>>({})
  const conversation=useRef<string|null>(null),request=useRef<AbortController|null>(null),owner=useRef<object|null>(null),started=useRef(0)
  useEffect(()=>{
    const token={};owner.current=token
    api.ensureConversation(null).then(detail=>{if(owner.current===token){conversation.current=detail.id;setMessages(rows(detail.messages));setCreated(receipts(detail.messages))}}).catch(()=>{if(owner.current===token)setProgress({status:'failed',elapsedMs:0,errorMessage:'对话记录读取失败，请检查本地服务。'})}).finally(()=>{if(owner.current===token)setBusy(false)})
    return()=>{owner.current=null;request.current?.abort()}
  },[])
  useEffect(()=>{if(progress?.status!=='generating')return;const timer=window.setInterval(()=>setProgress(current=>current?.status==='generating'?{...current,elapsedMs:performance.now()-started.current}:current),250);return()=>window.clearInterval(timer)},[progress?.status])
  async function send(message:string){
    if(busy||request.current)return
    const token=owner.current,controller=new AbortController(),requestId=crypto.randomUUID()
    request.current=controller;started.current=performance.now();setBusy(true);setProgress({status:'generating',elapsedMs:0});setMessages(current=>[...current,{id:requestId,requestId,role:'user',text:message}])
    try{
      const detail=conversation.current?null:await api.ensureConversation(null)
      if(owner.current!==token||request.current!==controller)return
      if(detail){conversation.current=detail.id;setMessages(current=>reconcileMessages(current,rows(detail.messages),true));setCreated(current=>({...current,...receipts(detail.messages)}))}
      const turn=await api.sendConversationMessage(conversation.current!,message,requestId,controller.signal)
      if(owner.current!==token||request.current!==controller)return
      const newReceipts=receipts([turn.assistant_message])
      setCreated(current=>({...current,...newReceipts}))
      setMessages(current=>reconcileMessages(current.filter(row=>row.id!==requestId),rows([turn.user_message,turn.assistant_message])));setProgress({status:'completed',elapsedMs:performance.now()-started.current,timings:turn.timings,requestId:turn.request_id})
      if(Object.keys(newReceipts).length)onExperimentCreated?.()
    }catch(error){
      if(owner.current!==token||request.current!==controller)return
      // The server may have committed a draft before its HTTP reply was lost.
      // Recover the durable receipt before inviting another creation request.
      let recovered=false
      try{
        const detail=conversation.current?await api.getConversation(conversation.current):null
        if(owner.current!==token||request.current!==controller)return
        if(detail){
          const saved=detail.messages.find(row=>row.role==='assistant'&&row.request_id===requestId&&row.status==='completed')
          setMessages(current=>reconcileMessages(current,rows(detail.messages),true))
          setCreated(current=>({...current,...receipts(detail.messages)}))
          if(saved){
            recovered=true
            setProgress({status:'completed',elapsedMs:performance.now()-started.current,requestId})
            if(Object.keys(receipts([saved])).length)onExperimentCreated?.()
          }
        }
      }catch{ /* Keep the uncertain status visible if recovery is unavailable. */ }
      if(!recovered&&owner.current===token&&request.current===controller)setProgress({status:'failed',elapsedMs:performance.now()-started.current,errorMessage:'回复未完成。请先刷新对话记录和实验列表确认结果，再决定是否重新发送。',requestId:error instanceof ApiError?error.requestId:undefined})
    }
    finally{if(owner.current===token&&request.current===controller){request.current=null;setBusy(false)}}
  }
  function cancel(){request.current?.abort();request.current=null;setBusy(false);setProgress({status:'cancelled',elapsedMs:performance.now()-started.current})}
  return <ChatPanel messages={messages} busy={busy} onSend={send} onCancel={cancel} progress={progress} renderCard={id=>created[id]&&<ExperimentCreatedCard receipt={created[id]} onOpen={onOpenExperiment}/>}/>
}
