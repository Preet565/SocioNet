import type { UniverseId, WorldEvent } from './types'

export type InterventionKind = 'counterevidence' | 'seed_post' | 'trust_shift' | 'fact_check' | 'suppress_citizen' | 'suppress_post' | 'suppress_relationship'

export interface ExperimentIntervention {
  kind: InterventionKind
  topic?: string | null
  agent_id?: string | null
  target_agent_id?: string | null
  delay_ticks?: number
  strength?: number
  delta?: number
  truth_label?: boolean | null
  text?: string | null
  content_id?: string | null
  claim_id?: string | null
}

export interface ExperimentScheduleEntry {
  at_tick: number
  intervention: ExperimentIntervention
  target_community_id?: string | null
  claim_id?: string | null
}

export interface ExperimentArmConfig {
  id: string
  name: string
  schedule: ExperimentScheduleEntry[]
}

export interface ExperimentClaimConfig {
  id: string
  topic: string
  text: string
  truth_label: boolean
  credibility: number
  author_id: string
  visibility: number
  reshare_probability: number
}

export interface InitialNetworkConfig {
  structure?: 'community' | 'ring' | 'random' | 'empty'
  max_edges_per_agent?: number
  edge_probability?: number
  ring_degree?: number
  homophily_weight?: number
  cross_community_probability?: number
}

/** Optional fields use the server defaults; advanced JSON retains all Phase 4 parameters. */
export interface ExperimentSimulationConfig {
  population: number
  initial_network?: InitialNetworkConfig
  archetypes?: Record<string, Record<string, number>>
  max_relationships?: number
  max_following?: number
  [parameter: string]: unknown
}

export interface MultiExperimentConfig {
  schema_version: 1
  name: string
  hypothesis: string
  seed: number
  ticks: number
  source: { mode: 'generated' | 'universe'; universe_id: UniverseId }
  simulation: ExperimentSimulationConfig
  agent_composition: Record<string, number>
  claims: ExperimentClaimConfig[]
  baseline_arm_id: string
  arms: ExperimentArmConfig[]
  computational_budget: number
}

/** Universe inputs can defer generation fields to the captured source. */
export type MultiExperimentConfigInput = Omit<MultiExperimentConfig, 'seed' | 'simulation'> & { seed?: number | null; simulation?: ExperimentSimulationConfig | null }

export interface ExperimentPlan {
  source_tick: number
  source_hash?: string
  population: number
  ticks: number
  arm_count: number
  agent_ticks: number
  baseline_arm_id: string
  arms: Array<{ id: string; name: string; intervention_count: number }>
  warnings: string[]
}

export interface ValidatedExperiment {
  valid: true
  config: MultiExperimentConfig
  plan: ExperimentPlan
}

export type ExperimentMetrics = Record<string, unknown>

export interface MultiExperimentArmResult {
  id: string
  name: string
  status: string
  initial_snapshot_hash?: string | null
  final_snapshot_id?: number | string | null
  metrics?: ExperimentMetrics | null
  metric_deltas?: Record<string, number> | null
  earliest_divergence?: Record<string, unknown> | null
  error?: string | null
}

export interface MultiExperimentSummary {
  id: string
  status: string
  name: string
  created_at: string
}

export interface MultiExperimentDetail extends MultiExperimentSummary {
  config: MultiExperimentConfig
  config_hash: string
  source_hash: string
  source_snapshot_id: number | string | null
  source_tick: number
  baseline_arm_id: string
  arms: MultiExperimentArmResult[]
  error?: string | null
}

export interface MultiExperimentArmDetail {
  id: string
  name: string
  metrics: ExperimentMetrics | null
  samples: Array<{ tick: number; metrics: ExperimentMetrics }>
  events: WorldEvent[]
  state: Record<string, unknown> | null
  metric_deltas: Record<string, number> | null
  earliest_divergence: Record<string, unknown> | null
}

/** The portable manifest is kept opaque so immutable source/provenance fields survive import. */
export type ExperimentManifest = Record<string, unknown> & { config: MultiExperimentConfig; source_snapshot: Record<string, unknown> }
