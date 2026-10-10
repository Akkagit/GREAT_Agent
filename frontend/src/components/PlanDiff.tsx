import type { FieldDiff, Proposal } from '../api/types'

interface PlanDiffProps {
  proposal: Proposal | null
  busy: boolean
  onAccept: () => Promise<void>
  onReject: () => void
}

function displayValue(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : JSON.stringify(value)
}

function fieldName(diff: FieldDiff): string {
  return diff.path.split('/').filter(Boolean).at(-1) ?? 'plan'
}

export function PlanDiff({ proposal, busy, onAccept, onReject }: PlanDiffProps) {
  return (
    <aside className="diff-panel" aria-labelledby="diff-heading">
      <header className="panel-heading"><div><h2 id="diff-heading">修改审批</h2><p>只有接受后才会生成新 revision。</p></div></header>
      {proposal === null ? (
        <div className="diff-empty">
          <div className="orbit-mark" aria-hidden="true"><i /></div>
          <strong>等待提案</strong><p>对话或卡片编辑产生的差异会出现在这里。</p>
        </div>
      ) : (
        <div className="diff-content">
          {proposal.explanation && <p className="proposal-note">{proposal.explanation}</p>}
          <div className="diff-list">
            {proposal.diff.map((diff) => (
              <article className="diff-item" key={diff.path}>
                <h3>{fieldName(diff)}</h3>
                <p><span className="diff-summary">{displayValue(diff.before)} → {displayValue(diff.after)}</span></p>
              </article>
            ))}
          </div>
          <div className="approval-actions">
            <button className="primary-button" disabled={busy} onClick={onAccept} type="button">接受修改</button>
            <button className="text-button" disabled={busy} onClick={onReject} type="button">放弃提案</button>
          </div>
        </div>
      )}
    </aside>
  )
}
