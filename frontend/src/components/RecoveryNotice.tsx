import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Space } from 'antd'
import { api } from '../api/client'
import type { MonitorTask } from '../api/types'

export function RecoveryNotice({ task }: { task: MonitorTask }) {
  const [actions, setActions] = useState<string[]>([]), [busy, setBusy] = useState(false), [notice, setNotice] = useState<string | null>(null)
  const { id, active_attempt_id: attemptId, recovery } = task
  const alive = useRef(false)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => {
    let disposed = false
    if (recovery.state !== 'observing' && recovery.state !== 'blocked') return
    api.getTaskRecovery(id).then(value => { if (!disposed) setActions(Array.isArray(value.allowed_actions) ? value.allowed_actions : []) }).catch(() => { if (!disposed) setActions([]) })
    return () => { disposed = true }
  }, [id, attemptId, recovery.state, recovery.version])
  if (recovery.state !== 'observing' && recovery.state !== 'blocked') return null
  async function act(kind: 'recheck' | 'stop') {
    if (!attemptId || busy) return
    setBusy(true)
    try { await (kind === 'recheck' ? api.recheckTaskRecovery : api.stopTaskRecovery)(id, { expected_attempt_id: attemptId, request_id: crypto.randomUUID() }); if (alive.current) setNotice(kind === 'recheck' ? '已请求重新检测，等待 Worker 复核。' : '已请求停止，等待 Worker 核验身份并确认退出。') }
    catch (error) { if (alive.current) setNotice(error instanceof Error ? error.message : '恢复操作未完成，请重新检测。') }
    finally { if (alive.current) setBusy(false) }
  }
  return <Alert type="warning" title={recovery.state === 'observing' ? '遗留进程仍运行，正在观察' : '任务恢复受阻'} description={<><p>{recovery.reason ?? '等待 Worker 复核进程身份。'}</p>{recovery.last_checked_at && <p>最近检查：{recovery.last_checked_at}</p>}<Space>{actions.includes('recheck') && <Button disabled={busy} onClick={() => void act('recheck')}>重新检测</Button>}{actions.includes('stop') && <Button danger disabled={busy} onClick={() => void act('stop')}>停止遗留任务</Button>}</Space>{notice && <p role="status">{notice}</p>}</>} />
}
