import { type FormEvent, useState } from 'react'

export interface ChatMessage { role: 'user' | 'assistant'; text: string }

interface ChatPanelProps {
  messages: ChatMessage[]
  busy: boolean
  onSend: (message: string) => Promise<void>
}

export function ChatPanel({ messages, busy, onSend }: ChatPanelProps) {
  const [message, setMessage] = useState('')

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
        <div><h2 id="chat-heading">规划对话</h2><p>模型只提出修改，接受权始终在你。</p></div>
        <span className="signal-dot" aria-label="模型通道就绪" />
      </header>
      <div className="chat-history" aria-live="polite">
        {messages.length === 0 ? (
          <div className="empty-message">
            <strong>描述你要运行的解算。</strong>
            <span>例如：把日期改成 2024 年 DOY 350–360，并使用三频固定解。</span>
          </div>
        ) : messages.map((item, index) => (
          <div className={`chat-bubble ${item.role}`} key={`${item.role}-${index}`}>
            <span>{item.role === 'user' ? '你' : 'Agent'}</span><p>{item.text}</p>
          </div>
        ))}
      </div>
      <form className="composer" onSubmit={submit}>
        <label htmlFor="message">Message</label>
        <textarea id="message" value={message} onChange={(event) => setMessage(event.target.value)} placeholder="用自然语言调整当前计划…" rows={3} />
        <button className="primary-button" disabled={busy || !message.trim()} type="submit">{busy ? '生成中…' : '发送提案'}</button>
      </form>
    </section>
  )
}
