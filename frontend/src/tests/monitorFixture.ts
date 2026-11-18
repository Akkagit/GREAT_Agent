import type { TaskDetail, TaskSummary } from '../api/types'

export const monitorCursor = (experimentId: string, position = '0') => `v1:11111111-1111-4111-8111-111111111111:${experimentId}:${position}`
export function monitorTask(detail: TaskDetail) {
  return { ...detail, analysis_status: 'unknown' as const, artifact_version: 0, recovery: { state: 'none', reason: null, last_checked_at: null, version: 0 } }
}
export async function legacyMonitorSnapshot(id: string, list: (id: string) => Promise<TaskSummary[]>, detail?: (id: string) => Promise<TaskDetail>) {
  const tasks = await list(id)
  return { experiment_id: id, cursor: monitorCursor(id), tasks: await Promise.all(tasks.map(async task => monitorTask({ ...(detail ? await detail(task.id) : { ...task, active_attempt_id: null, attempts: [] }), ...task, experiment_id: id }))) }
}
