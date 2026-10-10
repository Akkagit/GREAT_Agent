import { ExperimentWorkspace } from './pages/ExperimentWorkspace'
import { RunDashboard } from './pages/RunDashboard'

function App() {
  const experimentId = new URLSearchParams(window.location.search).get('experiment') ?? 'local'
  const view = new URLSearchParams(window.location.search).get('view')
  return view === 'run' ? <RunDashboard experimentId={experimentId} /> : <ExperimentWorkspace experimentId={experimentId} />
}

export default App
