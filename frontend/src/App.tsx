import { useState } from 'react'

import './App.css'
import { api } from './api/client'
import type { ExperimentSpec } from './api/types'
import { ExperimentWorkspace } from './pages/ExperimentWorkspace'
import { RunDashboard } from './pages/RunDashboard'

const initialSpec: ExperimentSpec = {
  schema_version: '1.0',
  workflow: 'pppflt',
  scenario: { mode: 'static', frequency: 'DF', ambiguity: 'float', reset_hours: 0 },
  date_segments: [{ year: 2024, start_doy: 350, end_doy: 350 }],
  excluded_dates: [],
  station_group_id: 'default_15',
  stations: [
    'AHB0', 'BJB0', 'GDA4', 'GSE4', 'GXC4',
    'HBB2', 'HJE2', 'HNB1', 'JL18', 'LNC1',
    'QHA4', 'SCI5', 'SD10', 'SXE4', 'YNJ8',
  ],
  input_profile_id: 'local_default',
  solver_profile_id: 'great_local',
  template_profile_id: 'pppflt_base',
  concurrency: 1,
  outputs: ['flt', 'log'],
}

function App() {
  const requestedExperiment = new URLSearchParams(window.location.search).get('experiment')
  const [experimentId, setExperimentId] = useState(requestedExperiment)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const view = new URLSearchParams(window.location.search).get('view')

  async function createFirstExperiment() {
    setCreating(true)
    setError(null)
    try {
      const created = await api.createExperiment(initialSpec)
      window.history.replaceState({}, '', `?experiment=${created.id}`)
      setExperimentId(created.id)
    } catch {
      setError('无法连接本地 API。请确认 GREAT Agent 服务正在运行。')
    } finally {
      setCreating(false)
    }
  }

  if (!experimentId) {
    return (
      <main className="launch-shell">
        <section className="launch-panel" aria-labelledby="launch-title">
          <div className="launch-orbit" aria-hidden="true"><i /></div>
          <div className="launch-copy">
            <p className="launch-kicker">GREAT Agent · 本地 PPP-FLT 编排台</p>
            <h1 id="launch-title">建立首个 PPP-FLT 实验</h1>
            <p>从已配置的 2024 年 DOY 350 与 15 个测站开始。创建只写入本地计划数据库，不会启动 GREAT。</p>
            {error && <div className="launch-error" role="alert">{error}</div>}
            <button className="primary-button launch-action" disabled={creating} onClick={() => void createFirstExperiment()}>
              {creating ? '正在创建…' : '创建实验并进入规划台'}
            </button>
          </div>
          <dl className="launch-readout">
            <div><dt>日期</dt><dd>2024 · DOY 350</dd></div>
            <div><dt>场景</dt><dd>静态 · DF · Float</dd></div>
            <div><dt>测站</dt><dd>15</dd></div>
            <div><dt>执行</dt><dd>保持关闭</dd></div>
          </dl>
        </section>
      </main>
    )
  }

  return view === 'run' ? <RunDashboard experimentId={experimentId} /> : <ExperimentWorkspace experimentId={experimentId} />
}

export default App
