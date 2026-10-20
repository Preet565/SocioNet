import type { ReactNode } from 'react'
import { createExperimentConfig, DEFAULT_ARCHETYPES, EXPERIMENT_LIMITS, INTERVENTION_KINDS, newIntervention, nextConfigId, renameArm, renameClaim } from './multiExperimentHelpers'
import type { ExperimentArmConfig, ExperimentClaimConfig, ExperimentScheduleEntry, InitialNetworkConfig, InterventionKind, MultiExperimentConfigInput } from './multiExperimentTypes'
import type { UniverseState } from './types'

export function BuilderField({ label, children }: { label: string; children: ReactNode }) {
  return <label className="builder-field"><span>{label}</span>{children}</label>
}

function NumberField({ label, value, onChange, min = 0, max, step = 1, disabled }: { label: string; value: number | null | undefined; onChange: (value: number) => void; min?: number; max?: number; step?: number; disabled?: boolean }) {
  return <BuilderField label={label}><input type="number" min={min} max={max} step={step} value={value ?? ''} disabled={disabled} onChange={(event) => onChange(Number(event.target.value))} /></BuilderField>
}

export function ExperimentConfigForm({ config, onChange, universeState }: { config: MultiExperimentConfigInput; onChange: (config: MultiExperimentConfigInput) => void; universeState: UniverseState }) {
  const update = (patch: Partial<MultiExperimentConfigInput>) => onChange({ ...config, ...patch })
  const defaults = createExperimentConfig(config.source.universe_id)
  const simulation = config.simulation ?? defaults.simulation
  const network = config.simulation?.initial_network ?? {}
  const updateNetwork = (patch: Partial<InitialNetworkConfig>) => update({ simulation: { ...simulation, initial_network: { ...network, ...patch } } })
  const updateClaim = (index: number, patch: Partial<ExperimentClaimConfig>) => update({ claims: config.claims.map((claim, i) => i === index ? { ...claim, ...patch } : claim) })
  const updateArm = (index: number, patch: Partial<ExperimentArmConfig>) => update({ arms: config.arms.map((arm, i) => i === index ? { ...arm, ...patch } : arm) })
  const archetypes = [...new Set([...DEFAULT_ARCHETYPES, ...Object.keys(config.simulation?.archetypes ?? {}), ...Object.keys(config.agent_composition)])]
  const compositionTotal = Object.values(config.agent_composition).reduce((total, count) => total + count, 0)
  const universeSource = config.source.mode === 'universe'
  const citizenIds = universeSource && config.source.universe_id === universeState.universeId
    ? universeState.citizens.map((citizen) => ({ id: citizen.id, label: citizen.name }))
    : !universeSource ? Array.from({ length: Math.min(100, Math.max(0, simulation.population)) }, (_, i) => ({ id: `citizen-${String(i + 1).padStart(3, '0')}`, label: 'Generated citizen' })) : []

  return <div className="builder-config-form">
    <fieldset className="builder-section"><legend>01 / Question & source</legend>
      <div className="builder-grid two">
        <BuilderField label="Experiment name"><input value={config.name} maxLength={120} onChange={(event) => update({ name: event.target.value })} required /></BuilderField>
        <NumberField label="Seed (captured by server for universe sources)" value={config.seed} disabled={universeSource} min={Number.MIN_SAFE_INTEGER} max={Number.MAX_SAFE_INTEGER} onChange={(seed) => update({ seed })} />
        <BuilderField label="Hypothesis"><textarea rows={3} value={config.hypothesis} maxLength={2000} onChange={(event) => update({ hypothesis: event.target.value })} /></BuilderField>
        <div className="builder-grid two">
          <NumberField label="Ticks (1–20)" value={config.ticks} min={1} max={20} onChange={(ticks) => update({ ticks })} />
          <NumberField label="Configured budget / agent-ticks (up to 5,000)" value={config.computational_budget} min={2} max={EXPERIMENT_LIMITS.computationalBudget} onChange={(computational_budget) => update({ computational_budget })} />
        </div>
        <BuilderField label="Source mode"><select value={config.source.mode} onChange={(event) => update({ source: { ...config.source, mode: event.target.value as 'generated' | 'universe' }, ...(event.target.value === 'universe' ? { seed: undefined, simulation: undefined, agent_composition: {} } : { seed: config.seed ?? defaults.seed, simulation: config.simulation ?? defaults.simulation }) })}><option value="generated">Generate from seed & configuration</option><option value="universe">Fork a current universe</option></select></BuilderField>
         <BuilderField label="Source universe"><select value={config.source.universe_id} onChange={(event) => update({ source: { ...config.source, universe_id: event.target.value as 'prime' | 'echo' } })}><option value="prime">Prime</option><option value="echo">Echo</option></select></BuilderField>
      </div>
      <p className="builder-note">Execution is capped at 2,000 agent-ticks or the configured budget, whichever is lower. A configured budget up to 5,000 is accepted.</p>
      {universeSource && <p className="builder-note">Validation captures the universe’s seed, configuration, population, and prepared source hash. Run checks that hash before execution. Generation settings below apply to generated sources. Use a saved run’s “Rerun exact source” for an immutable replay.</p>}
    </fieldset>

    <fieldset className="builder-section" disabled={universeSource}><legend>02 / Population & topology</legend>
      <div className="builder-grid three">
        <NumberField label="Population (1–100)" value={config.simulation?.population} min={1} max={100} onChange={(population) => update({ simulation: { ...simulation, population } })} />
        <BuilderField label="Initial topology"><select value={network.structure ?? 'community'} onChange={(event) => updateNetwork({ structure: event.target.value as InitialNetworkConfig['structure'] })}>{['community', 'ring', 'random', 'empty'].map((structure) => <option key={structure}>{structure}</option>)}</select></BuilderField>
        <NumberField label="Maximum edges / citizen" value={network.max_edges_per_agent ?? 10} max={50} onChange={(max_edges_per_agent) => updateNetwork({ max_edges_per_agent })} />
        <NumberField label="Random edge probability" value={network.edge_probability ?? 0.16} max={1} step={0.01} onChange={(edge_probability) => updateNetwork({ edge_probability })} />
        <NumberField label="Ring degree" value={network.ring_degree ?? 2} min={1} max={10} onChange={(ring_degree) => updateNetwork({ ring_degree })} />
        <NumberField label="Initial homophily weight" value={network.homophily_weight ?? 0.65} max={1} step={0.01} onChange={(homophily_weight) => updateNetwork({ homophily_weight })} />
        <NumberField label="Cross-community edge probability" value={network.cross_community_probability ?? 0.08} max={1} step={0.01} onChange={(cross_community_probability) => updateNetwork({ cross_community_probability })} />
      </div>
      <div className="builder-subhead"><h4>Agent composition <small>{compositionTotal} / {config.simulation?.population ?? 'captured on validation'} assigned</small></h4><button type="button" className="text-button" onClick={() => update({ agent_composition: { civic_participant: simulation.population } })}>Set all to civic participants</button></div>
      <div className="builder-grid three">{archetypes.map((key) => <NumberField key={key} label={key.replaceAll('_', ' ')} value={config.agent_composition[key] ?? 0} max={100} onChange={(count) => update({ agent_composition: { ...config.agent_composition, [key]: count } })} />)}</div>
      <p className="builder-note">Counts must sum to population. Advanced JSON supports custom archetype behavior, relationship dynamics, following, and community evolution.</p>
    </fieldset>

    <fieldset className="builder-section"><legend>03 / Claims · {config.claims.length} / 16</legend>
      <p className="builder-note">Claim IDs are configuration keys used by counterevidence schedules. Each claim requires a source citizen as author. Visibility and reshare probability are values from 0 to 1.</p>
      {config.claims.map((claim, index) => <fieldset className="builder-card" key={index}><legend>Claim {index + 1}</legend>
        <div className="builder-grid three">
          <BuilderField label="Claim ID"><input value={claim.id} onChange={(event) => onChange(renameClaim(config, index, event.target.value))} required /></BuilderField>
          <BuilderField label="Topic"><input value={claim.topic} onChange={(event) => updateClaim(index, { topic: event.target.value })} maxLength={80} required /></BuilderField>
          <BuilderField label="Truth label"><select value={String(claim.truth_label)} onChange={(event) => updateClaim(index, { truth_label: event.target.value === 'true' })}><option value="false">False</option><option value="true">True</option></select></BuilderField>
        </div>
        <BuilderField label="Claim text"><textarea rows={2} value={claim.text} onChange={(event) => updateClaim(index, { text: event.target.value })} maxLength={280} required /></BuilderField>
        <div className="builder-grid four">
          <NumberField label="Credibility" value={claim.credibility} max={1} step={0.05} onChange={(credibility) => updateClaim(index, { credibility })} />
          <NumberField label="Visibility" value={claim.visibility} max={1} step={0.05} onChange={(visibility) => updateClaim(index, { visibility })} />
          <NumberField label="Reshare probability" value={claim.reshare_probability} max={1} step={0.05} onChange={(reshare_probability) => updateClaim(index, { reshare_probability })} />
          <BuilderField label="Author citizen ID"><input list="builder-citizens" value={claim.author_id} onChange={(event) => updateClaim(index, { author_id: event.target.value })} required /></BuilderField>
        </div>
        <button type="button" className="text-button" onClick={() => update({ claims: config.claims.filter((_, i) => i !== index) })}>Remove claim {index + 1}</button>
      </fieldset>)}
      {!config.claims.length && <p className="builder-note">No seeded claims. Branches can still publish through interventions.</p>}
      <button type="button" className="button small ghost" disabled={config.claims.length >= 16} onClick={() => update({ claims: [...config.claims, { id: nextConfigId('claim', config.claims.map((claim) => claim.id)), topic: 'civic_event', text: '', truth_label: false, credibility: 0.5, author_id: citizenIds[0]?.id ?? 'citizen-001', visibility: 1, reshare_probability: 0.5 }] })}>+ Add claim</button>
    </fieldset>

    <fieldset className="builder-section"><legend>04 / Named arms & schedules · {config.arms.length} / 6</legend>
      <p className="builder-note">Choose one baseline. Tick 0 is immediate; later offsets are relative to the source tick. Each arm supports up to 16 interventions. Relationship targets are directional: source → target.</p>
      {config.arms.map((arm, index) => <fieldset className={`builder-card ${arm.id === config.baseline_arm_id ? 'is-baseline' : ''}`} key={index}><legend>Arm {index + 1}{arm.id === config.baseline_arm_id ? ' / baseline' : ''}</legend>
        <div className="builder-grid three">
          <BuilderField label="Arm ID"><input value={arm.id} onChange={(event) => onChange(renameArm(config, index, event.target.value))} required /></BuilderField>
          <BuilderField label="Arm name"><input value={arm.name} onChange={(event) => updateArm(index, { name: event.target.value })} required /></BuilderField>
          <label className="builder-checkbox"><input type="radio" name="experiment-baseline" checked={arm.id === config.baseline_arm_id} onChange={() => update({ baseline_arm_id: arm.id })} />Use arm {index + 1} as baseline</label>
        </div>
        {arm.schedule.map((entry, entryIndex) => <ScheduleEditor key={entryIndex} entry={entry} number={entryIndex + 1} ticks={config.ticks} claims={config.claims} onChange={(next) => updateArm(index, { schedule: arm.schedule.map((item, i) => i === entryIndex ? next : item) })} onRemove={() => updateArm(index, { schedule: arm.schedule.filter((_, i) => i !== entryIndex) })} />)}
        {!arm.schedule.length && <p className="builder-note">No interventions scheduled in this arm.</p>}
        <div className="builder-actions"><button type="button" className="button small ghost" disabled={arm.schedule.length >= 16} onClick={() => updateArm(index, { schedule: [...arm.schedule, { at_tick: 0, intervention: { ...newIntervention(config.claims[0] ? 'counterevidence' : 'seed_post'), ...(config.claims[0] ? { topic: config.claims[0].topic } : {}) }, ...(config.claims[0] ? { claim_id: config.claims[0].id } : {}) }] })}>+ Add intervention to arm {index + 1}</button><button type="button" className="text-button" disabled={config.arms.length <= 2 || arm.id === config.baseline_arm_id} onClick={() => update({ arms: config.arms.filter((_, i) => i !== index) })}>Remove arm {index + 1}</button></div>
      </fieldset>)}
      <button type="button" className="button small ghost" disabled={config.arms.length >= 6} onClick={() => update({ arms: [...config.arms, { id: nextConfigId('arm', config.arms.map((arm) => arm.id)), name: `Branch ${config.arms.length + 1}`, schedule: [] }] })}>+ Add named arm</button>
    </fieldset>
    <datalist id="builder-citizens">{citizenIds.map((citizen) => <option key={citizen.id} value={citizen.id}>{citizen.label}</option>)}</datalist>
    <datalist id="builder-communities">{universeSource && config.source.universe_id === universeState.universeId && universeState.communities?.map((community) => <option key={community.id} value={community.id}>{community.name}</option>)}</datalist>
  </div>
}

function ScheduleEditor({ entry, number, ticks, claims, onChange, onRemove }: { entry: ExperimentScheduleEntry; number: number; ticks: number; claims: ExperimentClaimConfig[]; onChange: (entry: ExperimentScheduleEntry) => void; onRemove: () => void }) {
  const intervention = entry.intervention
  const kind = intervention.kind
  const suppression = kind.startsWith('suppress_')
  const patch = (value: Partial<typeof intervention>) => onChange({ ...entry, intervention: { ...intervention, ...value } })
  return <fieldset className="builder-schedule"><legend>Intervention {number}</legend>
    <div className="builder-grid three">
      <NumberField label="At tick / offset (0 = immediate)" value={entry.at_tick} max={ticks} onChange={(at_tick) => onChange({ ...entry, at_tick })} />
      <BuilderField label="Intervention kind"><select value={kind} onChange={(event) => onChange({ at_tick: entry.at_tick, intervention: { ...newIntervention(event.target.value as InterventionKind), ...(event.target.value === 'counterevidence' && claims[0] ? { topic: claims[0].topic } : {}) }, ...(event.target.value === 'counterevidence' && claims[0] ? { claim_id: claims[0].id } : {}) })}>{INTERVENTION_KINDS.map((item) => <option key={item} value={item}>{item.replaceAll('_', ' ')}</option>)}</select></BuilderField>
      {!suppression && kind !== 'trust_shift' && <BuilderField label="Topic"><input value={intervention.topic ?? ''} onChange={(event) => patch({ topic: event.target.value || null })} maxLength={80} /></BuilderField>}
      {kind !== 'suppress_post' && <BuilderField label={kind === 'suppress_relationship' ? 'Source citizen ID' : 'Citizen / author ID (optional unless required)'}><input list="builder-citizens" value={intervention.agent_id ?? ''} onChange={(event) => patch({ agent_id: event.target.value || null })} /></BuilderField>}
      {['trust_shift', 'suppress_relationship'].includes(kind) && <BuilderField label="Target citizen ID"><input list="builder-citizens" value={intervention.target_agent_id ?? ''} onChange={(event) => patch({ target_agent_id: event.target.value || null })} required /></BuilderField>}
      {kind === 'suppress_post' && <BuilderField label="Target content ID"><input value={intervention.content_id ?? ''} onChange={(event) => patch({ content_id: event.target.value || null })} required /></BuilderField>}
      {['seed_post', 'trust_shift'].includes(kind) && <BuilderField label="Target community ID (optional)"><input list="builder-communities" value={entry.target_community_id ?? ''} onChange={(event) => onChange({ ...entry, target_community_id: event.target.value || undefined })} placeholder="Source community ID" /></BuilderField>}
      {kind === 'counterevidence' && <BuilderField label="Claim configuration ID"><select value={entry.claim_id ?? ''} onChange={(event) => onChange({ ...entry, claim_id: event.target.value || undefined, intervention: { ...intervention, topic: claims.find((claim) => claim.id === event.target.value)?.topic ?? intervention.topic } })} required><option value="">Select a shared claim</option>{entry.claim_id && !claims.some((claim) => claim.id === entry.claim_id) && <option value={entry.claim_id}>Missing: {entry.claim_id}</option>}{claims.map((claim, i) => <option key={i} value={claim.id}>{claim.id} · {claim.topic}</option>)}</select></BuilderField>}
      {!suppression && kind !== 'trust_shift' && <NumberField label="Strength" value={intervention.strength ?? 0.7} max={1} step={0.05} onChange={(strength) => patch({ strength })} />}
      {kind === 'trust_shift' && <NumberField label="Trust delta (non-zero)" value={intervention.delta ?? 0} min={-1} max={1} step={0.05} onChange={(delta) => patch({ delta })} />}
      {['counterevidence', 'seed_post'].includes(kind) && <BuilderField label="Truth label (optional)"><select value={intervention.truth_label == null ? '' : String(intervention.truth_label)} onChange={(event) => patch({ truth_label: event.target.value === '' ? null : event.target.value === 'true' })}><option value="">Server default</option><option value="true">True</option><option value="false">False</option></select></BuilderField>}
    </div>
    {['counterevidence', 'seed_post'].includes(kind) && <BuilderField label="Intervention text (required for seed post)"><textarea rows={2} value={intervention.text ?? ''} onChange={(event) => patch({ text: event.target.value || null })} maxLength={280} /></BuilderField>}
    {Boolean(intervention.delay_ticks) && <p className="builder-note">Imported delay_ticks: {intervention.delay_ticks}. Phase 6 uses at_tick as its only clock; set delay_ticks to zero in Advanced JSON.</p>}
    <button type="button" className="text-button" onClick={onRemove}>Remove intervention {number}</button>
  </fieldset>
}
