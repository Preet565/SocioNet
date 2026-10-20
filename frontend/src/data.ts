import type {
  AgentMind,
  Citizen,
  Experiment,
  InformationSource,
  InterventionRecord,
  NetworkEdge,
  NetworkNode,
  UniverseId,
  UniverseState,
} from './types'

const citizens: Citizen[] = [
  { id: 'ava-chen', name: 'Ava Chen', handle: '@avachen', archetype: 'Bridge Builder', location: 'Toronto / UTC−4', status: 'influential', sentiment: 0.68, trust: 0.82, reach: 0.74, beliefs: ['Local-first systems', 'Open knowledge'], lastAction: 'Shared a neighborhood mutual-aid map' },
  { id: 'marco-ibarra', name: 'Marco Ibarra', handle: '@marco_i', archetype: 'Signal Amplifier', location: 'Mexico City / UTC−6', status: 'active', sentiment: 0.41, trust: 0.57, reach: 0.62, beliefs: ['Economic mobility', 'Visible accountability'], lastAction: 'Questioned a high-velocity narrative' },
  { id: 'noor-patel', name: 'Noor Patel', handle: '@noor.p', archetype: 'Quiet Observer', location: 'London / UTC+1', status: 'observed', sentiment: 0.54, trust: 0.76, reach: 0.31, beliefs: ['Evidence before action', 'Plural perspectives'], lastAction: 'Bookmarked three competing sources' },
  { id: 'theo-wright', name: 'Theo Wright', handle: '@theow', archetype: 'Community Steward', location: 'Melbourne / UTC+11', status: 'active', sentiment: 0.72, trust: 0.64, reach: 0.55, beliefs: ['Care is infrastructure', 'Slow consensus'], lastAction: 'Convened a local listening circle' },
  { id: 'linh-nguyen', name: 'Linh Nguyen', handle: '@linh.n', archetype: 'Pattern Seeker', location: 'Singapore / UTC+8', status: 'influential', sentiment: 0.29, trust: 0.69, reach: 0.79, beliefs: ['Systems thinking', 'Useful friction'], lastAction: 'Mapped a cross-community information path' },
  { id: 'samir-okafor', name: 'Samir Okafor', handle: '@samir.o', archetype: 'Context Keeper', location: 'Lagos / UTC+1', status: 'quiet', sentiment: 0.59, trust: 0.88, reach: 0.48, beliefs: ['Memory matters', 'Repair over spectacle'], lastAction: 'Added historical context to a live thread' },
]

const nodes: NetworkNode[] = [
  { id: 'ava-chen', label: 'Ava Chen', kind: 'citizen', influence: 0.74, sentiment: 0.68, x: 19, y: 40, group: 'care' },
  { id: 'marco-ibarra', label: 'Marco Ibarra', kind: 'citizen', influence: 0.62, sentiment: 0.41, x: 37, y: 67, group: 'signal' },
  { id: 'noor-patel', label: 'Noor Patel', kind: 'citizen', influence: 0.31, sentiment: 0.54, x: 49, y: 23, group: 'observe' },
  { id: 'theo-wright', label: 'Theo Wright', kind: 'citizen', influence: 0.55, sentiment: 0.72, x: 64, y: 72, group: 'care' },
  { id: 'linh-nguyen', label: 'Linh Nguyen', kind: 'citizen', influence: 0.79, sentiment: 0.29, x: 78, y: 34, group: 'signal' },
  { id: 'samir-okafor', label: 'Samir Okafor', kind: 'citizen', influence: 0.48, sentiment: 0.59, x: 87, y: 68, group: 'context' },
  { id: 'commons-lab', label: 'Commons Lab', kind: 'institution', influence: 0.86, sentiment: 0.61, x: 51, y: 49, group: 'care' },
  { id: 'open-wire', label: 'Open Wire', kind: 'media', influence: 0.71, sentiment: 0.36, x: 29, y: 17, group: 'signal' },
]

const edges: NetworkEdge[] = [
  { source: 'ava-chen', target: 'commons-lab', weight: 0.82, relation: 'organizes' },
  { source: 'ava-chen', target: 'open-wire', weight: 0.44, relation: 'shares' },
  { source: 'marco-ibarra', target: 'open-wire', weight: 0.67, relation: 'questions' },
  { source: 'marco-ibarra', target: 'linh-nguyen', weight: 0.38, relation: 'responds' },
  { source: 'noor-patel', target: 'commons-lab', weight: 0.34, relation: 'observes' },
  { source: 'noor-patel', target: 'samir-okafor', weight: 0.52, relation: 'checks' },
  { source: 'theo-wright', target: 'commons-lab', weight: 0.71, relation: 'stewards' },
  { source: 'theo-wright', target: 'samir-okafor', weight: 0.45, relation: 'listens' },
  { source: 'linh-nguyen', target: 'commons-lab', weight: 0.76, relation: 'maps' },
  { source: 'linh-nguyen', target: 'open-wire', weight: 0.59, relation: 'tracks' },
  { source: 'samir-okafor', target: 'open-wire', weight: 0.46, relation: 'contexts' },
]

const sources: InformationSource[] = [
  { id: 'open-wire', name: 'Open Wire', type: 'Independent media', reach: 0.71, velocity: 0.82, credibility: 0.63, signal: 'rising' },
  { id: 'civic-ledger', name: 'Civic Ledger', type: 'Public record', reach: 0.39, velocity: 0.28, credibility: 0.91, signal: 'stable' },
  { id: 'neighborhood-loop', name: 'Neighborhood Loop', type: 'Community channel', reach: 0.56, velocity: 0.61, credibility: 0.78, signal: 'rising' },
  { id: 'long-view', name: 'The Long View', type: 'Analysis collective', reach: 0.47, velocity: 0.34, credibility: 0.86, signal: 'stable' },
  { id: 'quicktake', name: 'Quicktake', type: 'Viral feed', reach: 0.88, velocity: 0.94, credibility: 0.32, signal: 'fading' },
]

const experiments: Experiment[] = [
  { id: 'same-seed', name: 'Same seed, different context', description: 'Hold the initial signal constant and trace how two universes metabolize it.', status: 'ready', variables: ['Seed signal', 'Network topology', 'Trust baseline'], projectedImpact: 'Divergence trace' },
  { id: 'slow-signal', name: 'Slow the signal', description: 'Reduce information velocity at one bridge and observe downstream resilience.', status: 'ready', variables: ['Velocity cap', 'Bridge node', 'Observation window'], projectedImpact: 'Cascades contained' },
  { id: 'care-network', name: 'Strengthen care ties', description: 'Increase the visibility of reciprocal support without changing the narrative mix.', status: 'complete', variables: ['Reciprocity', 'Local visibility', 'Community scope'], projectedImpact: 'Cohesion response', lastRun: '14 min ago' },
]

const interventions: InterventionRecord[] = [
  { id: 'int-104', label: 'Context card placed', target: 'Open Wire', state: 'observed', timestamp: '8 min ago', effect: 'Lowered interpretation volatility' },
  { id: 'int-103', label: 'Bridge node highlighted', target: 'Commons Lab', state: 'applied', timestamp: '21 min ago', effect: 'Increased cross-cluster visibility' },
  { id: 'int-102', label: 'Listening prompt seeded', target: 'Neighborhood Loop', state: 'observed', timestamp: '42 min ago', effect: 'Invited slower replies' },
]

const minds: AgentMind[] = [
  { id: 'ava-chen', name: 'Ava Chen', role: 'Bridge Builder', mood: 'Attentive', attention: 0.78, openness: 0.86, conviction: 0.63, memory: ['Mutual-aid map found unexpected reach', 'Commons Lab responded within one cycle'], activeDrivers: ['Reciprocity', 'Visible care', 'Local proof'] },
  { id: 'linh-nguyen', name: 'Linh Nguyen', role: 'Pattern Seeker', mood: 'Restless', attention: 0.91, openness: 0.62, conviction: 0.74, memory: ['Three clusters share an information bridge', 'Velocity is outpacing context'], activeDrivers: ['Novelty', 'Coherence', 'Leverage'] },
  { id: 'samir-okafor', name: 'Samir Okafor', role: 'Context Keeper', mood: 'Grounded', attention: 0.57, openness: 0.82, conviction: 0.69, memory: ['Historical note shifted a heated thread', 'Repair language travels slowly'], activeDrivers: ['Continuity', 'Repair', 'Trust'] },
]

// Preview-only deterministic seeds. Live state is always sourced from the API first.
const baseMetrics = (universeId: UniverseId) => universeId === 'prime'
  ? { population: 100, activeAgents: 100, socialCohesion: 0.64, trustIndex: 0.58, informationEntropy: 0.44, interventionBudget: 0.73 }
  : { population: 100, activeAgents: 100, socialCohesion: 0.49, trustIndex: 0.43, informationEntropy: 0.68, interventionBudget: 0.61 }

export const buildFallbackState = (universeId: UniverseId): UniverseState => {
  const previewCitizens = Array.from({ length: 100 }, (_, index) => {
    const base = citizens[index % citizens.length]
    const serial = String(index + 1).padStart(3, '0')
    return { ...base, id: `preview-citizen-${serial}`, name: `Citizen ${serial}`, handle: `@citizen_${serial}` }
  })
  const previewNodes = Array.from({ length: 100 }, (_, index) => {
    const serial = String(index + 1).padStart(3, '0')
    const angle = index / 100 * Math.PI * 2
    return { id: `preview-citizen-${serial}`, label: `Citizen ${serial}`, kind: 'citizen' as const, influence: 0.25 + (index % 11) / 20, sentiment: 0.35 + (index % 13) / 20, x: 50 + Math.cos(angle) * 40, y: 50 + Math.sin(angle) * 40, group: index % 3 === 0 ? 'care' : index % 3 === 1 ? 'signal' : 'observe' }
  })
  const previewEdges = previewNodes.slice(0, 99).map((node, index) => ({ source: node.id, target: previewNodes[index + 1].id, weight: 0.2 + (index % 7) / 10, relation: 'preview tie' }))
  return {
  universeId,
   label: universeId === 'prime' ? 'Prime / offline sample' : 'Echo / offline sample',
  phase: universeId === 'prime' ? 'Signal propagation' : 'Trust recalibration',
  tick: 0,
  updatedAt: '2026-02-18T14:32:00.000Z',
  metrics: baseMetrics(universeId),
  nodes: previewNodes,
  edges: previewEdges,
  citizens: previewCitizens.map((citizen) => ({ ...citizen, beliefs: [...citizen.beliefs] })),
  sources: sources.map((source) => ({ ...source })),
   experiments: experiments.map((experiment) => ({ ...experiment, status: 'ready', lastRun: undefined, variables: [...experiment.variables] })),
   interventions: interventions.map((intervention) => ({ ...intervention, state: 'queued', timestamp: 'preview sample', effect: 'Illustrative offline sample; not recorded.' })),
  minds: minds.map((mind) => ({ ...mind, memory: [...mind.memory], activeDrivers: [...mind.activeDrivers] })),
  }
}

export const FALLBACK_UNIVERSES: Record<UniverseId, UniverseState> = {
  prime: buildFallbackState('prime'),
  echo: buildFallbackState('echo'),
}

export const getFallbackCitizen = (universeId: UniverseId, id: string): Citizen | undefined =>
  buildFallbackState(universeId).citizens.find((citizen) => citizen.id === id)
