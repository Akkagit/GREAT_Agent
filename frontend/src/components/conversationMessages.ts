import type { ChatMessage } from './ChatPanel'

// The request UUID identifies optimistic rows before the server assigns message UUIDs.
// Proposal identity also upgrades a recovery placeholder to the durable explanation.
export function reconcileMessages(current:ChatMessage[],incoming:ChatMessage[],prepend=false):ChatMessage[]{
  const matches=(left:ChatMessage,right:ChatMessage)=>left===right || left.role===right.role && (
    (!!left.id && left.id===right.id) ||
    (!!right.requestId && (left.requestId===right.requestId || left.id===right.requestId)) ||
    (!!left.cardId && left.cardId===right.cardId)
  )
  const canonical=incoming.map(row=>{
    const previous=current.find(item=>matches(item,row))
    return {...previous,...row,...(!row.cardId && previous?.cardId?{cardId:previous.cardId}:{})}
  })
  if(prepend)return [...canonical,...current.filter(row=>!canonical.some(item=>matches(row,item)))]
  const result=current.map(row=>canonical.find(item=>matches(row,item)) ?? row)
  for(const row of canonical)if(!result.some(item=>matches(item,row)))result.push(row)
  return result.filter((row,index)=>result.findIndex(item=>matches(item,row))===index)
}
