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
  exposureCount: number
}

export interface WorldEvent {
  event_id: string
  tick: number
  actor_id: string
  action: string
  target_id?: string | null
  content_id?: string | null
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

export interface SelectedCitizen extends Citizen {
  connections?: NetworkNode[]
  recentSignals?: string[]
}
