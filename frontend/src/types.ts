export type UniverseId = 'prime' | 'echo'

export type ViewId =
  | 'overview'
  | 'social'
  | 'citizens'
  | 'information'
  | 'lab'
  | 'interventions'
  | 'mind'

export type NodeKind = 'citizen' | 'institution' | 'media' | 'community' | 'agent' | 'unknown'

export interface UniverseMetrics {
  population: number
  activeAgents: number
  socialCohesion: number
  trustIndex: number
  informationEntropy: number
  interventionBudget: number
}

export interface NetworkNode {
  id: string
  label: string
  kind: NodeKind
  influence: number
  sentiment: number
  x?: number
  y?: number
  group?: string
}

export interface NetworkEdge {
  source: string
  target: string
  weight: number
  relation?: string
}

export interface Citizen {
  id: string
  name: string
  handle: string
  archetype: string
  location: string
  status: 'active' | 'observed' | 'quiet' | 'influential'
  sentiment: number
  trust: number
  reach: number
  beliefs: string[]
  lastAction: string
  age?: number
  interests?: string[]
  ideology?: number
  skepticism?: number
  curiosity?: number
  communityIds?: string[]
  memoryCount?: number
  relationshipCount?: number
  beliefDetail?: Array<{ topic: string; stance: number; confidence: number; evidence_for: number; evidence_against: number }>
  memories?: Array<{ memory_id: string; summary: string; topic?: string; credibility: number; recall_strength: number; created_tick: number }>
  relationships?: Array<{ target_id: string; affinity: number; trust: number; interactions: number; follows: boolean }>
  decisionTrace?: Array<{ tick: number; phase: string; action?: string; details: Record<string, unknown> }>
  recentPosts?: Array<{ id: string; text: string; topic: string; created_tick: number; credibility: number }>
}

export interface InformationSource {
  id: string
  name: string
  type: string
  reach: number
  velocity: number
  credibility: number
  signal: 'rising' | 'stable' | 'fading'
}

export interface Experiment {
  id: string
  name: string
  description: string
  status: 'ready' | 'running' | 'complete' | 'paused'
  variables: string[]
  projectedImpact: string
  lastRun?: string
  lastRunId?: string
  lastRunStatus?: string
}

export interface InterventionRecord {
  id: string
  label: string
  target: string
  state: 'queued' | 'applied' | 'observed'
  timestamp: string
  effect?: string
}

export interface AgentMind {
  id: string
  name: string
  role: string
  mood: string
  attention: number
  openness: number
  conviction: number
  memory: string[]
  activeDrivers: string[]
  latestTrace?: { tick: number; phase: string; action?: string; content_id?: string; details: Record<string, unknown> }
}

export interface FeedPost {
  id: string
  author_id: string
  author: string
  kind: string
  topic: string
  text: string
  claim_truth?: boolean | null
  parent_id?: string | null
  created_tick: number
  credibility: number
  likes: number
  fact_checked: boolean
  is_counterevidence: boolean
  claim_id?: string | null
  is_reshare?: boolean
  provenance?: string
  intervention_id?: string | null
  exposureCount: number
}

export interface WorldEvent {
  event_id: string
  tick: number
  actor_id: string
  action: string
  target_id?: string | null
  content_id?: string | null
  observed_content_id?: string | null
  claim_id?: string | null
  topic?: string | null
  details: string
}

export interface UniverseState {
  universeId: UniverseId
  label: string
  phase: string
  tick: number
  updatedAt: string
  metrics: UniverseMetrics
  nodes: NetworkNode[]
  edges: NetworkEdge[]
  citizens: Citizen[]
  sources: InformationSource[]
  experiments: Experiment[]
  interventions: InterventionRecord[]
  minds: AgentMind[]
  posts?: FeedPost[]
  events?: WorldEvent[]
  communities?: Array<{ id: string; name: string; topic: string; description: string; member_ids: string[] }>
  metricsDetail?: MetricsDetail
}

export interface HealthResponse {
  status: string
  version?: string
  tick?: number
}

export interface ExperimentMetricSnapshot {
  tick: number
  population: number
  total_contents: number
  posts: number
  replies: number
  fact_checks: number
  ignored_decisions: number
  average_belief_confidence: number
  average_relationship_trust: number
  polarization: number
  belief_accuracy: number
  engagement_rate: number
  exposure_rate: number
  reshare_rate: number
  correction_reach: number
  time_until_claim_stops_spreading: number
  claim_metrics?: Record<string, ClaimMetricSnapshot>
}

export type ClaimStatus = 'active' | 'disputed' | 'confirmed' | 'corrected'

export interface ClaimMetricSnapshot {
  claim_id: string
  topic: string
  status: ClaimStatus
  truth_label: boolean
  first_tick: number
  last_tick: number
  exposures: number
  unique_exposed_agents: number
  reshares: number
  unique_resharers: number
  replies: number
  fact_checks: number
  corrections: number
}

export interface ClaimLifecycleEvent {
  status: ClaimStatus
  tick: number
  content_id?: string | null
  actor_id?: string | null
  reason: string
}

export interface ClaimProvenanceRecord {
  content_id: string
  kind: string
  actor_id: string
  tick: number
  parent_content_id?: string | null
  source: string
  intervention_id?: string | null
}

export interface ClaimRecord {
  claim_id: string
  topic: string
  text: string
  truth_label: boolean
  origin_content_id: string
  origin_author_id: string
  created_tick: number
  origin_source: string
  origin_intervention_id?: string | null
  status: ClaimStatus
  provenance: ClaimProvenanceRecord[]
  lifecycle: ClaimLifecycleEvent[]
  metrics?: ClaimMetricSnapshot | null
}

export interface MetricsDetail {
  tick: number
  population: number
  total_contents: number
  posts: number
  replies: number
  fact_checks: number
  ignored_decisions: number
  follows: number
  unfollows: number
  community_joins: number
  community_leaves: number
  active_agents: number
  average_belief_confidence: number
  average_relationship_trust: number
  polarization: number
  belief_accuracy: number
  engagement_rate: number
  exposure_rate: number
  reshare_rate: number
  correction_reach: number
  community_fragmentation: number
  time_until_claim_stops_spreading: number
  topic_metrics: Record<string, Record<string, number>>
  claim_metrics: Record<string, ClaimMetricSnapshot>
}

export interface MetricHistorySample {
  tick: number
  metrics: ExperimentMetricSnapshot
}

export interface ExperimentHistoryBranch {
  samples: MetricHistorySample[]
}

export interface ExperimentHistory {
  run_id: string
  experiment_id: string
  universe_id: UniverseId
  seed: number
  ticks: number
  branches: {
    control?: ExperimentHistoryBranch
    treatment?: ExperimentHistoryBranch
  }
  metric_definitions: Record<string, string>
}

export interface ExperimentComparison {
  experiment_id: string
  universe_id: UniverseId
  seed: number
  ticks: number
  control_metrics: ExperimentMetricSnapshot
  treatment_metrics: ExperimentMetricSnapshot
  metric_deltas: Record<string, number>
  earliest_divergence: Record<string, unknown> | null
  run_id?: string
  status?: string
}

export type ExperimentRunStatus = 'created' | 'running' | 'completed' | 'failed'

export interface SnapshotProvenance {
  snapshotId: number
  seed: number
  tick: number
  createdAt?: string | null
  provenance: string
  universeId: UniverseId
  experimentRunId?: string | null
}

export interface ExperimentRun {
  runId: string
  experimentId: string
  universeId: UniverseId
  status: ExperimentRunStatus
  seed?: number
  sourceTick?: number
  ticks?: number
  createdAt: string
  updatedAt: string
  sourceSnapshot?: SnapshotProvenance
  controlSnapshot?: SnapshotProvenance
  treatmentSnapshot?: SnapshotProvenance
  result?: ExperimentComparison
  error?: string
}

export interface BatchExperimentConfigInput {
  universeId: UniverseId
  experimentId: string
  baseSeed?: number
  population: number
  ticks: number
  repetitions: number
  computationalBudget: number
  confidenceLevel: number
  objective: {
    metric: string
    direction: 'increase' | 'decrease'
    threshold: number
  }
}

export interface BatchProgress {
  totalRuns: number
  completedRuns: number
  failedRuns: number
  percentComplete: number
  currentRepetition?: number
  executionMode: 'serial_synchronous'
}

export interface BatchDescriptiveStatistics {
  count: number
  mean?: number | null
  median?: number | null
  variance?: number | null
  standardDeviation?: number | null
  confidenceInterval?: { lower: number; upper: number; level: number; method: string } | null
}

export interface BatchMetricSummary {
  control: BatchDescriptiveStatistics
  treatment: BatchDescriptiveStatistics
  pairedDifference: BatchDescriptiveStatistics
  effectSize: {
    pairedCohensD?: number | null
    cliffsDelta?: number | null
    probabilityTreatmentBetter?: number | null
  }
}

export interface BatchAggregate {
  completedPairs: number
  failedRuns: number
  objective: {
    metric: string
    direction: 'increase' | 'decrease'
    threshold: number
    successes: number
    evaluated: number
    successRate: number
    definition: string
  }
  objectiveSuccessRate: number
  metrics: Record<string, BatchMetricSummary>
}

export interface ExperimentBatchRunSummary {
  runId: string
  repetition: number
  seed: number
  status: string
}

export type ExperimentBatchStatus = 'created' | 'running' | 'completed' | 'completed_with_failures' | 'failed'

export interface ExperimentBatch {
  batchId: string
  experimentId: string
  universeId: UniverseId
  status: ExperimentBatchStatus
  config?: Record<string, unknown>
  totalRuns: number
  completedRuns: number
  failedRuns: number
  runIds: string[]
  runs: ExperimentBatchRunSummary[]
  progress?: BatchProgress
  aggregate?: BatchAggregate
  objectiveSuccessRate?: number
  createdAt: string
  updatedAt: string
}

export interface SelectedCitizen extends Citizen {
  connections?: NetworkNode[]
  recentSignals?: string[]
}
