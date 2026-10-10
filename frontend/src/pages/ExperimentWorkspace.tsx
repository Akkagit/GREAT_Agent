import { useCallback, useEffect, useState } from 'react'

import { api } from '../api/client'
import type { ExperimentSpec, ExperimentView, Proposal, ValidationResult } from '../api/types'
import { ChatPanel, type ChatMessage } from '../components/ChatPanel'
import { PlanCards } from '../components/PlanCards'
import { PlanDiff } from '../components/PlanDiff'
import './ExperimentWorkspace.css'

interface ExperimentWorkspaceProps { experimentId: string }

function errorCode(error: unknown): string | undefined {
  return typeof error === 'object' && error !== null && 'code' in error ? String(error.code) : undefined
}

export function ExperimentWorkspace({ experimentId }: ExperimentWorkspaceProps) {
  const [experiment, setExperiment] = useState<ExperimentView | null>(null)
  const [draft, setDraft] = useState<ExperimentSpec | null>(null)
  const [proposal, setProposal] = useState<Proposal | null>(null)
  const [validation, setValidation] = useState<ValidationResult | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadExperiment = useCallback(async () => {
    const latest = await api.getExperiment(experimentId)
    setExperiment(latest)
    setDraft(latest.spec)
    setProposal(null)
    setValidation(null)
  }, [experimentId])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect -- fetch completion synchronizes remote experiment state
    loadExperiment().catch(() => setError('无法读取计划。确认本地服务正在运行。')).finally(() => setBusy(false))
  }, [loadExperiment])

  async function sendMessage(message: string) {
    setBusy(true); setError(null)
    setMessages((current) => [...current, { role: 'user', text: message }])
    try {
      const next = await api.proposeChat(experimentId, message)
      setProposal(next)
      setMessages((current) => [...current, { role: 'assistant', text: next.explanation ?? '已生成一份待审批修改。' }])
    } catch { setError('模型提案失败。你仍可直接编辑计划卡片。') }
    finally { setBusy(false) }
  }

  async function previewCards() {
    if (!draft) return
    setBusy(true); setError(null)
    try { setProposal(await api.previewProposal(experimentId, { source: 'card', candidate: draft })) }
    catch { setError('计划字段未通过校验。检查日期、测站和并发设置。') }
    finally { setBusy(false) }
  }

  async function acceptProposal() {
    if (!proposal || !experiment) return
    setBusy(true); setError(null)
    try {
      const revision = await api.acceptRevision(experimentId, { proposal_id: proposal.id, expected_revision: experiment.current_revision })
      setExperiment({ ...experiment, current_revision: revision.number, spec: revision.spec })
      setDraft(revision.spec); setProposal(null); setValidation(null)
    } catch (caught) {
      if (errorCode(caught) === 'revision_conflict') {
        setError('计划已被更新，已重新载入最新 revision。请重新预览修改。')
        await loadExperiment()
      } else { setError('接受修改失败。当前计划没有被覆盖。') }
    } finally { setBusy(false) }
  }

  async function validate() {
    setBusy(true); setError(null)
    try { setValidation(await api.validateExperiment(experimentId)) }
    catch { setError('输入检查未完成。冻结仍保持禁用。') }
    finally { setBusy(false) }
  }

  async function freeze() {
    if (!validation?.ok) return
    setBusy(true)
    try {
      await api.freezeExperiment(experimentId)
      setExperiment((current) => current ? { ...current, status: 'ready' } : current)
    } catch { setError('冻结失败。计划仍可编辑，请检查 revision 状态。') }
    finally { setBusy(false) }
  }

  if (!experiment || !draft) {
    if (error) return <main className="workspace-loading" role="alert">{error}</main>
    return <main className="workspace-loading" aria-busy="true">正在读取实验计划…</main>
  }
  const frozen = experiment.status === 'ready'

  return (
    <main className="workspace-shell">
      <header className="workspace-header">
        <div className="brand-lockup"><span className="brand-symbol" aria-hidden="true">G</span><div><strong>GREAT Agent</strong><span>PPP-FLT 本地编排台</span></div></div>
        <div className="revision-status"><span>Revision {experiment.current_revision}</span><strong className={frozen ? 'frozen' : 'draft'}>{frozen ? '已冻结' : '草稿'}</strong></div>
      </header>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="workspace-columns">
        <ChatPanel messages={messages} busy={busy} onSend={sendMessage} />
        <PlanCards spec={draft} disabled={busy || frozen} onChange={setDraft} onPreview={previewCards} />
        <PlanDiff proposal={proposal} busy={busy} onAccept={acceptProposal} onReject={() => setProposal(null)} />
      </div>
      <footer className="preflight-bar">
        <div><strong>执行门禁</strong>
          {validation === null && <span>尚未检查本地输入</span>}
          {validation?.ok && <span className="validation-ok">全部输入已就绪</span>}
          {validation && !validation.ok && <span className="validation-failed">缺少 {validation.missing.length} 项：{validation.missing[0]?.station ?? validation.missing[0]?.kind}</span>}
        </div>
        <div className="gate-actions">
          <button className="secondary-button" disabled={busy || frozen} onClick={validate} type="button">检查输入</button>
          <button className="freeze-button" disabled={busy || frozen || !validation?.ok} onClick={freeze} type="button">冻结计划</button>
        </div>
      </footer>
    </main>
  )
}
