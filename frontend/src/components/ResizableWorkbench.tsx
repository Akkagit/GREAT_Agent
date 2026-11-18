import { useEffect, useRef, useState, type ReactNode, type PointerEvent } from 'react'
import './ResizableWorkbench.css'
import { Button } from 'antd'

export type WorkbenchPane = 'history' | 'center' | 'chat'
interface Props { history: ReactNode; center: ReactNode; chat: ReactNode; mobilePane?: WorkbenchPane; onMobilePaneChange?: (pane:WorkbenchPane)=>void; idPrefix?: string; layoutKey?: string; labels?: Record<WorkbenchPane, string> }
const defaults = { history: 232, chat: 386 }
const minimum = { history: 180, chat: 290, center: 360 }
type Side = 'history' | 'chat'

function fit(width: number, sizes: typeof defaults) {
  const available = Math.max(width, 850) - 20 - minimum.center
  const history = Math.max(minimum.history, Math.min(sizes.history, available - minimum.chat))
  const chat = Math.max(minimum.chat, Math.min(sizes.chat, available - history))
  return { history, chat }
}
function restore(width: number, storageKey: string) {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) ?? 'null')
    if (saved && [saved.history, saved.chat].every(value => typeof value === 'number' && Number.isFinite(value) && value > 0 && value < .8)) {
      return fit(width, { history: saved.history * width, chat: saved.chat * width })
    }
  } catch { /* Storage is optional; layout remains usable. */ }
  return fit(width, defaults)
}

export function ResizableWorkbench({ history, center, chat, mobilePane:controlledMobilePane, onMobilePaneChange, idPrefix = 'workbench', layoutKey: storageKey = 'great-agent-workbench-layout-v1', labels }: Props) {
  const board = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(() => Math.max(850, window.innerWidth - 28))
  const [sizes, setSizes] = useState(() => restore(Math.max(850, window.innerWidth - 28), storageKey))
  const [localMobilePane, setLocalMobilePane] = useState<WorkbenchPane>('center')
  const mobilePane = controlledMobilePane ?? localMobilePane
  function changeMobilePane(pane:WorkbenchPane){
    if(controlledMobilePane===undefined)setLocalMobilePane(pane)
    onMobilePaneChange?.(pane)
  }
  const drag = useRef<{ side: Side; x: number; size: number } | null>(null)
  const fitted = fit(width, sizes)

  useEffect(() => {
    const measure = () => {
      const next = board.current?.getBoundingClientRect().width || window.innerWidth - 28
      if (window.innerWidth <= 880) return
      setWidth(Math.max(850, next))
      setSizes(restore(Math.max(850, next), storageKey))
    }
    measure()
    window.addEventListener('resize', measure)
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    if (board.current) observer?.observe(board.current)
    return () => { observer?.disconnect(); window.removeEventListener('resize', measure) }
  }, [storageKey])

  function update(side: Side, value: number) {
    const other = side === 'history' ? 'chat' : 'history'
    const requested = Math.max(minimum[side], Math.min(value, width - 20 - minimum.center - minimum[other]))
    const next = fit(width, { ...fitted, [side]: requested, [other]: Math.min(fitted[other], width - 20 - minimum.center - requested) })
    setSizes(next)
    try { localStorage.setItem(storageKey, JSON.stringify({ history: next.history / width, chat: next.chat / width })) } catch { /* optional */ }
  }
  function start(side: Side, event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return
    drag.current = { side, x: event.clientX, size: fitted[side] }
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }
  function move(event: PointerEvent<HTMLDivElement>) {
    const current = drag.current
    if (current) update(current.side, current.size + (event.clientX - current.x) * (current.side === 'history' ? 1 : -1))
  }
  function reset() {
    setSizes(fit(width, defaults))
    try { localStorage.removeItem(storageKey) } catch { /* optional */ }
  }
  function separator(side: Side) {
    return <div className="workbench-separator" role="separator" tabIndex={0} aria-orientation="vertical"
      aria-label={labels ? `调整${labels[side]}宽度` : side === 'history' ? '调整历史区宽度' : '调整对话区宽度'} aria-valuemin={minimum[side]}
      aria-valuemax={Math.floor(width - 20 - minimum.center - fitted[side === 'history' ? 'chat' : 'history'])}
      aria-valuenow={Math.round(fitted[side])} aria-controls={`${idPrefix}-${side}`}
      onPointerDown={event => start(side, event)} onPointerMove={move}
      onPointerUp={() => { drag.current = null }} onPointerCancel={() => { drag.current = null }} onLostPointerCapture={() => { drag.current = null }}
      onKeyDown={event => { if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
        event.preventDefault(); update(side, fitted[side] + (event.key === 'ArrowRight' ? 1 : -1) * (side === 'history' ? 1 : -1) * (event.shiftKey ? 30 : 10)) }} />
  }
  return <div className="workbench-frame">
    <div className="workbench-tools"><span>拖动分隔线调整宽度</span><nav className="workbench-mobile-nav" aria-label={labels ? `${labels.center}区域切换` : '区域切换'}>{([['history','实验'],['center','工作区'],['chat','对话']] as const).map(([key,label]) => <Button key={key} type={mobilePane===key?'primary':'text'} size="small" aria-label={labels?.[key] ?? label} aria-pressed={mobilePane===key} onClick={() => changeMobilePane(key)}>{labels?.[key] ?? label}</Button>)}</nav><Button type="text" size="small" onClick={reset}>恢复默认布局</Button></div>
    <div className="resizable-workbench" ref={board} style={{ gridTemplateColumns: `${fitted.history}px 10px minmax(0,1fr) 10px ${fitted.chat}px` }}>
      <aside id={`${idPrefix}-history`} aria-label={labels?.history ?? '实验历史区'} className={`workbench-pane workbench-history ${mobilePane === 'history' ? 'mobile-active' : ''}`}>{history}</aside>
      {separator('history')}
      <section id={`${idPrefix}-center`} aria-label={labels?.center ?? '实验内容区'} style={{ overflow: 'hidden' }} className={`workbench-pane workbench-center ${mobilePane === 'center' ? 'mobile-active' : ''}`}>{center}</section>
      {separator('chat')}
      <aside id={`${idPrefix}-chat`} aria-label={labels?.chat ?? '模型对话区'} className={`workbench-pane workbench-chat ${mobilePane === 'chat' ? 'mobile-active' : ''}`}>{chat}</aside>
    </div>
  </div>
}
