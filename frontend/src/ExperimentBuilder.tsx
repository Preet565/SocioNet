import { useEffect, useRef, useState } from 'react'
import { ApiResponseError, ApiUnavailableError } from './api'
import { BuilderField, ExperimentConfigForm } from './ExperimentConfigForm'
import { ExperimentResults } from './ExperimentResults'
import { Icon } from './icons'
import { multiExperimentApi } from './multiExperimentApi'
import { canRunExperiment, configIssues, createExperimentConfig, downloadJson, downloadJsonText, effectiveAgentTickBudget, EXPERIMENT_LIMITS, isRecord, parseExperimentJson, plannedAgentTicks, validationInput } from './multiExperimentHelpers'
import type { ExperimentManifest, MultiExperimentConfigInput, MultiExperimentDetail, MultiExperimentSummary, ValidatedExperiment } from './multiExperimentTypes'
import type { UniverseId, UniverseState } from './types'
import './experimentBuilder.css'

const json = (value: unknown) => JSON.stringify(value, null, 2)
const terminal = (status: string) => !['created', 'pending', 'queued', 'running'].includes(status)

export default function ExperimentBuilder({ universeId, offline, universeState, sourceStates, onBusyChange }: { universeId: UniverseId; offline: boolean; universeState: UniverseState; sourceStates?: Partial<Record<UniverseId, UniverseState>>; onBusyChange?: (busy: boolean) => void }) {
  const [config, setConfig] = useState<MultiExperimentConfigInput>(() => createExperimentConfig(universeId))
  const [rawConfig, setRawConfig] = useState(() => json(config))
  const [jsonDirty, setJsonDirty] = useState(false)
  const [validated, setValidated] = useState<ValidatedExperiment>()
  const [bundle, setBundle] = useState<ExperimentManifest>()
  const [bundleText, setBundleText] = useState('')
  const [importText, setImportText] = useState('')
  const [schema, setSchema] = useState<Record<string, unknown>>()
  const [schemaError, setSchemaError] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [connectionLost, setConnectionLost] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [historyUniverse, setHistoryUniverse] = useState<UniverseId>(universeId)
  const [history, setHistory] = useState<MultiExperimentSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [detail, setDetail] = useState<MultiExperimentDetail>()
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const revision = useRef(0)
  const mounted = useRef(true)
  const operation = useRef(false)
  const validatedSource = useRef('')
  const builderTop = useRef<HTMLHeadingElement>(null)
  const blocked = offline || connectionLost
  const sourceState = sourceStates?.[config.source.universe_id] ?? universeState
  const sourcePopulation = bundle && isRecord(bundle.source_snapshot.agents) ? Object.keys(bundle.source_snapshot.agents).length : config.source.mode === 'universe' ? sourceState.metrics.population : undefined
  const issues = configIssues(config, sourcePopulation)
  const sourceFingerprint = JSON.stringify({ universe: sourceState.universeId, tick: sourceState.tick, citizens: sourceState.citizens, edges: sourceState.edges, posts: sourceState.posts, interventions: sourceState.interventions })
  const staleSource = !bundle && config.source.mode === 'universe' && validated !== undefined && (sourceState.tick !== validated.plan.source_tick || validatedSource.current !== sourceFingerprint)
  const runnable = canRunExperiment(config, validated, { offline: blocked, busy: Boolean(busy), jsonDirty, staleSource, issues })

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; revision.current++ }
  }, [])

  const failure = (requestError: unknown) => {
    if (requestError instanceof ApiUnavailableError) setConnectionLost(true)
    if (requestError instanceof ApiResponseError && requestError.status === 409) {
      setValidated(undefined)
      setError(`The source changed since validation. Validate again before running. ${requestError.message}`)
      return
    }
    const message = requestError instanceof ApiResponseError && requestError.status === 404
      ? 'The multi-experiment API endpoint or saved record is unavailable (404). Check that the Phase 6 backend is running.'
      : requestError instanceof Error ? requestError.message : 'The request failed.'
    setError(message)
  }

  useEffect(() => {
    if (offline) return
    const controller = new AbortController()
    setSchemaError('')
    void multiExperimentApi.schema(controller.signal).then(setSchema).catch((requestError: unknown) => {
      if (controller.signal.aborted) return
      setSchemaError(requestError instanceof Error ? requestError.message : 'Could not load the server schema.')
      if (requestError instanceof ApiUnavailableError) setConnectionLost(true)
    })
    return () => controller.abort()
  }, [offline, refresh])

  useEffect(() => {
    if (offline) return
    const controller = new AbortController()
    setHistoryLoading(true); setHistoryError(''); setHistory([])
    void multiExperimentApi.list(historyUniverse, controller.signal).then((response) => {
      if (!controller.signal.aborted) { setHistory(response.experiments); setConnectionLost(false) }
    }).catch((requestError: unknown) => {
      if (controller.signal.aborted) return
      setHistoryError(requestError instanceof Error ? requestError.message : 'Could not load saved experiments.')
      if (requestError instanceof ApiUnavailableError) setConnectionLost(true)
    }).finally(() => { if (!controller.signal.aborted) setHistoryLoading(false) })
    return () => controller.abort()
  }, [historyUniverse, offline, refresh])

  useEffect(() => {
    if (!selectedId || offline) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    setDetailLoading(true); setDetailError('')
    const load = async () => {
      try {
        const run = await multiExperimentApi.detail(selectedId, controller.signal)
        if (controller.signal.aborted) return
        setDetail(run); setDetailError('')
        setHistory((current) => current.map((item) => item.id === run.id ? run : item))
        if (!terminal(run.status)) timer = setTimeout(() => void load(), 2000)
      } catch (requestError) {
        if (controller.signal.aborted) return
        setDetailError(requestError instanceof Error ? requestError.message : 'Could not open the run.')
        if (requestError instanceof ApiUnavailableError) setConnectionLost(true)
      } finally { if (!controller.signal.aborted) setDetailLoading(false) }
    }
    void load()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [selectedId, offline, refresh])

  const changeConfig = (next: MultiExperimentConfigInput) => {
    revision.current++
    setConfig(next); setRawConfig(json(next)); setJsonDirty(false); setValidated(undefined); setError('')
    if (bundle) setNotice('Configuration edited. The next run will use a new source; import the original bundle again for exact-source replay.')
    else setNotice('')
    setBundle(undefined)
    setBundleText('')
  }

  const loadJson = (text: string) => {
    revision.current++
    setValidated(undefined)
    try {
      const imported = parseExperimentJson(text)
      changeConfig(imported.config)
      if (imported.kind === 'bundle') { setBundle(imported.manifest); setBundleText(text) }
      setNotice(imported.kind === 'bundle' ? 'Portable bundle loaded with immutable source. Validate it on the server to preview the execution plan before running.' : 'Configuration loaded. Validate to preview the execution plan.')
      builderTop.current?.focus()
    } catch (parseError) { setError(parseError instanceof Error ? parseError.message : 'Could not parse JSON.') }
  }

  const perform = async (label: string, action: () => Promise<void>) => {
    if (operation.current || offline) return
    operation.current = true
    onBusyChange?.(true)
    setBusy(label); setError(''); setNotice('')
    try { await action() } catch (requestError) { if (mounted.current) failure(requestError) }
    finally { operation.current = false; if (mounted.current) { setBusy(''); onBusyChange?.(false) } }
  }

  const validate = () => void perform('Validating configuration', async () => {
    const version = revision.current
    const source = sourceFingerprint
    setValidated(undefined)
    const result = bundle ? await multiExperimentApi.validateBundleText(bundleText) : await multiExperimentApi.validate(validationInput(config))
    if (!mounted.current || version !== revision.current) return
    setConfig(result.config); setRawConfig(json(result.config)); setValidated(result)
    validatedSource.current = source
    setConnectionLost(false); setNotice(bundle ? 'Portable bundle validated by the server, including saved source hashes and references. Review the planned cost and arms, then run.' : 'Validated by the server. Review the planned cost and arms, then run.')
  })

  const recordRun = (run: MultiExperimentDetail) => {
    if (!mounted.current) return
    setSelectedId(run.id); setDetail(run); setHistoryUniverse(run.config.source.universe_id)
    setRefresh((value) => value + 1)
    setNotice(`Run ${run.id}: ${run.status.replaceAll('_', ' ')}. The saved result is open below.`)
    setConnectionLost(false)
    setValidated(undefined)
  }

  const run = () => {
    if (!runnable || !validated) return
    void perform(bundle ? 'Importing bundle & running' : 'Running experiment', async () => {
      recordRun(bundle ? await multiExperimentApi.importBundleText(bundleText) : await multiExperimentApi.create(validated.config, validated.plan.source_hash))
    })
  }

  const duplicate = (id: string) => void perform('Loading saved configuration', async () => {
    const saved = await multiExperimentApi.config(id)
    const imported = parseExperimentJson(json(saved))
    changeConfig(imported.config)
    setNotice('Saved configuration duplicated into the builder. Validate to run against a new source.')
    builderTop.current?.focus()
  })

  const exportRun = (id: string, mode: 'config' | 'bundle') => void perform(`Exporting ${mode}`, async () => {
    const filename = `experiment-${id}-${mode}.json`
    if (mode === 'config') downloadJson(await multiExperimentApi.config(id), filename)
    else downloadJsonText(await multiExperimentApi.bundleText(id), filename)
    setNotice(`${mode === 'config' ? 'Normalized configuration' : 'Portable source bundle'} downloaded.`)
  })

  return <section className="experiment-builder" aria-labelledby="experiment-builder-title">
    <div className="builder-title"><div><span className="eyebrow"><Icon name="flask" size={14} /> Phase 6 / design a branching experiment</span><h3 id="experiment-builder-title" ref={builderTop} tabIndex={-1}>One question. Multiple possible worlds.</h3><p>Configure two to six named arms, validate a bounded plan, and compare recorded outcomes against your baseline.</p></div><span className="status-pill violet">≤ {EXPERIMENT_LIMITS.agentTicks.toLocaleString('en-US')} agent-ticks</span></div>
    {(offline || connectionLost) && <div className="builder-alert warning" role="status"><strong>{offline ? 'Offline preview' : 'Experiment API disconnected'}</strong><p>You can edit and import configurations locally. Validation, execution, saved history, and exports require the live API.</p>{offline ? <p>Use “Sync with API” in the command-center header to reconnect.</p> : <button className="button small" onClick={() => { setConnectionLost(false); setRefresh((value) => value + 1) }}>Retry connection</button>}</div>}
    {error && <div className="builder-alert error" role="alert"><strong>Experiment request needs attention</strong><pre>{error}</pre><button className="text-button" onClick={() => setError('')}>Dismiss error</button></div>}
    <div className="builder-notice" role="status" aria-live="polite">{busy ? <><span className="spinner" /> {busy}…</> : notice}</div>
    <div className="panel builder-editor" aria-busy={Boolean(busy)}>
      <fieldset className="builder-editor-lock" disabled={Boolean(busy)}>
        <ExperimentConfigForm config={config} onChange={changeConfig} universeState={sourceState} />
        <details className="builder-section"><summary>Advanced JSON / full SimulationConfig & archetype parameters</summary>
          <p className="builder-note">Edit the complete configuration. Apply valid JSON to update the forms; the server validates all simulation parameters. Pending JSON edits disable execution.</p>
          <BuilderField label="Advanced experiment configuration JSON"><textarea className="builder-code" rows={18} spellCheck={false} value={rawConfig} onChange={(event) => { revision.current++; setRawConfig(event.target.value); setJsonDirty(true); setValidated(undefined) }} /></BuilderField>
          <div className="builder-actions"><button className="button small" disabled={!jsonDirty} onClick={() => loadJson(rawConfig)}>Apply JSON to builder</button><button className="text-button" disabled={!jsonDirty} onClick={() => { setRawConfig(json(config)); setJsonDirty(false) }}>Discard JSON edits</button><button className="text-button" onClick={() => downloadJson(config, 'experiment-draft-config.json')}>Download draft config</button></div>
          {jsonDirty && <p className="builder-note">Unapplied JSON edits. Apply or discard them before validation.</p>}
        </details>
        <details className="builder-section"><summary>Import a configuration or portable source bundle</summary>
          <BuilderField label="Import experiment JSON file"><input type="file" accept=".json,application/json" onChange={(event) => {
            const file = event.target.files?.[0]
            event.target.value = ''
            if (!file) return
            if (file.size > 32 * 1024 * 1024) { setValidated(undefined); setError('Import file exceeds 32 MB.'); return }
            const version = revision.current
            void file.text().then((text) => { if (mounted.current && version === revision.current) loadJson(text) }).catch(() => setError('Could not read the selected file.'))
          }} /></BuilderField>
          <BuilderField label="Or paste config / bundle JSON"><textarea className="builder-code" rows={6} spellCheck={false} value={importText} onChange={(event) => setImportText(event.target.value)} /></BuilderField>
          <button className="button small" disabled={!importText.trim()} onClick={() => loadJson(importText)}>Load pasted JSON</button>
        </details>
      </fieldset>
      <div className="builder-plan">
        <div className="builder-subhead"><h4>{validated && !staleSource ? 'Server-validated execution plan' : 'Draft cost estimate'}</h4><span className={`status-pill ${validated && !staleSource ? 'green' : 'amber'}`}>{validated && !staleSource ? 'validated' : 'validation required'}</span></div>
        {bundle && <p className="builder-note">Portable replay mode: the original manifest and immutable source will be submitted intact. Editing the configuration switches to a new-source run.</p>}
        <div className="builder-cost-grid"><div><span>Population</span><strong>{validated?.plan.population ?? sourcePopulation ?? config.simulation?.population ?? 'awaiting server'}</strong></div><div><span>Ticks × arms</span><strong>{validated?.plan.ticks ?? config.ticks} × {validated?.plan.arm_count ?? config.arms.length}</strong></div><div><span>Agent-ticks / effective budget</span><strong>{validated?.plan.agent_ticks ?? plannedAgentTicks(config, sourcePopulation) ?? 'awaiting server'} / {effectiveAgentTickBudget(config.computational_budget)}</strong></div><div><span>Source tick</span><strong>{validated?.plan.source_tick ?? (bundle ? 'saved source' : config.source.mode === 'generated' ? '0 (generated)' : `${sourceState.label} / awaiting validation`)}</strong></div></div>
        <p className="builder-note">Configured budget: {config.computational_budget}. Effective budget: min(2,000, configured budget).</p>
        {validated?.plan.source_hash && <p className="builder-note">Validated prepared source hash (including shared claims): <code>{validated.plan.source_hash}</code></p>}
        {validated && <ul className="builder-plan-arms">{validated.plan.arms.map((arm) => <li key={arm.id}><strong>{arm.name}</strong><span>{arm.id === validated.plan.baseline_arm_id ? 'baseline · ' : ''}{arm.intervention_count} interventions</span></li>)}</ul>}
        {validated?.plan.warnings.length ? <ul className="builder-issues">{validated.plan.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul> : null}
        {issues.length > 0 && <div className="builder-issues" role="status"><strong>Resolve before validation</strong><ul>{issues.map((issue, i) => <li key={i}>{issue}</li>)}</ul></div>}
        {staleSource && <p className="builder-note">The source universe changed since validation. Validate again to update the plan.</p>}
        <div className="builder-actions"><button className="button" onClick={validate} disabled={blocked || Boolean(busy) || jsonDirty || issues.length > 0}>{bundle ? 'Validate bundle & preview plan' : 'Validate & preview plan'}</button><button className="button primary" onClick={run} disabled={!runnable} aria-describedby="builder-run-help"><Icon name="play" size={14} />{bundle ? 'Import bundle & run' : 'Run validated experiment'}</button></div>
        <p className="builder-note" id="builder-run-help">{runnable ? bundle ? 'Ready to import the server-validated bundle with its exact saved source.' : 'Ready to run. The backend checks the validated source hash, when supplied, before saving and executing the arms.' : 'Run is enabled only for a current, server-validated configuration or bundle while connected.'}</p>
      </div>
    </div>

    <section className="panel builder-history" aria-labelledby="builder-history-title">
      <div className="panel-header"><div><h3 id="builder-history-title">Saved multi-arm experiments</h3><p>Durable server history · latest 50 runs</p></div><div className="builder-actions"><BuilderField label="History universe"><select value={historyUniverse} onChange={(event) => setHistoryUniverse(event.target.value as UniverseId)}><option value="prime">Prime</option><option value="echo">Echo</option></select></BuilderField><button className="button small ghost" disabled={offline || Boolean(busy) || historyLoading} onClick={() => setRefresh((value) => value + 1)}>Refresh history</button></div></div>
      {offline ? <p className="builder-empty">Saved experiments are available when the API reconnects.</p> : historyLoading ? <p className="builder-empty" role="status">Loading saved experiments…</p> : historyError ? <div className="builder-alert error" role="alert"><p>{historyError}</p><button className="text-button" onClick={() => setRefresh((value) => value + 1)}>Retry history</button></div> : history.length === 0 ? <p className="builder-empty">No saved multi-arm runs for {historyUniverse}. Validate and run a configuration to create one.</p> : <div className="builder-history-list">{history.map((item) => <article key={item.id} className={`builder-history-row ${selectedId === item.id ? 'selected' : ''}`}><div><strong>{item.name}</strong><span>{item.id} · {new Date(item.created_at).toLocaleString()}</span></div><span className={`status-pill ${item.status === 'completed' ? 'green' : item.status.includes('fail') ? 'red' : 'amber'}`}>{item.status.replaceAll('_', ' ')}</span><div className="builder-actions"><button className="button small" aria-pressed={selectedId === item.id} disabled={Boolean(busy)} onClick={() => { setDetail(undefined); setSelectedId(item.id); if (selectedId === item.id) setRefresh((value) => value + 1) }}>Open run<span className="sr-only"> {item.name} {item.id}</span></button><button className="text-button" disabled={Boolean(busy) || blocked} onClick={() => duplicate(item.id)}>Duplicate<span className="sr-only"> {item.name} {item.id}</span></button></div></article>)}</div>}
    </section>
    {selectedId && <section className="panel builder-saved-result" aria-label="Selected saved experiment">
      <div className="panel-header"><div><h3>Saved experiment / {detail?.name ?? selectedId}</h3><p>{detail?.status.replaceAll('_', ' ') ?? 'Retrieving record'}</p></div><button className="text-button" onClick={() => { setSelectedId(''); setDetail(undefined) }}>Close result</button></div>
      {detailLoading && <p className="builder-empty" role="status">Loading saved run…</p>}
      {detailError && <div className="builder-alert error" role="alert"><p>{detailError}</p><button className="text-button" disabled={offline} onClick={() => setRefresh((value) => value + 1)}>Retry saved run</button></div>}
      {detail && <><div className="builder-saved-actions builder-actions"><button className="button small" disabled={blocked || Boolean(busy)} onClick={() => void perform('Rerunning exact saved source', async () => recordRun(await multiExperimentApi.rerun(detail.id)))}>Rerun exact source</button><button className="text-button" disabled={blocked || Boolean(busy)} onClick={() => duplicate(detail.id)}>Duplicate into builder</button><button className="text-button" disabled={blocked || Boolean(busy)} onClick={() => exportRun(detail.id, 'config')}>Export config</button><button className="text-button" disabled={blocked || Boolean(busy)} onClick={() => exportRun(detail.id, 'bundle')}>Export portable bundle</button></div><ExperimentResults key={detail.id} run={detail} offline={blocked} /></>}
    </section>}
    <details className="builder-section builder-schema"><summary>Server JSON Schema reference</summary>{schema ? <><button className="text-button" onClick={() => downloadJson(schema, 'multi-experiment-schema.json')}>Download JSON Schema</button><pre className="builder-code">{json(schema)}</pre></> : <p className="builder-note">{offline ? 'Connect to retrieve the authoritative schema.' : schemaError || 'Loading JSON Schema…'}</p>}{schemaError && <button className="text-button" disabled={offline} onClick={() => setRefresh((value) => value + 1)}>Retry schema</button>}</details>
  </section>
}
