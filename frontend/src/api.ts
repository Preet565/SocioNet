import type {
  AgentMind,
  Citizen,
  Experiment,
  ExperimentComparison,
  HealthResponse,
  InformationSource,
  InterventionRecord,
  NetworkEdge,
  NetworkNode,
  UniverseId,
  UniverseMetrics,
  UniverseState,
  WorldEvent,
} from './types'

export const API_URL = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/$/, '')

export class ApiUnavailableError extends Error {
  constructor(message = 'The simulation API is unreachable.') {
    super(message)
    this.name = 'ApiUnavailableError'
  }
}

export class ApiResponseError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiResponseError'
    this.status = status
  }
}

const request = async <T>(path: string, options: RequestInit = {}): Promise<T> => {
  let response: Response
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...options.headers },
    })
  } catch {
    throw new ApiUnavailableError()
  }

  const text = await response.text()
  let payload: unknown = undefined
  if (text) {
    try {
      payload = JSON.parse(text) as unknown
    } catch {
      payload = text
    }
  }
  if (!response.ok) {
    const record = asRecord(payload)
    const detail = typeof record?.detail === 'string' ? record.detail : `Request failed with ${response.status}`
    throw new ApiResponseError(response.status, detail)
  }
  return payload as T
}

const asRecord = (value: unknown): Record<string, unknown> | undefined =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined

const pick = (record: Record<string, unknown> | undefined, keys: string[]): unknown => {
  for (const key of keys) {
    if (record && record[key] !== undefined && record[key] !== null) return record[key]
  }
  return undefined
}

const numberOr = (value: unknown, fallback: number): number => {
  const numeric = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(numeric) ? numeric : fallback
}

const stringOr = (value: unknown, fallback: string): string => typeof value === 'string' && value.length > 0 ? value : fallback

const arrayOr = (value: unknown): unknown[] => Array.isArray(value) ? value : []

const unwrap = (payload: unknown): Record<string, unknown> => {
  const root = asRecord(payload) ?? {}
  const nested = asRecord(root.data) ?? asRecord(root.state)
  return nested ?? root
}

const normalizeNode = (value: unknown, index: number): NetworkNode => {
  const record = asRecord(value)
  const kindValue = stringOr(pick(record, ['kind', 'type', 'category']), 'unknown').toLowerCase()
  const allowedKinds: NetworkNode['kind'][] = ['citizen', 'institution', 'media', 'community', 'agent']
  return {
    id: stringOr(pick(record, ['id', 'node_id', 'key']), `node-${index + 1}`),
    label: stringOr(pick(record, ['label', 'name', 'title']), `Node ${index + 1}`),
    kind: allowedKinds.includes(kindValue as NetworkNode['kind']) ? kindValue as NetworkNode['kind'] : 'unknown',
    influence: numberOr(pick(record, ['influence', 'centrality', 'weight']), 0.5),
    sentiment: numberOr(pick(record, ['sentiment', 'valence']), 0.5),
    x: numberOr(pick(record, ['x', 'position_x']), Number.NaN),
    y: numberOr(pick(record, ['y', 'position_y']), Number.NaN),
    group: stringOr(pick(record, ['group', 'cluster']), undefined as unknown as string),
  }
}

const normalizeEdge = (value: unknown, index: number): NetworkEdge => {
  const record = asRecord(value)
  return {
    source: stringOr(pick(record, ['source', 'from', 'source_id']), `node-${index + 1}`),
    target: stringOr(pick(record, ['target', 'to', 'target_id']), `node-${index + 2}`),
    weight: numberOr(pick(record, ['weight', 'strength', 'value']), 0.5),
    relation: stringOr(pick(record, ['relation', 'type', 'label']), 'connected'),
  }
}

const normalizeCitizen = (value: unknown, index: number): Citizen => {
  const record = asRecord(value)
  const status = stringOr(pick(record, ['status', 'activity']), 'active')
  const allowedStatuses: Citizen['status'][] = ['active', 'observed', 'quiet', 'influential']
  return {
    id: stringOr(pick(record, ['id', 'citizen_id']), `citizen-${index + 1}`),
    name: stringOr(pick(record, ['name', 'display_name']), `Citizen ${index + 1}`),
    handle: stringOr(pick(record, ['handle', 'username']), '@unassigned'),
    archetype: stringOr(pick(record, ['archetype', 'role', 'persona']), 'Participant'),
    location: stringOr(pick(record, ['location', 'timezone']), 'Unknown location'),
    status: allowedStatuses.includes(status as Citizen['status']) ? status as Citizen['status'] : 'active',
    sentiment: numberOr(pick(record, ['sentiment', 'valence']), 0.5),
    trust: numberOr(pick(record, ['trust', 'trust_index']), 0.5),
    reach: numberOr(pick(record, ['reach', 'influence']), 0.5),
    beliefs: arrayOr(pick(record, ['beliefs', 'values'])).map((belief) => String(belief)),
    lastAction: stringOr(pick(record, ['last_action', 'lastAction', 'recent_action']), 'No recent action recorded'),
    age: numberOr(pick(record, ['age']), 0),
    interests: arrayOr(pick(record, ['interests'])).map((item) => String(item)),
    ideology: numberOr(pick(record, ['ideology']), 0),
    skepticism: numberOr(pick(record, ['skepticism']), 0),
    curiosity: numberOr(pick(record, ['curiosity']), 0),
    communityIds: arrayOr(pick(record, ['communityIds', 'community_ids'])).map((item) => String(item)),
    memoryCount: numberOr(pick(record, ['memoryCount', 'memory_count']), 0),
    relationshipCount: numberOr(pick(record, ['relationshipCount', 'relationship_count']), 0),
    beliefDetail: arrayOr(pick(record, ['beliefDetail', 'belief_detail'])).map((item) => item as NonNullable<Citizen['beliefDetail']>[number]),
    memories: arrayOr(pick(record, ['memories'])).map((item) => item as NonNullable<Citizen['memories']>[number]),
    relationships: arrayOr(pick(record, ['relationships'])).map((item) => item as NonNullable<Citizen['relationships']>[number]),
    decisionTrace: arrayOr(pick(record, ['decisionTrace', 'decision_trace'])).map((item) => item as NonNullable<Citizen['decisionTrace']>[number]),
    recentPosts: arrayOr(pick(record, ['recentPosts', 'recent_posts'])).map((item) => item as NonNullable<Citizen['recentPosts']>[number]),
  }
}

const normalizeSource = (value: unknown, index: number): InformationSource => {
  const record = asRecord(value)
  const signal = stringOr(pick(record, ['signal', 'trend']), 'stable')
  return {
    id: stringOr(pick(record, ['id', 'source_id']), `source-${index + 1}`),
    name: stringOr(pick(record, ['name', 'label']), `Source ${index + 1}`),
    type: stringOr(pick(record, ['type', 'category']), 'Information source'),
    reach: numberOr(pick(record, ['reach', 'audience']), 0.5),
    velocity: numberOr(pick(record, ['velocity', 'spread_rate']), 0.5),
    credibility: numberOr(pick(record, ['credibility', 'trust']), 0.5),
    signal: signal === 'rising' || signal === 'fading' ? signal : 'stable',
  }
}

const normalizeExperiment = (value: unknown, index: number): Experiment => {
  const record = asRecord(value)
  const status = stringOr(pick(record, ['status', 'state']), 'ready')
  const allowed: Experiment['status'][] = ['ready', 'running', 'complete', 'paused']
  return {
    id: stringOr(pick(record, ['id', 'experiment_id']), `experiment-${index + 1}`),
    name: stringOr(pick(record, ['name', 'title']), `Experiment ${index + 1}`),
    description: stringOr(pick(record, ['description', 'summary']), 'No experiment description provided.'),
    status: allowed.includes(status as Experiment['status']) ? status as Experiment['status'] : 'ready',
    variables: arrayOr(pick(record, ['variables', 'parameters'])).map((variable) => String(variable)),
    projectedImpact: stringOr(pick(record, ['projected_impact', 'impact']), 'Awaiting model output'),
    lastRun: typeof pick(record, ['last_run', 'lastRun']) === 'string' ? pick(record, ['last_run', 'lastRun']) as string : undefined,
  }
}

const normalizeIntervention = (value: unknown, index: number): InterventionRecord => {
  const record = asRecord(value)
  const state = stringOr(pick(record, ['state', 'status']), 'queued')
  return {
    id: stringOr(pick(record, ['id', 'intervention_id']), `intervention-${index + 1}`),
    label: stringOr(pick(record, ['label', 'name', 'action']), 'Intervention'),
    target: stringOr(pick(record, ['target', 'target_id']), 'Unspecified target'),
    state: state === 'applied' || state === 'observed' ? state : 'queued',
    timestamp: stringOr(pick(record, ['timestamp', 'created_at']), 'Just now'),
    effect: typeof pick(record, ['effect', 'result']) === 'string' ? pick(record, ['effect', 'result']) as string : undefined,
  }
}

const normalizeMind = (value: unknown, index: number): AgentMind => {
  const record = asRecord(value)
  return {
    id: stringOr(pick(record, ['id', 'agent_id']), `agent-${index + 1}`),
    name: stringOr(pick(record, ['name', 'label']), `Agent ${index + 1}`),
    role: stringOr(pick(record, ['role', 'archetype']), 'Adaptive agent'),
    mood: stringOr(pick(record, ['mood', 'state']), 'Undetermined'),
    attention: numberOr(pick(record, ['attention', 'attention_level']), 0.5),
    openness: numberOr(pick(record, ['openness', 'openness_level']), 0.5),
    conviction: numberOr(pick(record, ['conviction', 'conviction_level']), 0.5),
    memory: arrayOr(pick(record, ['memory', 'recent_memory'])).map((item) => String(item)),
    activeDrivers: arrayOr(pick(record, ['active_drivers', 'activeDrivers', 'drivers'])).map((item) => String(item)),
    latestTrace: asRecord(pick(record, ['latestTrace', 'latest_trace'])) as AgentMind['latestTrace'],
  }
}

const normalizePost = (value: unknown, index: number) => {
  const record = asRecord(value)
  return {
    id: stringOr(pick(record, ['id']), `content-${index + 1}`),
    author_id: stringOr(pick(record, ['author_id', 'authorId']), 'unknown'),
    author: stringOr(pick(record, ['author', 'author_name']), 'Unknown citizen'),
    kind: stringOr(pick(record, ['kind', 'type']), 'post'),
    topic: stringOr(pick(record, ['topic']), 'unclassified'),
    text: stringOr(pick(record, ['text']), 'No content returned.'),
    claim_truth: typeof pick(record, ['claim_truth', 'claimTruth']) === 'boolean' ? pick(record, ['claim_truth', 'claimTruth']) as boolean : null,
    parent_id: typeof pick(record, ['parent_id', 'parentId']) === 'string' ? pick(record, ['parent_id', 'parentId']) as string : null,
    created_tick: numberOr(pick(record, ['created_tick', 'createdTick']), 0),
    credibility: numberOr(pick(record, ['credibility']), 0.5),
    likes: numberOr(pick(record, ['likes']), 0),
    fact_checked: Boolean(pick(record, ['fact_checked', 'factChecked'])),
    is_counterevidence: Boolean(pick(record, ['is_counterevidence', 'isCounterevidence'])),
    exposureCount: numberOr(pick(record, ['exposureCount', 'exposure_count']), 0),
  }
}

const normalizeEvent = (value: unknown, index: number): WorldEvent => {
  const record = asRecord(value)
  return {
    event_id: stringOr(pick(record, ['event_id', 'id']), `event-${index + 1}`),
    tick: numberOr(pick(record, ['tick']), 0),
    actor_id: stringOr(pick(record, ['actor_id', 'actorId']), 'unknown'),
    action: stringOr(pick(record, ['action']), 'observe'),
    target_id: typeof pick(record, ['target_id', 'targetId']) === 'string' ? pick(record, ['target_id', 'targetId']) as string : null,
    content_id: typeof pick(record, ['content_id', 'contentId']) === 'string' ? pick(record, ['content_id', 'contentId']) as string : null,
    topic: typeof pick(record, ['topic']) === 'string' ? pick(record, ['topic']) as string : null,
    details: stringOr(pick(record, ['details']), 'State transition recorded.'),
  }
}

export const normalizeState = (payload: unknown, universeId: UniverseId): UniverseState => {
  const root = unwrap(payload)
  const metricsRecord = asRecord(pick(root, ['metrics', 'indicators'])) ?? root
  const rawMetrics: UniverseMetrics = {
    population: numberOr(pick(metricsRecord, ['population', 'population_count', 'citizens']), 0),
    activeAgents: numberOr(pick(metricsRecord, ['active_agents', 'activeAgents', 'agents']), 0),
    socialCohesion: numberOr(pick(metricsRecord, ['social_cohesion', 'socialCohesion', 'cohesion']), 0),
    trustIndex: numberOr(pick(metricsRecord, ['trust_index', 'trustIndex', 'trust']), 0),
    informationEntropy: numberOr(pick(metricsRecord, ['information_entropy', 'informationEntropy', 'entropy']), 0),
    interventionBudget: numberOr(pick(metricsRecord, ['intervention_budget', 'interventionBudget', 'budget']), 0),
  }
  return {
    universeId,
    label: stringOr(pick(root, ['label', 'name', 'universe']), universeId === 'prime' ? 'Prime' : 'Echo'),
    phase: stringOr(pick(root, ['phase', 'stage']), 'Simulation active'),
    tick: numberOr(pick(root, ['tick', 'step', 'cycle']), 0),
    updatedAt: stringOr(pick(root, ['updated_at', 'updatedAt', 'timestamp']), new Date().toISOString()),
    metrics: rawMetrics,
    nodes: arrayOr(pick(root, ['nodes', 'network_nodes', 'graph_nodes'])).map(normalizeNode),
    edges: arrayOr(pick(root, ['edges', 'network_edges', 'graph_edges'])).map(normalizeEdge),
    citizens: arrayOr(pick(root, ['citizens', 'people'])).map(normalizeCitizen),
    sources: arrayOr(pick(root, ['sources', 'information_sources', 'channels'])).map(normalizeSource),
    experiments: arrayOr(pick(root, ['experiments', 'scenarios'])).map(normalizeExperiment),
    interventions: arrayOr(pick(root, ['interventions', 'actions'])).map(normalizeIntervention),
    minds: arrayOr(pick(root, ['minds', 'agents', 'agent_minds'])).map(normalizeMind),
    posts: arrayOr(pick(root, ['posts', 'feed'])).map(normalizePost),
    events: arrayOr(pick(root, ['events', 'event_stream'])).map(normalizeEvent),
    communities: arrayOr(pick(root, ['communities'])).map((value) => value as NonNullable<UniverseState['communities']>[number]),
  }
}

const encodeUniverse = (universeId: UniverseId) => encodeURIComponent(universeId)

export const api = {
  getHealth: () => request<HealthResponse>('/health'),
  getState: async (universeId: UniverseId) => normalizeState(await request<unknown>(`/state?universe_id=${encodeUniverse(universeId)}`), universeId),
  getExperiments: async (universeId: UniverseId) => {
    const payload = await request<unknown>(`/experiments?universe_id=${encodeUniverse(universeId)}`)
    const root = unwrap(payload)
    const rows = arrayOr(root.experiments ?? payload)
    return rows.map(normalizeExperiment)
  },
  getCitizens: async (universeId: UniverseId) => {
    const payload = await request<unknown>(`/citizens?universe_id=${encodeUniverse(universeId)}`)
    const root = unwrap(payload)
    return arrayOr(root.citizens ?? payload).map(normalizeCitizen)
  },
  getCitizen: async (universeId: UniverseId, citizenId: string) => {
    const payload = await request<unknown>(`/citizens/${encodeURIComponent(citizenId)}?universe_id=${encodeUniverse(universeId)}`)
    return normalizeCitizen(payload, 0)
  },
  tick: (universeId: UniverseId, steps = 1) => request<unknown>('/tick', { method: 'POST', body: JSON.stringify({ universe_id: universeId, steps }) }),
  reset: (universeId: UniverseId) => request<unknown>('/reset', { method: 'POST', body: JSON.stringify({ universe_id: universeId }) }),
  runExperiment: (universeId: UniverseId, experimentId: string) => request<ExperimentComparison>('/experiments/run', { method: 'POST', body: JSON.stringify({ universe_id: universeId, experiment_id: experimentId }) }),
  intervene: (universeId: UniverseId, input: { action: string; target: string; intensity: number; rationale: string }) => request<unknown>('/interventions', { method: 'POST', body: JSON.stringify({ universe_id: universeId, ...input }) }),
}
