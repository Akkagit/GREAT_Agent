import { ExperimentWorkspace } from './pages/ExperimentWorkspace'

function App() {
  const experimentId = new URLSearchParams(window.location.search).get('experiment') ?? 'local'
  return <ExperimentWorkspace experimentId={experimentId} />
}

export default App
