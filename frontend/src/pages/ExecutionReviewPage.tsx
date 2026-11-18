import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { ExecutionPreview as Preview } from '../api/types'
import { ExecutionPreview } from '../components/ExecutionPreview'
import { Alert, Button } from 'antd'

interface Props { experimentId: string; previewId: string | null; onStarted: () => void; onBack: () => void }

export function ExecutionReviewPage({ experimentId, previewId, onStarted, onBack }: Props) {
  const [preview, setPreview] = useState<Preview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const submitting = useRef(false)
  const owner = useRef<object | null>(null)
  useEffect(() => { owner.current = {}; return () => { owner.current = null } }, [experimentId, previewId])
  useEffect(() => {
    let disposed = false
    if (!previewId) return
    api.getExecutionPreview(experimentId, previewId).then((value) => { if (!disposed) setPreview(value) })
      .catch((caught) => { if (!disposed) setError(caught instanceof Error ? caught.message : '无法读取执行预览，请返回计划重新生成。') })
    return () => { disposed = true }
  }, [experimentId, previewId])
  async function confirm() {
    if (!preview || submitting.current) return
    const token = owner.current
    submitting.current = true; setBusy(true); setError(null)
    try {
      await api.confirmExecution(experimentId, { preview_id: preview.id, expected_revision: preview.revision, confirmed: true })
      if (owner.current === token) onStarted()
    } catch (caught) { if (owner.current === token) setError(caught instanceof Error ? caught.message : '确认提交失败，请检查服务后重新核对。') }
    finally { if (owner.current === token) { submitting.current = false; setBusy(false) } }
  }
  if (!preview) return <main className="review-page"><h1>执行复核</h1><Alert type={error?'error':'info'} role={error?'alert':'status'} title={error ?? (previewId ? '正在读取已保存的 XML 预览…' : '请先在规划台检查输入并生成 XML。')} /><Button onClick={onBack}>返回查看计划</Button></main>
  return <main className="review-page"><ExecutionPreview key={preview.id} preview={preview} busy={busy} error={error} onConfirm={() => void confirm()} onClose={onBack} /></main>
}
