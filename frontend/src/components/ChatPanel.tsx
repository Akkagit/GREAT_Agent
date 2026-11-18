import { type FormEvent, type ReactNode, useEffect, useRef, useState } from 'react'
import type { ProposalTimings } from '../api/types'
import { Button, Input } from 'antd'
import { MarkdownContent } from './MarkdownContent'

export interface ChatMessage { id?: string; requestId?: string; role: 'user' | 'assistant'; text: string; cardId?: string }
export interface ChatProgress {
  status: 'generating' | 'completed' | 'cancelled' | 'failed'
  elapsedMs: number
  timings?: Partial<ProposalTimings>
  requestId?: string
  errorMessage?: string
}

interface ChatPanelProps {
  messages: ChatMessage[]
  busy: boolean
  onSend: (message: string) => Promise<void>
  progress?: ChatProgress | null
  onCancel?: () => void
  renderCard?: (id: string) => ReactNode
  readOnly?: boolean
  executionReady?: boolean
}

export function ChatPanel({ messages, busy, onSend, progress, onCancel, renderCard, readOnly, executionReady }: ChatPanelProps) {
  const [message, setMessage] = useState('')
  const history = useRef<HTMLDivElement>(null)
  useEffect(() => { if (history.current) history.current.scrollTop = history.current.scrollHeight }, [messages])

  async function submit(event: FormEvent) {
    event.preventDefault()
    const trimmed = message.trim()
    if (!trimmed || busy) return
    setMessage('')
    await onSend(trimmed)
  }

  return (
    <section className="chat-panel" aria-labelledby="chat-heading">
      <header className="panel-heading">
        <div><h2 id="chat-heading">对话</h2><p>{executionReady ? '计划已冻结；核对当前 XML 后，可在对话中明确授权执行。仍可咨询，修改请新建实验。' : readOnly ? '计划修改已锁定，仍可咨询；修改请新建实验。' : '可咨询 GNSS 理论或调整当前计划；新实验可在首页全局对话中创建。'}</p><p>理论回答基于模型知识；论文检索尚未启用。</p></div>
        <span className="signal-dot" aria-label="模型通道就绪" />
      </header>
      <div className="chat-history" aria-live="polite" ref={history}>
        {messages.length === 0 ? (
          <div className="empty-message">
            <strong>聊聊 GNSS 理论或当前计划；也可在首页全局对话中创建实验。</strong>
            <span>理论回答来自模型知识；论文检索尚未启用。</span>
          </div>
        ) : messages.map((item, index) => (
          <div className={`chat-bubble ${item.role}`} key={item.id ?? `${item.role}-${index}`}>
            <span>{item.role === 'user' ? '你' : 'Agent'}</span>
            {item.role === 'assistant'
              ? <MarkdownContent className="chat-message-text">{item.text}</MarkdownContent>
              : <p className="chat-message-text">{item.text}</p>}
            {item.cardId && renderCard?.(item.cardId)}
          </div>
        ))}
      </div>
      <form className="composer" onSubmit={submit}>
        <label htmlFor="message">Message</label>
        <Input.TextArea id="message" value={message} onChange={(event) => setMessage(event.target.value)} placeholder="输入问题或需求；首页全局对话支持创建实验…" rows={3} />
        {progress && <div className="chat-request-status">
          <p role="status">{progress.status === 'generating'
            ? `正在回复，已等待 ${Math.floor(progress.elapsedMs / 1000)} 秒`
            : progress.status === 'cancelled' ? '已取消等待；服务端可能已处理，请查看记录和实验进度确认结果。'
            : progress.status === 'failed' ? '回复失败，请查看详情'
            : `回复已完成，用时 ${(progress.elapsedMs / 1000).toFixed(1)} 秒`}</p>
          {progress.status === 'failed' && <>{progress.errorMessage && <p>{progress.errorMessage}</p>}{progress.requestId && <small>请求编号：{progress.requestId}</small>}</>}
          {progress.status === 'generating' && <small>等待服务端完整回复；执行授权以服务端确认结果为准。</small>}
          {progress.status === 'completed' && progress.timings && <details className="chat-timings">
            <summary>耗时明细</summary>
            <dl>{([
              ['读取计划', progress.timings.plan_load_ms],
              ['模型请求（含网络与生成）', progress.timings.model_request_ms],
              ['校验与差异计算', progress.timings.validation_ms],
              ['API 总耗时', progress.timings.api_total_ms],
              ['浏览器往返', progress.elapsedMs],
            ] as const).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{((value ?? 0) / 1000).toFixed(3)} 秒</dd></div>)}</dl>
            {progress.requestId && <small>请求编号：{progress.requestId}</small>}
          </details>}
        </div>}
        <div className={progress?.status === 'generating' ? 'chat-send-actions' : undefined}>
          <Button aria-label={busy ? '正在回复…' : '发送'} type="primary" disabled={busy || !message.trim()} htmlType="submit">{progress?.status === 'generating' ? '正在回复…' : '发送'}</Button>
          {progress?.status === 'generating' && onCancel && <Button htmlType="button" onClick={onCancel}>取消回复</Button>}
        </div>
      </form>
    </section>
  )
}
