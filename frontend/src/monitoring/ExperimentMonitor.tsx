import { useEffect, useMemo, type ReactNode } from 'react'
import { api } from '../api/client'
import { ExperimentMonitorSession } from './session'
import { MonitorContext } from './useExperimentMonitor'

export function ExperimentMonitorProvider({ experimentId, children }: { experimentId: string; children: ReactNode }) {
  const session = useMemo(() => new ExperimentMonitorSession(experimentId, { getSnapshot: api.getExperimentMonitor, open: api.openExperimentEvents }), [experimentId])
  useEffect(() => { session.start(); return () => session.stop() }, [session])
  return <MonitorContext value={session}>{children}</MonitorContext>
}
