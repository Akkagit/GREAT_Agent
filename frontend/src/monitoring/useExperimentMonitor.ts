import { createContext, useContext, useSyncExternalStore } from 'react'
import { ExperimentMonitorSession } from './session'

export const MonitorContext = createContext<ExperimentMonitorSession | null>(null)
export function useOptionalExperimentMonitor() { return useContext(MonitorContext) }
export function useExperimentMonitor() {
  const session = useContext(MonitorContext)
  if (!session) throw new Error('ExperimentMonitorProvider is required')
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot)
  return { ...state, tasks: state.snapshot?.tasks ?? [], refresh: session.refresh }
}
