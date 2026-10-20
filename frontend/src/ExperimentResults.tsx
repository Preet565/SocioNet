import { useEffect, useMemo, useState } from 'react'
import { BuilderField } from './ExperimentConfigForm'
import { multiExperimentApi } from './multiExperimentApi'
import { formatMetric, metricKeys, metricTimeline, numericMetric } from './multiExperimentHelpers'
import type { MultiExperimentArmDetail, MultiExperimentDetail } from './multiExperimentTypes'

export function ExperimentResults({ run, offline }: { run: MultiExperimentDetail; offline: boolean }) {
  const [selectedArm, setSelectedArm] = useState(run.arms.find((arm) => arm.id !== run.baseline_arm_id)?.id ?? run.baseline_arm_id)
  const [metric, setMetric] = useState('exposure_rate')
  const [arms, setArms] = useState<Record<string, MultiExperimentArmDetail>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [retry, setRetry] = useState(0)
  const [fromTick, setFromTick] = useState('')
  const [toTick, setToTick] = useState('')
  const [limit, setLimit] = useState(200)
  const [filters, setFilters] = useState<{ fromTick?: number; toTick?: number; limit: number }>({ limit: 200 })
  const baseline = run.arms.find((arm) => arm.id === run.baseline_arm_id)
  const selected = run.arms.find((arm) => arm.id === selectedArm)
  const baseData = arms[run.baseline_arm_id]
  const selectedData = arms[selectedArm]
  const rangeInvalid = (fromTick !== '' && (!Number.isInteger(Number(fromTick)) || Number(fromTick) < 0)) || (toTick !== '' && (!Number.isInteger(Number(toTick)) || Number(toTick) < 0)) || (fromTick !== '' && toTick !== '' && Number(fromTick) > Number(toTick))

  useEffect(() => {
    if (offline) return
    const controller = new AbortController()
    setLoading(true); setErrors({}); setArms({})
    const ids = [...new Set([selectedArm, run.baseline_arm_id])].filter(Boolean)
    void Promise.all(ids.map(async (armId) => {
      try {
        const result = await multiExperimentApi.arm(run.id, armId, { ...filters, eventLimit: filters.limit, signal: controller.signal })
        if (!controller.signal.aborted) setArms((current) => ({ ...current, [armId]: result }))
      } catch (error) {
        if (!controller.signal.aborted) setErrors((current) => ({ ...current, [armId]: error instanceof Error ? error.message : 'Could not load branch data.' }))
      }
    })).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [run, selectedArm, filters, offline, retry])

  const keys = useMemo(() => metricKeys(...run.arms.map((arm) => arm.metrics), ...Object.values(arms).flatMap((arm) => arm.samples.map((sample) => sample.metrics))), [run, arms])
  const activeMetric = keys.includes(metric) ? metric : keys[0] ?? ''

  return <div className="builder-results">
    {run.error && <div className="builder-alert error" role="alert">Run error: {run.error}</div>}
    <dl className="builder-provenance"><div><dt>Run ID</dt><dd>{run.id}</dd></div><div><dt>Source snapshot / tick</dt><dd>{run.source_snapshot_id ?? 'not returned'} / {run.source_tick}</dd></div><div><dt>Configuration hash</dt><dd>{run.config_hash}</dd></div><div><dt>Immutable source hash</dt><dd>{run.source_hash}</dd></div></dl>
    <p className="builder-note">Baseline: <strong>{baseline?.name ?? run.baseline_arm_id}</strong>. Deltas below are server-reported arm minus baseline. Missing measurements are shown as —.</p>
    <div className="builder-arm-results">{run.arms.map((arm) => <article key={arm.id} className={`builder-arm-result ${arm.id === run.baseline_arm_id ? 'is-baseline' : ''}`}><div className="builder-subhead"><h4>{arm.name}</h4><span className={`status-pill ${arm.status === 'completed' ? 'green' : arm.status.includes('fail') ? 'red' : 'amber'}`}>{arm.status}</span></div><p className="builder-note">{arm.id}{arm.id === run.baseline_arm_id ? ' · baseline' : ''} · final snapshot {arm.final_snapshot_id ?? 'not returned'}</p><p className="builder-note">Initial hash: <code>{arm.initial_snapshot_hash ?? 'not returned'}</code></p>{arm.error && <p className="builder-arm-error" role="alert">{arm.error}</p>}<details><summary>Earliest divergence / {arm.name}</summary>{arm.earliest_divergence ? <pre className="builder-code">{JSON.stringify(arm.earliest_divergence, null, 2)}</pre> : <p className="builder-note">{arm.status === 'completed' ? 'No divergence reported.' : 'No divergence data returned yet.'}</p>}</details></article>)}</div>
    <details open className="builder-result-section"><summary>Final metrics & baseline deltas</summary>{keys.length ? <div className="builder-table-scroll" tabIndex={0} role="region" aria-label="Final arm metrics table, scroll horizontally"><table className="builder-table"><caption>Recorded final metrics. Δ is relative to {baseline?.name ?? run.baseline_arm_id}.</caption><thead><tr><th scope="col">Metric</th>{run.arms.map((arm) => <th scope="col" key={arm.id}>{arm.name}{arm.id === run.baseline_arm_id ? ' (baseline)' : ''}</th>)}</tr></thead><tbody>{keys.map((key) => <tr key={key}><th scope="row">{key.replaceAll('_', ' ')}</th>{run.arms.map((arm) => <td key={arm.id}>{formatMetric(numericMetric(arm.metrics, key))}{arm.id !== run.baseline_arm_id && <small>Δ {formatMetric(arm.metric_deltas?.[key], true)}</small>}</td>)}</tr>)}</tbody></table></div> : <p className="builder-empty">No final scalar metrics have been returned for these arms.</p>}</details>

    <section className="builder-result-section" aria-labelledby="builder-series-title"><h4 id="builder-series-title">Branch timeline & event log</h4>
      <div className="builder-grid three"><BuilderField label="Branch to inspect"><select value={selectedArm} onChange={(event) => setSelectedArm(event.target.value)}>{run.arms.map((arm) => <option key={arm.id} value={arm.id}>{arm.name}{arm.id === run.baseline_arm_id ? ' (baseline)' : ''}</option>)}</select></BuilderField><BuilderField label="Timeline metric"><select value={activeMetric} disabled={!keys.length} onChange={(event) => setMetric(event.target.value)}>{!keys.length && <option value="">No measured metrics</option>}{keys.map((key) => <option key={key} value={key}>{key.replaceAll('_', ' ')}</option>)}</select></BuilderField></div>
      <form className="builder-filters" onSubmit={(event) => { event.preventDefault(); if (!rangeInvalid) setFilters({ fromTick: fromTick === '' ? undefined : Number(fromTick), toTick: toTick === '' ? undefined : Number(toTick), limit }) }}>
        <BuilderField label="From recorded tick (optional)"><input type="number" min={0} step={1} value={fromTick} onChange={(event) => setFromTick(event.target.value)} /></BuilderField>
        <BuilderField label="To recorded tick (optional)"><input type="number" min={0} step={1} value={toTick} onChange={(event) => setToTick(event.target.value)} /></BuilderField>
        <BuilderField label="Sample / event limit"><select value={limit} onChange={(event) => setLimit(Number(event.target.value))}><option value={50}>50</option><option value={100}>100</option><option value={200}>200</option></select></BuilderField>
        <button className="button small" type="submit" disabled={offline || loading || rangeInvalid}>Apply range</button>
      </form>
      {rangeInvalid && <p className="builder-arm-error" role="alert">Use non-negative integer ticks with from tick ≤ to tick.</p>}
      {offline && <p className="builder-note" role="status">Offline: reconnect to retrieve branch history.</p>}
      {loading && <p className="builder-empty" role="status">Loading selected branch and baseline measurements…</p>}
      {Object.entries(errors).map(([id, error]) => <div className="builder-alert error" role="alert" key={id}><strong>{run.arms.find((arm) => arm.id === id)?.name ?? id}</strong><p>{error}</p><button className="text-button" disabled={offline} onClick={() => setRetry((value) => value + 1)}>Retry branch data</button></div>)}
      {!loading && (selectedData || baseData) && <Timeline metric={activeMetric} selected={selectedData} baseline={selectedArm === run.baseline_arm_id ? undefined : baseData} />}
      {!loading && selectedData && <>
        <h4>Recorded actions / {selected?.name ?? selectedArm}</h4><p className="builder-note">{selectedData.events.length} events returned · limit {filters.limit}. Narrow the tick range to inspect dense logs.</p>
        {selectedData.events.length ? <ol className="builder-events">{selectedData.events.map((event, index) => <li key={`${event.event_id}-${index}`}><div><span className="status-pill violet">T{event.tick}</span><strong>{event.action.replaceAll('_', ' ')}</strong><span>{event.actor_id}{event.target_id ? ` → ${event.target_id}` : ''}</span></div><p>{event.details}</p><small>{event.event_id}{event.topic ? ` · ${event.topic}` : ''}{event.claim_id ? ` · claim ${event.claim_id}` : ''}{event.content_id ? ` · content ${event.content_id}` : ''}</small></li>)}</ol> : <p className="builder-empty">No action events were returned for this arm and tick range.</p>}
        <details><summary>Recorded final branch state / {selectedData.name}</summary><pre className="builder-code">{selectedData.state ? JSON.stringify(selectedData.state, null, 2) : 'No state returned.'}</pre></details>
      </>}
    </section>
  </div>
}

function Timeline({ metric, selected, baseline }: { metric: string; selected?: MultiExperimentArmDetail; baseline?: MultiExperimentArmDetail }) {
  const series = [selected, baseline].filter((arm): arm is MultiExperimentArmDetail => Boolean(arm))
  const timelines = series.map((arm) => metricTimeline(arm.samples, metric))
  const ticks = [...new Set(timelines.flatMap((timeline) => timeline.ticks))].sort((a, b) => a - b)
  const values = timelines.flatMap((timeline) => timeline.segments.flatMap((segment) => segment.map((point) => point.value)))
  if (!values.length) return <p className="builder-empty">No numeric samples were recorded for this metric and range.</p>
  const min = Math.min(...values)
  const max = Math.max(...values)
  const padding = Math.max((max - min) * 0.1, Math.abs(max) * 0.05, 0.01)
  const low = min - padding
  const high = max + padding
  const x = (tick: number) => 65 + (tick - ticks[0]) / Math.max(1, ticks[ticks.length - 1] - ticks[0]) * 635
  const y = (value: number) => 215 - (value - low) / (high - low) * 185
  return <div className="builder-timeline">
    <svg viewBox="0 0 740 250" role="img" aria-label={`${metric.replaceAll('_', ' ')} recorded timeline for ${series.map((arm) => arm.name).join(' and ')}`}>
      {[low, (low + high) / 2, high].map((value, index) => <g key={index}><line className="builder-chart-grid" x1={65} x2={700} y1={y(value)} y2={y(value)} /><text x={55} y={y(value) + 4} textAnchor="end">{formatMetric(value)}</text></g>)}
      {timelines.map((timeline, index) => <g key={series[index].id} className={index === 0 ? 'builder-series-branch' : 'builder-series-baseline'}>{timeline.segments.map((line, segmentIndex) => <g key={segmentIndex}><polyline fill="none" strokeWidth={2.5} points={line.map((point) => `${x(point.tick)},${y(point.value)}`).join(' ')} />{line.map((point) => <circle key={point.tick} cx={x(point.tick)} cy={y(point.value)} r={4}><title>{series[index].name} · T{point.tick}: {formatMetric(point.value)}</title></circle>)}</g>)}</g>)}
      {ticks.filter((_, index) => ticks.length <= 11 || index % 2 === 0).map((tick) => <text key={tick} x={x(tick)} y={240} textAnchor="middle">T{tick}</text>)}
    </svg>
    <div className="builder-chart-legend">{series.map((arm, index) => <span key={arm.id}><i className={index === 0 ? 'branch' : 'baseline'} />{arm.name}</span>)}</div>
    <details open><summary>Accessible time-series table / {metric.replaceAll('_', ' ')}</summary><div className="builder-table-scroll" tabIndex={0} role="region" aria-label="Recorded timeline data"><table className="builder-table"><caption>Persisted per-tick {metric.replaceAll('_', ' ')}; missing samples are not interpolated.</caption><thead><tr><th scope="col">Recorded tick</th>{series.map((arm) => <th scope="col" key={arm.id}>{arm.name}</th>)}</tr></thead><tbody>{ticks.map((tick) => <tr key={tick}><th scope="row">{tick}</th>{series.map((arm) => <td key={arm.id}>{formatMetric(numericMetric(arm.samples.find((sample) => sample.tick === tick)?.metrics, metric))}</td>)}</tr>)}</tbody></table></div></details>
  </div>
}
