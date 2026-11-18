import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ChatPanel } from '../components/ChatPanel'
import { PlanDiff } from '../components/PlanDiff'
import type { Proposal } from '../api/types'

function renderAssistant(text: string) {
  const page = render(<ChatPanel messages={[{ role: 'assistant', text }]} busy={false} onSend={async () => {}} />)
  return page.container.querySelector('.chat-bubble.assistant') as HTMLElement
}

describe('assistant Markdown', () => {
  it('renders formatted replies as semantic blocks and preserves literal fenced code', () => {
    const bubble = renderAssistant('## 检查结果\n\n**原因：** 当前使用 `DF`。\n\n1. 检查输入\n2. 修改设置\n\n- 保留原配置\n\n```xml\n<config>**literal**</config>\n```')
    expect(within(bubble).getByRole('heading', { name: '检查结果', level: 2 })).toBeInTheDocument()
    expect(within(bubble).getByText('原因：', { selector: 'strong' })).toBeInTheDocument()
    expect(within(bubble).getByText('DF', { selector: 'code' })).toBeInTheDocument()
    expect(bubble.querySelectorAll('ol > li')).toHaveLength(2)
    expect(bubble.querySelectorAll('ul > li')).toHaveLength(1)
    expect(bubble.querySelector('pre > code')?.textContent).toBe('<config>**literal**</config>\n')
    expect(bubble.querySelector('config')).toBeNull()
  })

  it('supports Chinese emphasis directly beside punctuation and Chinese text', () => {
    const bubble = renderAssistant('**原因：**当前配置。这里是**“双频”**设置。')
    expect(within(bubble).getByText('原因：', { selector: 'strong' })).toBeInTheDocument()
    expect(within(bubble).getByText('“双频”', { selector: 'strong' })).toBeInTheDocument()
  })

  it('renders GFM tables, task lists and links', () => {
    const bubble = renderAssistant('| 参数 | 值 |\n| --- | --- |\n| 频率 | ~~TF~~ DF |\n\n- [x] 输入已检查\n\n[官方说明](https://example.com/docs)')
    expect(within(bubble).getByRole('table')).toBeInTheDocument()
    expect(within(bubble).getByRole('cell', { name: '频率' })).toBeInTheDocument()
    expect(within(bubble).getByText('TF', { selector: 'del' })).toBeInTheDocument()
    expect(within(bubble).getByRole('checkbox')).toBeChecked()
    expect(within(bubble).getByRole('checkbox')).toBeDisabled()
    expect(within(bubble).getByRole('link', { name: '官方说明' })).toHaveAttribute('href', 'https://example.com/docs')
  })

  it('keeps user messages literal, including Markdown markers and HTML', () => {
    const text = '**原样**\n`DF`\n<script>window.bad = true</script>'
    const page = render(<ChatPanel messages={[{ role: 'user', text }]} busy={false} onSend={async () => {}} />)
    const bubble = page.container.querySelector('.chat-bubble.user') as HTMLElement
    expect(bubble.querySelector('p')?.textContent).toBe(text)
    expect(bubble.querySelector('strong, code, script')).toBeNull()
  })

  it('ignores raw HTML and rejects executable Markdown URLs', () => {
    const bubble = renderAssistant('<script>window.bad = true</script>\n\n<img src="x" onerror="window.bad = true">\n\n[危险链接](javascript:alert%281%29)\n\n[安全链接](https://example.com)')
    expect(bubble.querySelector('script, img, iframe, [onerror]')).toBeNull()
    expect(bubble.querySelector('a[href^="javascript:"]')).toBeNull()
    expect(within(bubble).getByRole('link', { name: '安全链接' })).toHaveAttribute('href', 'https://example.com')
  })

  it('formats proposal explanations while leaving exact diff values literal', () => {
    const proposal = {
      explanation: '**修改原因：**使用 `DF`。',
      diff: [{ path: '/scenario/frequency', before: '**TF**', after: 'DF' }],
    } as Proposal
    render(<PlanDiff proposal={proposal} busy={false} onAccept={async () => {}} onReject={() => {}} />)
    expect(screen.getByText('修改原因：', { selector: 'strong' })).toBeInTheDocument()
    expect(screen.getByText('DF', { selector: 'code' })).toBeInTheDocument()
    expect(screen.getByText('**TF** → DF')).toBeInTheDocument()
  })
})
