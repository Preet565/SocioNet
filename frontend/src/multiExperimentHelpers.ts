import type { UniverseId } from './types'
import type { ExperimentIntervention, ExperimentManifest, ExperimentMetrics, InterventionKind, MultiExperimentConfig, MultiExperimentConfigInput, ValidatedExperiment } from './multiExperimentTypes'

export const EXPERIMENT_LIMITS = { arms: 6, population: 100, ticks: 20, agentTicks: 2000, computationalBudget: 5000, claims: 16, schedule: 16 } as const
export const DEFAULT_ARCHETYPES = ['civic_participant', 'evidence_keeper', 'signal_seeker', 'community_steward', 'bridge_builder', 'contrarian']
export const INTERVENTION_KINDS: InterventionKind[] = ['counterevidence', 'seed_post', 'fact_check', 'trust_shift', 'suppress_citizen', 'suppress_post', 'suppress_relationship']

export function newIntervention(kind: InterventionKind): ExperimentIntervention {
  switch (kind) {
    case 'counterevidence': return { kind, topic: 'civic_event', strength: 0.85 }
    case 'seed_post': return { kind, topic: 'civic_event', text: 'A fictional civic event is scheduled.', truth_label: true, strength: 0.7 }
    case 'fact_check': return { kind, topic: 'civic_event', strength: 0.7 }
    case 'trust_shift': return { kind, target_agent_id: '', delta: 0.1 }
    case 'suppress_citizen': return { kind, agent_id: '' }
    case 'suppress_post': return { kind, content_id: '' }
    case 'suppress_relationship': return { kind, agent_id: '', target_agent_id: '' }
  }
}

export function createExperimentConfig(universeId: UniverseId): MultiExperimentConfig {
  return {
    schema_version: 1,
    name: 'A signal and its correction',
    hypothesis: 'Delayed credible counterevidence changes the spread of a fictional claim.',
    seed: 9001,
    ticks: 6,
    source: { mode: 'generated', universe_id: universeId },
    simulation: { population: 30, initial_network: { structure: 'community', max_edges_per_agent: 10, edge_probability: 0.16, ring_degree: 2, homophily_weight: 0.65, cross_community_probability: 0.08 } },
    agent_composition: { civic_participant: 10, evidence_keeper: 6, signal_seeker: 5, community_steward: 4, bridge_builder: 3, contrarian: 2 },
    claims: [{ id: 'civic-claim', topic: 'civic_event', text: 'The fictional civic event has been cancelled.', truth_label: false, credibility: 0.4, author_id: 'citizen-001', visibility: 1, reshare_probability: 0.5 }],
    baseline_arm_id: 'baseline',
    arms: [
      { id: 'baseline', name: 'No correction', schedule: [] },
      { id: 'correction', name: 'Delayed correction', schedule: [{ at_tick: 2, intervention: newIntervention('counterevidence'), claim_id: 'civic-claim' }] },
    ],
    computational_budget: 5000,
  }
}

export const isRecord = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value))

/** Guard editor shape before rendering it. Semantic validation remains server-owned. */
export function configShapeErrors(value: unknown, requireNormalized = false): string[] {
  if (!isRecord(value)) return ['Config must be a JSON object.']
  const errors: string[] = []
  const text = (item: unknown, path: string) => { if (typeof item !== 'string') errors.push(`${path} must be a string.`) }
  const numeric = (item: unknown, path: string) => { if (typeof item !== 'number' || !Number.isFinite(item)) errors.push(`${path} must be a finite number.`) }
  if (value.schema_version !== 1) errors.push('schema_version must be 1.')
  for (const key of ['name', 'hypothesis', 'baseline_arm_id']) text(value[key], key)
  for (const key of ['ticks', 'computational_budget']) numeric(value[key], key)
  const deferredSource = !requireNormalized && isRecord(value.source) && value.source.mode === 'universe'
  if (!deferredSource || value.seed != null) numeric(value.seed, 'seed')
  if (!isRecord(value.source) || !['generated', 'universe'].includes(String(value.source.mode)) || !['prime', 'echo'].includes(String(value.source.universe_id))) errors.push('source requires mode generated/universe and universe_id prime/echo.')
  if (deferredSource && value.simulation == null) { /* Captured by server validation. */ }
  else if (!isRecord(value.simulation)) errors.push('simulation must be an object with population.')
  else {
    numeric(value.simulation.population, 'simulation.population')
    if (value.simulation.initial_network !== undefined && !isRecord(value.simulation.initial_network)) errors.push('simulation.initial_network must be an object.')
    else if (isRecord(value.simulation.initial_network)) {
      const network = value.simulation.initial_network
      if (network.structure !== undefined) text(network.structure, 'simulation.initial_network.structure')
      for (const key of ['max_edges_per_agent', 'edge_probability', 'ring_degree', 'homophily_weight', 'cross_community_probability']) if (network[key] !== undefined) numeric(network[key], `simulation.initial_network.${key}`)
    }
    if (value.simulation.archetypes !== undefined && (!isRecord(value.simulation.archetypes) || Object.values(value.simulation.archetypes).some((behavior) => !isRecord(behavior)))) errors.push('simulation.archetypes must map keys to behavior parameter objects.')
  }
  if (!isRecord(value.agent_composition)) errors.push('agent_composition must map archetype keys to counts.')
  else Object.entries(value.agent_composition).forEach(([key, count]) => numeric(count, `agent_composition.${key}`))
  if (!Array.isArray(value.claims)) errors.push('claims must be an array.')
  else value.claims.forEach((claim: unknown, index: number) => {
    const path = `claims.${index}`
    if (!isRecord(claim)) { errors.push(`${path} must be an object.`); return }
    for (const key of ['id', 'topic', 'text']) text(claim[key], `${path}.${key}`)
    for (const key of ['credibility', 'visibility', 'reshare_probability']) numeric(claim[key], `${path}.${key}`)
    if (typeof claim.truth_label !== 'boolean') errors.push(`${path}.truth_label must be true or false.`)
    text(claim.author_id, `${path}.author_id`)
  })
  if (!Array.isArray(value.arms)) errors.push('arms must be an array.')
  else value.arms.forEach((arm: unknown, index: number) => {
    const path = `arms.${index}`
    if (!isRecord(arm)) { errors.push(`${path} must be an object.`); return }
    text(arm.id, `${path}.id`); text(arm.name, `${path}.name`)
    if (!Array.isArray(arm.schedule)) { errors.push(`${path}.schedule must be an array.`); return }
    arm.schedule.forEach((entry: unknown, entryIndex: number) => {
      const at = `${path}.schedule.${entryIndex}`
      if (!isRecord(entry) || !isRecord(entry.intervention)) { errors.push(`${at} requires an intervention object.`); return }
      numeric(entry.at_tick, `${at}.at_tick`)
      if (!INTERVENTION_KINDS.includes(entry.intervention.kind as InterventionKind)) errors.push(`${at}.intervention.kind is unsupported.`)
      for (const key of ['topic', 'text', 'agent_id', 'target_agent_id', 'content_id']) if (entry.intervention[key] != null) text(entry.intervention[key], `${at}.intervention.${key}`)
      for (const key of ['strength', 'delta', 'delay_ticks']) if (entry.intervention[key] !== undefined) numeric(entry.intervention[key], `${at}.intervention.${key}`)
      for (const key of ['target_community_id', 'claim_id']) if (entry[key] != null) text(entry[key], `${at}.${key}`)
    })
  })
  return errors
}

export function parseExperimentJson(text: string): { kind: 'config'; config: MultiExperimentConfigInput } | { kind: 'bundle'; config: MultiExperimentConfig; manifest: ExperimentManifest } {
  if (new TextEncoder().encode(text).length > 32 * 1024 * 1024) throw new Error('Import exceeds the 32 MB bundle limit.')
  let value: unknown
  try { value = JSON.parse(text) as unknown } catch (error) { throw new Error(`Invalid JSON: ${error instanceof Error ? error.message : 'check syntax'}`) }
  const record = isRecord(value) ? value : undefined
  const bundle = record && 'config' in record
  const config = bundle ? record.config : value
  if (new TextEncoder().encode(JSON.stringify(config) ?? '').length > 65_536) throw new Error('Configuration exceeds the 65,536-byte limit. Reduce claims, schedules, or advanced parameters.')
  const errors = configShapeErrors(config, Boolean(bundle))
  if (errors.length) throw new Error(errors.join('\n'))
  if (bundle) {
    if (!isRecord(record.source_snapshot)) throw new Error('A portable bundle must include its immutable source_snapshot object. Import a bare config for a new source.')
    return { kind: 'bundle', config: config as MultiExperimentConfig, manifest: value as ExperimentManifest }
  }
  return { kind: 'config', config: config as MultiExperimentConfigInput }
}

/** Universe validation captures its current seed/config. Normalized execution keeps them pinned. */
export function validationInput(config: MultiExperimentConfigInput): MultiExperimentConfigInput {
  if (config.source.mode === 'generated') return config
  const { seed: _seed, simulation: _simulation, ...input } = config
  return { ...input, agent_composition: {} }
}

export function plannedAgentTicks(config: MultiExperimentConfigInput, sourcePopulation?: number): number | undefined {
  const population = config.source.mode === 'universe' ? sourcePopulation ?? config.simulation?.population : config.simulation?.population
  return population === undefined ? undefined : population * config.ticks * config.arms.length
}

export const effectiveAgentTickBudget = (budget: number) => Math.min(EXPERIMENT_LIMITS.agentTicks, budget)

export function configIssues(config: MultiExperimentConfigInput, sourcePopulation?: number): string[] {
  const issues: string[] = []
  const integer = (value: number, min: number, max: number, path: string) => { if (!Number.isInteger(value) || value < min || value > max) issues.push(`${path} must be an integer from ${min} to ${max}.`) }
  const unit = (value: number, path: string) => { if (!Number.isFinite(value) || value < 0 || value > 1) issues.push(`${path} must be between 0 and 1.`) }
  if (!config.name.trim()) issues.push('name is required.')
  if (config.name.length > 120) issues.push('name must not exceed 120 characters.')
  if (config.hypothesis.length > 2000) issues.push('hypothesis must not exceed 2000 characters.')
  if (config.source.mode === 'generated' || config.seed != null) integer(config.seed ?? NaN, Number.MIN_SAFE_INTEGER, Number.MAX_SAFE_INTEGER, 'seed')
  integer(config.ticks, 1, EXPERIMENT_LIMITS.ticks, 'ticks')
  integer(config.arms.length, 2, EXPERIMENT_LIMITS.arms, 'arm count')
  if (config.source.mode === 'generated' || config.simulation != null) integer(config.simulation?.population ?? NaN, 1, EXPERIMENT_LIMITS.population, 'simulation.population')
  integer(config.computational_budget, 2, EXPERIMENT_LIMITS.computationalBudget, 'computational_budget')
  const cost = plannedAgentTicks(config, sourcePopulation)
  const effectiveBudget = effectiveAgentTickBudget(config.computational_budget)
  if (cost !== undefined && cost > effectiveBudget) issues.push(`Planned ${cost} agent-ticks exceeds the effective budget (${effectiveBudget}); hard cap is ${EXPERIMENT_LIMITS.agentTicks}.`)
  if (config.source.mode === 'universe' && sourcePopulation !== undefined && sourcePopulation > 100) issues.push('Source population exceeds 100. Choose a generated source or a smaller universe.')
  if (config.source.mode === 'generated') {
    const counts = Object.entries(config.agent_composition)
    counts.forEach(([key, count]) => integer(count, 0, 100, `agent_composition.${key}`))
    if (counts.length && counts.reduce((total, [, count]) => total + count, 0) !== config.simulation?.population) issues.push('Agent composition counts must sum to population (or use an empty object for server defaults).')
  }
  if (config.claims.length > 16) issues.push('At most 16 claims are allowed.')
  const identifier = (id: string, path: string) => { if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/.test(id)) issues.push(`${path} must be 1–80 letters, numbers, dots, underscores or hyphens, starting with a letter/number.`) }
  const unique = (ids: string[], path: string) => { if (new Set(ids).size !== ids.length) issues.push(`${path} IDs must be unique.`); ids.forEach((id) => identifier(id, `${path} ID`)) }
  unique(config.claims.map((claim) => claim.id), 'Claim')
  unique(config.arms.map((arm) => arm.id), 'Arm')
  if (!config.arms.some((arm) => arm.id === config.baseline_arm_id)) issues.push('baseline_arm_id must reference an arm.')
  config.claims.forEach((claim, index) => {
    if (!claim.topic.trim() || !claim.text.trim()) issues.push(`claims.${index}: topic and text are required.`)
    if (!claim.author_id) issues.push(`claims.${index}.author_id is required.`)
    unit(claim.credibility, `claims.${index}.credibility`); unit(claim.visibility, `claims.${index}.visibility`); unit(claim.reshare_probability, `claims.${index}.reshare_probability`)
  })
  config.arms.forEach((arm, index) => {
    if (!arm.name.trim()) issues.push(`arms.${index}.name is required.`)
    if (arm.schedule.length > 16) issues.push(`arms.${index}: at most 16 scheduled interventions.`)
    arm.schedule.forEach((entry, scheduleIndex) => {
      const path = `arms.${index}.schedule.${scheduleIndex}`
      integer(entry.at_tick, 0, config.ticks, `${path}.at_tick`)
      if (entry.claim_id && !config.claims.some((claim) => claim.id === entry.claim_id)) issues.push(`${path}.claim_id must reference a configured claim ID.`)
      const intervention = entry.intervention
      if (intervention.delay_ticks) issues.push(`${path}: use at_tick for delays; intervention.delay_ticks must be zero.`)
      if (entry.target_community_id && !['trust_shift', 'seed_post'].includes(intervention.kind)) issues.push(`${path}: community targets support only trust_shift and seed_post.`)
      if (intervention.kind === 'counterevidence') {
        const claim = config.claims.find((item) => item.id === entry.claim_id)
        if (!claim) issues.push(`${path}: counterevidence requires a configured claim_id.`)
        else if (claim.topic !== intervention.topic) issues.push(`${path}: counterevidence topic must match its claim.`)
      }
      if (entry.claim_id && intervention.kind !== 'counterevidence') issues.push(`${path}: claim_id is only supported for counterevidence.`)
      if (['counterevidence', 'seed_post'].includes(intervention.kind) && !intervention.topic?.trim()) issues.push(`${path}: ${intervention.kind} requires topic.`)
      if (intervention.kind === 'seed_post' && !intervention.text?.trim()) issues.push(`${path}: seed_post requires text.`)
      if (intervention.kind === 'fact_check' && !intervention.topic?.trim() && !intervention.agent_id) issues.push(`${path}: fact_check requires topic or citizen.`)
      if (['suppress_citizen', 'suppress_relationship'].includes(intervention.kind) && !intervention.agent_id) issues.push(`${path}: source citizen is required.`)
      if (['trust_shift', 'suppress_relationship'].includes(intervention.kind) && !intervention.target_agent_id) issues.push(`${path}: target citizen is required.`)
      if (intervention.kind === 'trust_shift' && !intervention.delta) issues.push(`${path}: trust shift requires a non-zero delta.`)
      if (intervention.kind === 'suppress_post' && !intervention.content_id) issues.push(`${path}: content ID is required.`)
    })
  })
  return issues
}

export function canRunExperiment(config: MultiExperimentConfigInput, validated: ValidatedExperiment | undefined, options: { offline: boolean; busy: boolean; jsonDirty: boolean; staleSource?: boolean; issues: string[] }) {
  return !options.offline && !options.busy && !options.jsonDirty && !options.staleSource && options.issues.length === 0 && validated?.valid === true && JSON.stringify(config) === JSON.stringify(validated.config)
}

export function nextConfigId(prefix: string, existing: string[]) {
  let index = 1
  while (existing.includes(`${prefix}-${index}`)) index++
  return `${prefix}-${index}`
}

export function renameClaim<T extends MultiExperimentConfigInput>(config: T, index: number, id: string): T {
  const oldId = config.claims[index].id
  return { ...config, claims: config.claims.map((claim, i) => i === index ? { ...claim, id } : claim), arms: config.arms.map((arm) => ({ ...arm, schedule: arm.schedule.map((entry) => entry.claim_id === oldId ? { ...entry, claim_id: id } : entry) })) }
}

export function renameArm<T extends MultiExperimentConfigInput>(config: T, index: number, id: string): T {
  const oldId = config.arms[index].id
  return { ...config, baseline_arm_id: config.baseline_arm_id === oldId ? id : config.baseline_arm_id, arms: config.arms.map((arm, i) => i === index ? { ...arm, id } : arm) }
}

export function numericMetric(metrics: ExperimentMetrics | null | undefined, key: string): number | undefined {
  const value = metrics?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

export function metricKeys(...metrics: Array<ExperimentMetrics | null | undefined>): string[] {
  return [...new Set(metrics.flatMap((record) => Object.keys(record ?? {}).filter((key) => !['tick', 'population'].includes(key) && numericMetric(record, key) !== undefined)))].sort()
}

/** Keep gaps missing in the chart, just as they are in the accessible table. */
export function metricTimeline(samples: Array<{ tick: number; metrics: ExperimentMetrics }>, metric: string) {
  const ordered = [...samples].filter((sample) => Number.isFinite(sample.tick)).sort((a, b) => a.tick - b.tick)
  const segments: Array<Array<{ tick: number; value: number }>> = []
  let segment: Array<{ tick: number; value: number }> = []
  for (const sample of ordered) {
    const value = numericMetric(sample.metrics, metric)
    if (value === undefined || (segment.length && sample.tick > segment[segment.length - 1].tick + 1)) {
      if (segment.length) segments.push(segment)
      segment = []
    }
    if (value !== undefined) segment.push({ tick: sample.tick, value })
  }
  if (segment.length) segments.push(segment)
  return { ticks: [...new Set(ordered.map((sample) => sample.tick))], segments }
}

export function formatMetric(value: unknown, signed = false) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${signed && value > 0 ? '+' : ''}${new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 }).format(value)}`
}

export function downloadJson(value: unknown, filename: string) {
  downloadJsonText(JSON.stringify(value, null, 2), filename)
}

export function downloadJsonText(text: string, filename: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url; link.download = filename
  document.body.append(link); link.click(); link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
