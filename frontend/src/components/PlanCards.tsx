import type { ExperimentSpec, Frequency } from '../api/types'

interface PlanCardsProps {
  spec: ExperimentSpec
  disabled: boolean
  onChange: (spec: ExperimentSpec) => void
  onPreview: () => Promise<void>
}

export function PlanCards({ spec, disabled, onChange, onPreview }: PlanCardsProps) {
  const segment = spec.date_segments[0]
  function updateScenario(key: keyof ExperimentSpec['scenario'], value: string | number) {
    onChange({ ...spec, scenario: { ...spec.scenario, [key]: value } })
  }

  return (
    <section className="plan-editor" aria-labelledby="plan-heading">
      <div className="plan-title-row">
        <div><h2 id="plan-heading">解算计划</h2><p>PPP-FLT · 每个日期一个可恢复任务</p></div>
        <button className="secondary-button" disabled={disabled} onClick={onPreview} type="button">预览修改</button>
      </div>
      <div className="doy-rail" aria-label={`DOY ${segment.start_doy} 到 ${segment.end_doy}`}>
        <span>{segment.year}</span><div className="rail-line"><i /></div><strong>{segment.start_doy}</strong><span>至</span><strong>{segment.end_doy}</strong>
      </div>
      <div className="plan-grid">
        <article className="plan-card scenario-card">
          <h3>解算场景</h3>
          <div className="field-row"><label htmlFor="mode">模式</label><select id="mode" disabled={disabled} value={spec.scenario.mode} onChange={(e) => updateScenario('mode', e.target.value)}><option value="static">静态</option><option value="kinematic">动态</option></select></div>
          <div className="field-row"><label htmlFor="frequency">频率</label><select id="frequency" disabled={disabled} value={spec.scenario.frequency} onChange={(e) => updateScenario('frequency', e.target.value as Frequency)}><option value="DF">双频 DF</option><option value="TF">三频 TF</option></select></div>
          <div className="field-row"><label htmlFor="ambiguity">模糊度</label><select id="ambiguity" disabled={disabled} value={spec.scenario.ambiguity} onChange={(e) => updateScenario('ambiguity', e.target.value)}><option value="float">浮点解</option><option value="fixed">固定解</option></select></div>
        </article>
        <article className="plan-card date-card">
          <h3>日期范围</h3><dl><div><dt>年份</dt><dd>{segment.year}</dd></div><div><dt>起始 DOY</dt><dd>{segment.start_doy}</dd></div><div><dt>结束 DOY</dt><dd>{segment.end_doy}</dd></div></dl>
        </article>
        <article className="plan-card station-card">
          <h3>测站快照</h3><p className="metric"><strong>{spec.stations.length}</strong> 个测站</p><p>{spec.station_group_id}</p>
        </article>
        <article className="plan-card input-card">
          <h3>输入配置</h3><dl><div><dt>输入</dt><dd>{spec.input_profile_id}</dd></div><div><dt>模板</dt><dd>{spec.template_profile_id}</dd></div></dl>
        </article>
        <article className="plan-card runtime-card">
          <h3>运行策略</h3><div className="field-row"><label htmlFor="concurrency">并发数</label><input id="concurrency" type="number" min="1" max="8" disabled={disabled} value={spec.concurrency} onChange={(e) => onChange({ ...spec, concurrency: Number(e.target.value) })} /></div><p>{spec.solver_profile_id}</p>
        </article>
        <article className="plan-card output-card">
          <h3>结果类型</h3><div className="output-chips">{spec.outputs.map((output) => <span key={output}>.{output}</span>)}</div><p>输出位置由本机运行目录控制。</p>
        </article>
      </div>
    </section>
  )
}
