import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiResponseError, ApiUnavailableError, API_URL } from './api'
import { buildFallbackState, getFallbackCitizen } from './data'
import { Icon, type IconName } from './icons'
import ExperimentBuilder from './ExperimentBuilder'
import { consoleActions, consoleInputValid, projectionDefinitions } from './viewHelpers'
import { formatMetric, metricTimeline } from './multiExperimentHelpers'
import type {
  Citizen,
  BranchComparison,
  CausalGraph,
  ExperimentBatch,
  Experiment,
  ExperimentComparison,
  ExperimentHistory,
  ExperimentRun,
  ImpactSummary,
  InterventionRecord,
  NetworkNode,
  SelectedCitizen,
  UniverseId,
  UniverseState,
  ViewId,
} from './types'

const DEMO_TICKS = 3

const viewMeta: Record<ViewId, { label: string; eyebrow: string; icon: IconName; description: string }> = {
  overview: { label: 'Civilization Overview', eyebrow: 'Command center', icon: 'command', description: 'Read the current state of the simulated civilization.' },
  social: { label: 'Social Universe', eyebrow: 'Topology', icon: 'network', description: 'Trace the relationships that move a signal.' },
  citizens: { label: 'Citizen Observatory', eyebrow: 'Population', icon: 'eye', description: 'Inspect the people and agents shaping the moment.' },
  information: { label: 'Information Ecosystem', eyebrow: 'Signal layer', icon: 'layers', description: 'Follow narratives, sources, and velocity.' },
  lab: { label: 'Butterfly Lab', eyebrow: 'Counterfactuals', icon: 'flask', description: 'Run reproducible experiments across universes.' },
  interventions: { label: 'Intervention Console', eyebrow: 'Operator tools', icon: 'target', description: 'Make a deliberate change and observe its wake.' },
  mind: { label: 'Agent Mind Explorer', eyebrow: 'Cognition', icon: 'brain', description: 'Open the internal state of selected agents.' },
}

const primaryNav: ViewId[] = ['overview', 'social', 'citizens', 'information']
const experimentNav: ViewId[] = ['lab', 'interventions', 'mind']

type Toast = { tone: 'success' | 'warning' | 'error'; message: string } | null

function formatNumber(value: number) {
  if (!Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(value)
}

function formatPercent(value: number) {
  if (!Number.isFinite(value)) return '—'
  return `${Math.round(value * 100)}%`
}

function formatDateTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(date)
}

function clamp(value: number) {
  return Math.max(0, Math.min(1, value))
}

function isUnavailable(error: unknown): error is ApiUnavailableError {
  return error instanceof ApiUnavailableError
}

function MetricCard({ label, value, detail, icon, accent = 'violet', definition }: { label: string; value: string; detail: string; icon: IconName; accent?: string; definition?: string }) {
  return <article className={`metric-card accent-${accent}`}>
    <div className="metric-card__head"><span>{label}</span><Icon name={icon} size={16} /></div>
    <strong>{value}</strong>
    <div className="metric-card__detail">{detail}</div>
    {definition && <span className="sr-only">Definition: {definition}</span>}
  </article>
}

function ProgressBar({ value, tone = 'violet', label = 'Indicator' }: { value: number; tone?: string; label?: string }) {
  return <div className={`progress ${tone}`} aria-label={label} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(clamp(value) * 100)}><span style={{ width: `${clamp(value) * 100}%` }} /></div>
}

function StatusPill({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: 'neutral' | 'green' | 'amber' | 'violet' | 'red' }) {
  return <span className={`status-pill ${tone}`}><span className="status-pill__dot" />{children}</span>
}

function EmptyState({ title, body, action }: { title: string; body: string; action?: React.ReactNode }) {
  return <div className="empty-state"><div className="empty-state__icon"><Icon name="spark" size={22} /></div><h3>{title}</h3><p>{body}</p>{action}</div>
}

function ErrorBanner({ message, onDismiss }: { message: string; onDismiss?: () => void }) {
  return <div className="error-banner" role="alert"><Icon name="activity" size={16} /><span>{message}</span>{onDismiss && <button className="icon-button" aria-label="Dismiss error" onClick={onDismiss}><Icon name="close" size={16} /></button>}</div>
}

function SectionHeader({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: React.ReactNode }) {
  return <div className="section-header"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2><p>{description}</p></div>{action}</div>
}

function PanelHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: React.ReactNode }) {
  return <div className="panel-header"><div><h3>{title}</h3>{subtitle && <p>{subtitle}</p>}</div>{action}</div>
}

function NetworkGraph({ nodes, edges, selectedId, onSelect }: { nodes: NetworkNode[]; edges: UniverseState['edges']; selectedId?: string; onSelect?: (node: NetworkNode) => void }) {
  const width = 820
  const height = 440
  const points = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>()
    nodes.forEach((node, index) => {
      const fallbackAngle = (index / Math.max(nodes.length, 1)) * Math.PI * 2 - Math.PI / 2
      const fallbackRadius = 130 + (index % 3) * 22
      const rawX = typeof node.x === 'number' && Number.isFinite(node.x) ? node.x : 50 + Math.cos(fallbackAngle) * (fallbackRadius / 5)
      const rawY = typeof node.y === 'number' && Number.isFinite(node.y) ? node.y : 50 + Math.sin(fallbackAngle) * (fallbackRadius / 4)
      const x = rawX <= 1 ? rawX * 100 : rawX
      const y = rawY <= 1 ? rawY * 100 : rawY
      map.set(node.id, { x: 44 + (x - 50) * 0.78, y: 50 + (y - 50) * 0.78 })
    })
    return map
  }, [nodes])

  const kindColor = (kind: NetworkNode['kind']) => ({ citizen: '#bba8ff', institution: '#60d7c6', media: '#ffb86b', community: '#93b7ff', agent: '#f18bc8', unknown: '#8992a5' })[kind]

  if (!nodes.length) return <EmptyState title="No topology in this tick" body="The API returned no network nodes. Advance the simulation or check the graph payload." />

  return <div className="network-graph-wrap">
    <svg className="network-graph" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Network graph with ${nodes.length} nodes and ${edges.length} connections`}>
      <defs>
        <radialGradient id="graphGlow"><stop stopColor="#9577ff" stopOpacity=".22" /><stop offset="1" stopColor="#9577ff" stopOpacity="0" /></radialGradient>
        <filter id="softGlow"><feGaussianBlur stdDeviation="4" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
      </defs>
      <circle cx="410" cy="220" r="185" fill="url(#graphGlow)" />
      <g className="graph-grid"><path d="M0 110H820M0 220H820M0 330H820M205 0V440M410 0V440M615 0V440" /></g>
      <g className="graph-edges">
        {edges.map((edge, index) => {
          const source = points.get(edge.source)
          const target = points.get(edge.target)
          if (!source || !target) return null
          return <line key={`${edge.source}-${edge.target}-${index}`} x1={source.x * 8.2} y1={source.y * 4.4} x2={target.x * 8.2} y2={target.y * 4.4} strokeWidth={1 + clamp(edge.weight) * 3.5} opacity={0.18 + clamp(edge.weight) * 0.4} />
        })}
      </g>
      <g className="graph-nodes">
        {nodes.map((node) => {
          const point = points.get(node.id)
          if (!point) return null
          const x = point.x * 8.2
          const y = point.y * 4.4
          const radius = 7 + clamp(node.influence) * 12
          const active = selectedId === node.id
          return <g key={node.id} className={`graph-node ${active ? 'selected' : ''}`} transform={`translate(${x} ${y})`} role="button" tabIndex={0} aria-pressed={active} aria-label={`Select ${node.label}, ${node.kind}`} onClick={() => onSelect?.(node)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect?.(node) } }}>
            {active && <circle className="graph-node__ring" r={radius + 8} />}
            <circle className="graph-node__halo" r={radius + 7} fill={kindColor(node.kind)} />
            <circle className="graph-node__core" r={radius} fill={kindColor(node.kind)} filter="url(#softGlow)" />
            <circle className="graph-node__pin" r={3} />
            <text x={radius + 8} y="4">{node.label}</text>
          </g>
        })}
      </g>
    </svg>
    <div className="graph-legend">
      {(['citizen', 'institution', 'media', 'community'] as NetworkNode['kind'][]).map((kind) => <span key={kind}><i style={{ background: kindColor(kind) }} />{kind}</span>)}
      <span className="graph-legend__meta"><Icon name="link" size={13} /> edge thickness = relationship weight</span>
    </div>
  </div>
}

export function OverviewView({ state, otherState, offline, onDemo, demoRunning, onNavigate, onTick, onReset }: { state: UniverseState; otherState: UniverseState; offline: boolean; onDemo: () => void; demoRunning: boolean; onNavigate: (view: ViewId) => void; onTick: () => void; onReset: () => void }) {
  const { metrics } = state
  return <>
    <div className="hero-grid">
      <div className="hero-copy">
        <span className="eyebrow"><span className="eyebrow-dot" /> Civilization state / {state.label}</span>
        <h1>Every signal<br /><em>leaves a trace.</em></h1>
        <p className="hero-lede">Observe how tiny interventions compound across a living social universe. The command center is tracking the present moment at the speed of the simulation.</p>
        <div className="hero-actions"><button className="button primary" onClick={onTick}><Icon name="play" size={15} /> Advance one tick</button><button className="button ghost" onClick={() => onNavigate('social')}><Icon name="network" size={15} /> Open topology</button></div>
        <div className="hero-meta"><span><i className="live-dot" />{offline ? 'Preview data' : 'Simulation connected'}</span><span className="meta-divider" /><span>tick {formatNumber(state.tick)}</span><span className="meta-divider" /><span>{state.phase}</span></div>
      </div>
      <div className="hero-orbit" aria-hidden="true"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="orbit orbit-three" /><div className="orbit-core"><Icon name="spark" size={30} /></div><span className="orbit-label orbit-label-a">signal</span><span className="orbit-label orbit-label-b">trust</span><span className="orbit-label orbit-label-c">care</span></div>
    </div>

    <div className="metric-grid">
      <MetricCard label="Population" value={formatNumber(metrics.population)} detail={`${formatNumber(metrics.activeAgents)} active agents`} icon="users" accent="violet" definition="Total simulated citizens in the universe." />
      <MetricCard label="Relationship affinity" value={formatPercent(metrics.socialCohesion)} detail="Mean affinity, rescaled to 0–1" icon="link" accent="teal" definition={projectionDefinitions.cohesion} />
      <MetricCard label="Relationship trust" value={formatPercent(metrics.trustIndex)} detail="Mean trust / baseline fallback" icon="target" accent="amber" definition={projectionDefinitions.trust} />
      <MetricCard label="Evidence complement" value={formatPercent(metrics.informationEntropy)} detail="1 − supporting-evidence share" icon="activity" accent="pink" definition={projectionDefinitions.evidence} />
    </div>

    <section className="demo-banner panel-glow">
      <div className="demo-banner__copy"><div className="demo-kicker"><Icon name="spark" size={14} /> Reproducible detached-branch demo</div><h2>One source. Two experiment branches.</h2><p>Reset both live universes using their own seeds and configurations, advance each three ticks, then fork control and treatment from Prime. Inspect the saved branch result in Butterfly Lab.</p><button className="button light" onClick={onDemo} disabled={demoRunning || offline}>{demoRunning ? <><span className="spinner light-spinner" /> Running sequence…</> : <><Icon name="play" size={14} /> Run the demo flow</>}</button></div>
      <div className="demo-flow" aria-label="Four step demo flow"><DemoStep step="01" title="Reset" caption="both live universes" active={demoRunning} /><div className="flow-line" /><DemoStep step="02" title={`Tick ×${DEMO_TICKS}`} caption="advance each world" active={demoRunning} /><div className="flow-line" /><DemoStep step="03" title="Fork Prime" caption="control & treatment" active={demoRunning} /><div className="flow-line" /><DemoStep step="04" title="Compare" caption="saved branches" active={false} /></div>
    </section>

    <div className="overview-lower">
      <section className="panel state-panel"><PanelHeader title="Universe telemetry" subtitle="Live indicators from the selected frame" action={<button className="text-button" onClick={() => onNavigate('information')}>View ecosystem <Icon name="arrow" size={14} /></button>} /><div className="telemetry-row"><span>Intervention indicator</span><strong>{formatPercent(metrics.interventionBudget)}</strong><ProgressBar label="Intervention indicator" value={metrics.interventionBudget} tone="teal" /></div><div className="telemetry-row"><span>Selected live frame</span><strong>{state.label}</strong><span className="telemetry-caption">Updated {formatDateTime(state.updatedAt)}</span></div><div className="telemetry-row"><span>Other live universe</span><strong>{otherState.label}</strong><span className="telemetry-caption">tick {formatNumber(otherState.tick)}</span></div><button className="reset-button" onClick={onReset}><Icon name="reset" size={14} /> Reset selected universe</button></section>
      <section className="panel definitions-panel"><PanelHeader title="Metric definitions" subtitle="Calculated backend projections" /><div className="definition-list"><DefinitionRow label="Relationship affinity" value="Affinity" text={projectionDefinitions.cohesion} /><DefinitionRow label="Relationship trust" value="Trust" text={projectionDefinitions.trust} /><DefinitionRow label="Evidence complement" value="Evidence" text={projectionDefinitions.evidence} /><DefinitionRow label="Intervention indicator" value="Display only" text={projectionDefinitions.budget} /></div><button className="text-button" onClick={() => onNavigate('social')}>Read the network <Icon name="arrow" size={14} /></button></section>
    </div>
    <section className="panel event-stream-panel"><PanelHeader title="World event stream" subtitle="The latest recorded state transitions" action={<button className="text-button" onClick={() => onNavigate('information')}>Open feed <Icon name="arrow" size={14} /></button>} />{state.events?.length ? <div className="event-stream">{state.events.slice(-8).reverse().map((event) => <div className="event-row" key={event.event_id}><span className="event-tick">T{event.tick}</span><div><strong>{event.action.replaceAll('_', ' ')}</strong><span>{event.actor_id}{event.topic ? ` · ${event.topic}` : ''}</span></div><p>{event.details}</p></div>)}</div> : <EmptyState title="No events recorded" body="Advance one tick to populate the causal event stream." />}</section>
  </>
}

function DemoStep({ step, title, caption, active }: { step: string; title: string; caption: string; active: boolean }) {
  return <div className={`demo-step ${active ? 'active' : ''}`}><span>{step}</span><strong>{title}</strong><small>{caption}</small></div>
}

function DefinitionRow({ label, value, text }: { label: string; value: string; text: string }) {
  return <div className="definition-row"><div><strong>{label}</strong><span>{value}</span></div><p>{text}</p></div>
}

function SocialUniverseView({ state }: { state: UniverseState }) {
  const [selectedId, setSelectedId] = useState<string | undefined>(state.nodes[0]?.id)
  useEffect(() => {
    if (!state.nodes.some((node) => node.id === selectedId)) setSelectedId(state.nodes[0]?.id)
  }, [state.universeId, state.nodes, selectedId])
  const selectedNode = state.nodes.find((node) => node.id === selectedId)
  return <>
     <SectionHeader eyebrow="Topology / relationships" title="Social Universe" description="The graph is rendered from the active universe’s nodes and edges. Select a node to inspect its local signal." action={<StatusPill tone="green">{state.nodes.length} nodes · {state.edges.length} edges</StatusPill>} />
      <div className="social-grid"><section className="panel graph-panel"><PanelHeader title="Relationship topology" subtitle={`Tick ${formatNumber(state.tick)} · edge thickness tracks weight`} /><NetworkGraph nodes={state.nodes} edges={state.edges} selectedId={selectedId} onSelect={(node) => setSelectedId(node.id)} /></section><aside className="panel node-detail">{selectedNode ? <><span className="eyebrow">Selected node</span><div className="node-detail__title"><div className="node-avatar" style={{ '--node-color': selectedNode.kind === 'media' ? '#ffb86b' : selectedNode.kind === 'institution' ? '#60d7c6' : '#bba8ff' } as React.CSSProperties}>{selectedNode.label.slice(0, 1)}</div><div><h3>{selectedNode.label}</h3><p>{selectedNode.kind} · {selectedNode.group || 'unclustered'}</p></div></div><div className="detail-stat" title={projectionDefinitions.influence}><span>Influence score</span><strong>{formatPercent(selectedNode.influence)}</strong><ProgressBar label="Influence score" value={selectedNode.influence} /></div><div className="detail-stat" title={projectionDefinitions.stance}><span>Belief stance</span><strong>{formatPercent(selectedNode.sentiment)}</strong><ProgressBar label="Belief stance" value={selectedNode.sentiment} tone="amber" /></div><div className="node-detail__note"><Icon name="link" size={15} /><span>{selectedNode.degree ?? state.edges.filter((edge) => edge.source === selectedNode.id || edge.target === selectedNode.id).length} degree · {formatPercent(selectedNode.betweenness ?? 0)} betweenness</span></div></> : <EmptyState title="Select a node" body="Choose any node in the graph to inspect its local signal." />}</aside></div>
     <section className="panel graph-insight"><div className="insight-icon"><Icon name="spark" size={18} /></div><div><span className="eyebrow">Reading the graph</span><h3>Small bridges can carry civilization-scale change.</h3><p>Nodes are entities, edges are observed relationships, and edge weight is the relative strength of a connection. Geometry is a view of the current API payload, not a fixed illustration.</p></div><div className="insight-stat"><strong>{formatPercent(state.metrics.socialCohesion)}</strong><span>cohesion index</span></div><div className="insight-stat"><strong>{state.graphMetrics ? state.graphMetrics.averageDegree.toFixed(1) : '—'}</strong><span>average degree</span></div><div className="insight-stat"><strong>{formatPercent(state.graphMetrics?.networkDensity ?? 0)}</strong><span>network density</span></div></section>
  </>
}

function CitizenObservatoryView({ state, selected, onSelect, onNavigate }: { state: UniverseState; selected?: SelectedCitizen; onSelect: (citizen: Citizen) => void; onNavigate: (view: ViewId) => void }) {
  const [query, setQuery] = useState('')
  const filtered = state.citizens.filter((citizen) => `${citizen.name} ${citizen.handle} ${citizen.archetype}`.toLowerCase().includes(query.toLowerCase()))
  return <>
    <SectionHeader eyebrow="Population / live roster" title="Citizen Observatory" description="A readable view into the actors whose choices create the simulation’s next state." action={<button className="button ghost small" onClick={() => onNavigate('mind')}><Icon name="brain" size={14} /> Explore agent minds</button>} />
    <div className="observatory-layout"><section className="panel roster-panel"><PanelHeader title="Observed citizens" subtitle={`${filtered.length} of ${state.citizens.length} visible`} action={<label className="search-input"><Icon name="search" size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search roster" aria-label="Search citizens" /></label>} />{filtered.length ? <div className="citizen-list">{filtered.map((citizen) => <button key={citizen.id} className={`citizen-row ${selected?.id === citizen.id ? 'selected' : ''}`} onClick={() => onSelect(citizen)}><div className="citizen-avatar">{citizen.name.split(' ').map((part) => part[0]).join('')}</div><div className="citizen-row__identity"><strong>{citizen.name}</strong><span>{citizen.handle} · {citizen.archetype}</span></div><div className="citizen-row__signal"><StatusPill tone={citizen.status === 'influential' ? 'violet' : citizen.status === 'active' ? 'green' : 'neutral'}>{citizen.status}</StatusPill><small>{formatPercent(citizen.reach)} reach</small></div><Icon name="chevron" size={15} /></button>)}</div> : <EmptyState title="No citizens match" body="Try a different name, handle, or archetype." />}</section><aside className="panel citizen-profile">{selected ? <CitizenProfile citizen={selected} /> : <EmptyState title="Choose a citizen" body="Select a row to open the detailed citizen signal." />}</aside></div>
  </>
}

export function CitizenProfile({ citizen }: { citizen: SelectedCitizen }) {
  const decision = citizen.decisionTrace?.slice(-1)[0]
  return <>
    <div className="profile-top"><div className="profile-avatar">{citizen.name.split(' ').map((part) => part[0]).join('')}</div><div><span className="eyebrow">Citizen profile</span><h3>{citizen.name}</h3><p>{citizen.handle} · {citizen.location}</p></div></div>
    <div className="profile-archetype"><span>{citizen.archetype}</span><StatusPill tone={citizen.status === 'influential' ? 'violet' : 'green'}>{citizen.status}</StatusPill></div>
    <div className="profile-metrics"><div title={projectionDefinitions.stance}><span>Belief stance</span><strong>{formatPercent(citizen.sentiment)}</strong><ProgressBar label="Belief stance" value={citizen.sentiment} tone="pink" /></div><div><span>Trust</span><strong>{formatPercent(citizen.trust)}</strong><ProgressBar label="Citizen trust" value={citizen.trust} tone="teal" /></div><div title={projectionDefinitions.influence}><span>Influence score</span><strong>{formatPercent(citizen.reach)}</strong><ProgressBar label="Influence score" value={citizen.reach} /></div></div>
    <div className="profile-block"><span className="eyebrow">Belief signals</span><div className="tag-list">{citizen.beliefs.length ? citizen.beliefs.map((belief) => <span key={belief} className="tag">{belief}</span>) : <span className="muted">No belief signals returned.</span>}</div></div>
    <div className="profile-block"><span className="eyebrow">Active drivers</span><div className="tag-list">{citizen.interests?.length ? citizen.interests.map((interest) => <span key={interest} className="tag accent">{interest}</span>) : <span className="muted">No interests returned.</span>}</div></div>
    <div className="profile-block"><span className="eyebrow">Memory & relationships</span><p className="profile-action">{citizen.memoryCount ?? 0} durable memories · {citizen.relationshipCount ?? 0} relationship signals</p>{citizen.memories?.slice(0, 2).map((memory) => <p className="profile-memory" key={memory.memory_id}>“{memory.summary}” <small>tick {memory.created_tick}</small></p>)}</div>
    <div className="profile-block"><span className="eyebrow">Decision trace</span><p className="profile-action">{citizen.lastAction}</p>{decision && <div className="trace-chip"><strong>{decision.phase}</strong><span>{decision.action || 'observed'} · tick {decision.tick}</span></div>}</div>
  </>
}

function InformationEcosystemView({ state }: { state: UniverseState }) {
  const sortedSources = [...state.sources].sort((a, b) => b.velocity - a.velocity)
  const claimMetrics = state.metricsDetail?.claim_metrics || {}
  return <>
    <SectionHeader eyebrow="Signal layer / narratives" title="Information Ecosystem" description="See where signals begin, how quickly they move, and how much context they carry." action={<div className="signal-summary"><span className="summary-dot rising" />{state.sources.filter((source) => source.signal === 'rising').length} rising signals</div>} />
     <div className="information-grid"><section className="panel source-panel"><PanelHeader title="Topic activity" subtitle="Sorted by calculated content activity" />{sortedSources.length ? <div className="source-list">{sortedSources.map((source) => <div className="source-row" key={source.id}><div className="source-row__main"><div className={`source-glyph ${source.signal}`}><Icon name={source.type.toLowerCase().includes('media') ? 'book' : 'layers'} size={16} /></div><div><strong>{source.name}</strong><span>{source.type}</span></div></div><div className="source-meter" title={projectionDefinitions.velocity}><div><span>Topic activity</span><strong>{formatPercent(source.velocity)}</strong></div><ProgressBar label="Topic activity" value={source.velocity} tone={source.signal === 'rising' ? 'pink' : source.signal === 'fading' ? 'amber' : 'violet'} /></div><div className="source-facts"><span>Reach <b>{formatPercent(source.reach)}</b></span><span>Source credibility <b>{formatPercent(source.credibility)}</b></span><StatusPill tone={source.signal === 'rising' ? 'green' : source.signal === 'fading' ? 'amber' : 'neutral'}>{source.signal}</StatusPill></div></div>)}</div> : <EmptyState title="No information sources" body="The current state has not returned any channels." />}</section><aside className="panel narrative-panel"><PanelHeader title="Signal hygiene" subtitle="Three useful questions for every cascade" /><div className="hygiene-list"><HygieneRow index="01" title="How active?" text={projectionDefinitions.velocity} /><HygieneRow index="02" title="How far?" text="Reach estimates the fraction of citizens in the source community." /><HygieneRow index="03" title="How grounded?" text="Credibility is the mean credibility of content in the topic; it does not establish truth." /></div><div className="note-card"><Icon name="spark" size={16} /><span>Context is an intervention. Activity and credibility are separate projections.</span></div></aside></div>
     <section className="panel ecosystem-footer"><div><span className="eyebrow">Current frame</span><h3>Information is a landscape, not a stream.</h3></div><div className="footer-stat"><strong>{formatPercent(state.metrics.informationEntropy)}</strong><span>evidence complement</span></div><div className="footer-stat"><strong>{formatPercent(state.metrics.trustIndex)}</strong><span>relationship trust</span></div></section>
     <section className="panel feed-panel"><PanelHeader title="Information feed" subtitle="Actual citizen content from the current simulation tick" />{state.posts?.length ? <div className="feed-list">{state.posts.slice(0, 12).map((post) => <FeedItem key={post.id} post={post} claimMetric={post.claim_id ? claimMetrics[post.claim_id] : undefined} />)}</div> : <EmptyState title="No content has been published" body="Advance the simulation to let citizens observe, post, reply, or fact-check." />}</section>
  </>
}

export function FeedItem({ post, claimMetric }: { post: NonNullable<UniverseState['posts']>[number]; claimMetric?: NonNullable<UniverseState['metricsDetail']>['claim_metrics'][string] }) {
  const evidence = post.is_counterevidence ? 'counterevidence' : post.fact_checked ? 'fact-checked in simulation' : post.claim_truth === true ? 'simulated truth: true' : post.claim_truth === false ? 'simulated truth: false' : 'no truth label'
  const evidenceTone = post.is_counterevidence || post.fact_checked ? 'green' : post.claim_truth === false ? 'amber' : 'neutral'
  return <article className="feed-item"><div className="feed-item__top"><div><strong>{post.author}</strong><span> · {post.kind} · tick {post.created_tick}</span></div><StatusPill tone={evidenceTone}>{evidence}</StatusPill></div><p>{post.text}</p><div className="feed-item__meta"><span>topic: <b>{post.topic}</b></span><span>credibility <b>{formatPercent(post.credibility)}</b></span><span>exposures <b>{formatNumber(claimMetric?.exposures ?? post.exposureCount)}</b></span><span>{post.parent_id ? 'reply chain' : 'origin signal'}</span>{claimMetric && <><span>claim <b>{claimMetric.status}</b></span><span>reshares <b>{formatNumber(claimMetric.reshares)}</b></span><span>corrections <b>{formatNumber(claimMetric.corrections)}</b></span></>}</div></article>
}

function HygieneRow({ index, title, text }: { index: string; title: string; text: string }) {
  return <div className="hygiene-row"><span>{index}</span><div><strong>{title}</strong><p>{text}</p></div></div>
}

export function ButterflyLabView({ state, otherState, universeId, offline, comparison, causalComparison, impactSummary, causalGraph, history, experimentHistory, batches, runningId, batchRunningId, onRun, onRunBatch, onBuilderBusy }: { state: UniverseState; otherState: UniverseState; universeId: UniverseId; offline: boolean; comparison?: ExperimentComparison; causalComparison?: BranchComparison; impactSummary?: ImpactSummary; causalGraph?: CausalGraph; history: ExperimentRun[]; experimentHistory?: ExperimentHistory; batches: ExperimentBatch[]; runningId?: string; batchRunningId?: string; onRun: (experiment: Experiment) => void; onRunBatch: (experiment: Experiment) => void; onBuilderBusy?: (busy: boolean) => void }) {
  return <>
    <SectionHeader eyebrow="Counterfactuals / controlled change" title="Butterfly Lab" description="Run the same question against the current universe and record what moves." action={<StatusPill tone="violet"><Icon name="flask" size={13} /> reproducible experiments</StatusPill>} />
    <ExperimentBuilder key={universeId} universeId={universeId} offline={offline} universeState={state} sourceStates={{ [state.universeId]: state, [otherState.universeId]: otherState }} onBusyChange={onBuilderBusy} />
    <section className="panel lab-intro"><div className="lab-intro__mark"><Icon name="spark" size={23} /></div><div><span className="eyebrow">Method note</span><h3>A good experiment changes one thing you can name.</h3><p>Every run is attached to a universe, a tick, and an explicit set of variables. Use the two-universe demo to establish a shared starting frame before comparing outcomes.</p></div><div className="lab-intro__tick"><span>Selected tick</span><strong>{formatNumber(state.tick)}</strong></div></section>
    <p className="builder-note">Live universe snapshots below are independent of saved experiment control/treatment branches. Running an experiment does not replace either live universe.</p>
    <section className="lab-compare" aria-label="Live universe snapshots"><UniverseCompareCard label={`${state.universeId} / live snapshot`} tone="violet" state={state} /><div className="compare-divider"><span>↔</span><small>live</small></div><UniverseCompareCard label={`${otherState.universeId} / live snapshot`} tone="teal" state={otherState} /></section>
      {comparison && <ComparisonResult comparison={comparison} />}
      {(causalComparison || impactSummary || causalGraph) && <CausalExplorer comparison={causalComparison} impactSummary={impactSummary} graph={causalGraph} />}
      {experimentHistory && <HistoryComparisonChart history={experimentHistory} />}
     <div className="experiment-grid">{state.experiments.length ? state.experiments.map((experiment) => <ExperimentCard key={experiment.id} experiment={experiment} running={runningId === experiment.id} batchRunning={batchRunningId === experiment.id} onRun={() => onRun(experiment)} onBatch={() => onRunBatch(experiment)} />) : <EmptyState title="No experiments registered" body="The API returned no scenarios for this universe." />}</div>
      <BatchHistory batches={batches} />
      <section className="panel experiment-history"><PanelHeader title="Durable run history" subtitle="Every completed run keeps its source and branch snapshot provenance." action={<StatusPill tone="neutral">{history.length} saved</StatusPill>} />{history.length ? <div className="run-history-list">{history.slice(0, 8).map((run) => <article className="run-history-row" key={run.runId}><div className="run-history-row__main"><StatusPill tone={run.status === 'completed' ? 'green' : run.status === 'failed' ? 'red' : 'amber'}>{run.status}</StatusPill><div><strong>{run.experimentId.replaceAll('-', ' ')}</strong><span>run {run.runId.slice(0, 10)} · {formatDateTime(run.updatedAt)}</span></div></div><div className="run-history-row__provenance"><span>Source {run.sourceSnapshot ? `#${run.sourceSnapshot.snapshotId} · tick ${run.sourceSnapshot.tick}` : 'unavailable'}</span><span>Branches {run.controlSnapshot ? `#${run.controlSnapshot.snapshotId}` : '—'} / {run.treatmentSnapshot ? `#${run.treatmentSnapshot.snapshotId}` : '—'}</span></div><a className="text-button" href={api.experimentRunExportUrl(run.runId)} target="_blank" rel="noreferrer">Export <Icon name="arrow" size={13} /></a></article>)}</div> : <EmptyState title="No durable runs yet" body="Run a catalog experiment to create a history entry with source, control, and treatment snapshot tags." />}</section>
     <section className="panel experiment-history"><PanelHeader title="Run protocol" subtitle="The backend owns the run record and branch snapshots." /><div className="protocol-grid"><ProtocolStep index="01" title="Name the variable" text="Pick one factor to change." /><ProtocolStep index="02" title="Run the sequence" text="The backend records a tagged source and two branches." /><ProtocolStep index="03" title="Read the wake" text="Retrieve, export, and compare the durable result." /></div></section>
  </>
}

function UniverseCompareCard({ label, tone, state }: { label: string; tone: 'violet' | 'teal'; state: UniverseState }) {
  return <article className={`universe-compare-card ${tone}`}><div className="universe-compare-card__head"><span className="eyebrow"><i className={`compare-dot ${tone}`} />{label}</span><strong>tick {formatNumber(state.tick)}</strong></div><div className="compare-values"><div><span>Population</span><strong>{formatNumber(state.metrics.population)}</strong></div><div><span>Trust</span><strong>{formatPercent(state.metrics.trustIndex)}</strong></div><div><span>Entropy</span><strong>{formatPercent(state.metrics.informationEntropy)}</strong></div><div><span>Edges</span><strong>{formatNumber(state.edges.length)}</strong></div></div><div className="compare-bar"><span style={{ width: `${state.metrics.trustIndex * 100}%` }} /></div></article>
}

export function ComparisonResult({ comparison }: { comparison: ExperimentComparison }) {
  const delta = (name: string) => comparison.metric_deltas[name]
  const signed = (value: number | undefined) => formatMetric(value, true)
  return <section className="panel comparison-result"><div><span className="eyebrow">Measured detached branch result</span><h3>{comparison.experiment_id.replaceAll('-', ' ')}</h3><p>Source universe: <b>{comparison.universe_id}</b> · run {comparison.run_id ?? 'ID unavailable'} · same seed <b>{comparison.seed}</b> · {comparison.ticks} ticks. Δ = treatment − control. These saved branches are not the live Prime/Echo snapshots.</p></div>
    <div className="builder-table-scroll" tabIndex={0} role="region" aria-label="Detached branch metrics"><table className="builder-table"><caption>Recorded experiment endpoints</caption><thead><tr><th scope="col">Metric</th><th scope="col">Control</th><th scope="col">Treatment</th><th scope="col">Δ</th></tr></thead><tbody>{(['average_belief_confidence', 'average_relationship_trust', 'fact_checks', 'polarization'] as const).map((metric) => <tr key={metric}><th scope="row">{metric.replaceAll('_', ' ')}</th><td>{formatMetric(comparison.control_metrics[metric])}</td><td>{formatMetric(comparison.treatment_metrics[metric])}</td><td>{signed(delta(metric))}</td></tr>)}</tbody></table></div>
    <div className="comparison-footnote"><Icon name="activity" size={14} />{comparison.earliest_divergence ? `Earliest divergence at recorded event ${String(comparison.earliest_divergence.event_index ?? '—')}.` : 'No divergence reported in this run.'}</div></section>
}

function CausalExplorer({ comparison, impactSummary, graph }: { comparison?: BranchComparison; impactSummary?: ImpactSummary; graph?: CausalGraph }) {
  const divergence = comparison?.earliest_divergence
  const position = divergence && typeof divergence.semantic_position === 'object' && divergence.semantic_position !== null
    ? divergence.semantic_position as Record<string, unknown>
    : undefined
  const control = divergence && typeof divergence.control === 'object' && divergence.control !== null
    ? divergence.control as Record<string, unknown>
    : undefined
  const treatment = divergence && typeof divergence.treatment === 'object' && divergence.treatment !== null
    ? divergence.treatment as Record<string, unknown>
    : undefined
  const edgePreview = graph?.edges.slice(0, 7) || []
  return <section className="panel causal-explorer">
     <PanelHeader title="Semantic divergence & causal ancestry" subtitle="Recorded dependencies from the selected durable run" action={comparison ? <StatusPill tone={comparison.equivalence === 'diverged' || comparison.equivalence === 'unknown' ? 'amber' : 'green'}>{comparison.equivalence}</StatusPill> : undefined} />
    <div className="causal-explorer__body">
      <div className="causal-explorer__summary">
        <div className="causal-status"><span className="eyebrow">Earliest semantic divergence</span>{comparison?.equivalence === 'identical' ? <strong>No divergence</strong> : position ? <><strong>Tick {String(position.tick ?? '—')}</strong><span>event {String(divergence?.event_index ?? '—')} · {String(divergence?.reason ?? 'semantic fields differ')}</span></> : <strong>Awaiting comparison</strong>}</div>
        <div className="causal-actors"><span className="eyebrow">Branch evidence</span>{control && treatment ? <><div><span>Control</span><strong>{String(control.action ?? 'event')} · {String(control.actor_id ?? 'unknown citizen')}</strong></div><div><span>Treatment</span><strong>{String(treatment.action ?? 'event')} · {String(treatment.actor_id ?? 'unknown citizen')}</strong></div></> : <p>Run a durable counterfactual to populate the semantic event comparison.</p>}</div>
      </div>
      {impactSummary && <div className="causal-impact-grid"><div><span>Citizens changed</span><strong>{impactSummary.changed_citizen_count}</strong></div><div><span>Claims changed</span><strong>{impactSummary.changed_claim_count}</strong></div><div><span>Communities changed</span><strong>{impactSummary.changed_community_count}</strong></div></div>}
      <div className="causal-explorer__graph"><div><span className="eyebrow">Recorded dependency graph</span><p>{graph ? `${graph.node_count} nodes · ${graph.edge_count} modeled links` : 'Graph is not available for this run yet.'}</p></div>{graph && edgePreview.length > 0 && <div className="causal-edge-list">{edgePreview.map((edge) => <div key={edge.link_id}><span>T{edge.tick}</span><strong>{edge.relation.replaceAll('_', ' ')}</strong><small>{edge.parent_event_id} → {edge.child_event_id}</small></div>)}</div>}</div>
      <div className="causal-explorer__note"><Icon name="link" size={14} /><span>{comparison?.evidence.modeled_dependency_label || impactSummary?.evidence.modeled_dependency_label || graph?.evidence.modeled_dependency_label || 'modeled dependency'}: links are declared by simulator rules. {comparison?.evidence.statistical_effect_label || impactSummary?.evidence.statistical_effect_label || graph?.evidence.statistical_effect_label || 'not statistically established effect'}.</span></div>
    </div>
  </section>
}

type HistoryMetric = 'total_contents' | 'engagement_rate' | 'exposure_rate' | 'reshare_rate' | 'correction_reach' | 'polarization'

const historyMetricOptions: Array<{ key: HistoryMetric; label: string; format: (value: number) => string }> = [
  { key: 'total_contents', label: 'Total content', format: (value) => formatNumber(value) },
  { key: 'engagement_rate', label: 'Engagement rate', format: formatPercent },
  { key: 'exposure_rate', label: 'Exposure rate', format: formatPercent },
  { key: 'reshare_rate', label: 'Reshare rate', format: formatPercent },
  { key: 'correction_reach', label: 'Correction reach', format: formatPercent },
  { key: 'polarization', label: 'Polarization', format: formatPercent },
]

function HistoryComparisonChart({ history }: { history: ExperimentHistory }) {
  const [metric, setMetric] = useState<HistoryMetric>('exposure_rate')
  const [hoveredTick, setHoveredTick] = useState<number>()
  const control = history.branches.control?.samples || []
  const treatment = history.branches.treatment?.samples || []
  const selected = historyMetricOptions.find((item) => item.key === metric) || historyMetricOptions[0]
  const validControl = control.filter((sample) => Number.isFinite(Number(sample.metrics[metric])))
  const validTreatment = treatment.filter((sample) => Number.isFinite(Number(sample.metrics[metric])))
  const ticks = Array.from(new Set([...control, ...treatment].map((sample) => sample.tick))).sort((left, right) => left - right)
  const values = [...control, ...treatment].map((sample) => Number(sample.metrics[metric])).filter((value) => Number.isFinite(value))
  const minimum = Math.min(...values, 0)
  const maximum = Math.max(...values, 1)
  const width = 760
  const height = 260
  const padding = { top: 22, right: 18, bottom: 32, left: 43 }
  const x = (tick: number) => padding.left + ((tick - (ticks[0] || 0)) / Math.max(1, (ticks[ticks.length - 1] || 1) - (ticks[0] || 0))) * (width - padding.left - padding.right)
  const y = (value: number) => height - padding.bottom - ((value - minimum) / Math.max(0.0001, maximum - minimum)) * (height - padding.top - padding.bottom)
  const hoveredControl = control.find((sample) => sample.tick === hoveredTick)
  const hoveredTreatment = treatment.find((sample) => sample.tick === hoveredTick)

  if (!control.length && !treatment.length) return <EmptyState title="No metric history returned" body="This run has no persisted per-tick samples to chart." />

  return <section className="panel history-chart-panel">
    <PanelHeader title="Historical branch comparison" subtitle="Persisted per-tick measurements from the selected run" action={<label className="chart-select"><span>Metric</span><select value={metric} onChange={(event) => setMetric(event.target.value as HistoryMetric)}>{historyMetricOptions.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}</select></label>} />
    <div className="history-chart-wrap">
      <svg className="history-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${selected.label} comparison over ${ticks.length} recorded ticks`}>
        <g className="history-chart__grid"><line x1={padding.left} x2={width - padding.right} y1={y(minimum)} y2={y(minimum)} /><line x1={padding.left} x2={width - padding.right} y1={y((minimum + maximum) / 2)} y2={y((minimum + maximum) / 2)} /><line x1={padding.left} x2={width - padding.right} y1={y(maximum)} y2={y(maximum)} /></g>
        {metricTimeline(control, metric).segments.map((segment, index) => <polyline key={`control-${index}`} className="history-chart__line control" points={segment.map((point) => `${x(point.tick)},${y(point.value)}`).join(' ')} />)}
        {metricTimeline(treatment, metric).segments.map((segment, index) => <polyline key={`treatment-${index}`} className="history-chart__line treatment" points={segment.map((point) => `${x(point.tick)},${y(point.value)}`).join(' ')} />)}
        <g className="history-chart__points">{validControl.map((sample) => <circle key={`control-${sample.tick}`} className="control" cx={x(sample.tick)} cy={y(Number(sample.metrics[metric]))} r="4" onMouseEnter={() => setHoveredTick(sample.tick)} onFocus={() => setHoveredTick(sample.tick)} tabIndex={0} aria-label={`Control tick ${sample.tick}: ${selected.format(Number(sample.metrics[metric]))}`} />)}{validTreatment.map((sample) => <circle key={`treatment-${sample.tick}`} className="treatment" cx={x(sample.tick)} cy={y(Number(sample.metrics[metric]))} r="4" onMouseEnter={() => setHoveredTick(sample.tick)} onFocus={() => setHoveredTick(sample.tick)} tabIndex={0} aria-label={`Treatment tick ${sample.tick}: ${selected.format(Number(sample.metrics[metric]))}`} />)}</g>
        {ticks.map((tick) => <text key={tick} x={x(tick)} y={height - 10} textAnchor="middle">T{tick}</text>)}
      </svg>
      <div className="history-chart__legend"><span><i className="control" />Control</span><span><i className="treatment" />Treatment</span>{hoveredTick !== undefined && <strong>Tick {hoveredTick} · control {hoveredControl ? selected.format(Number(hoveredControl.metrics[metric])) : '—'} · treatment {hoveredTreatment ? selected.format(Number(hoveredTreatment.metrics[metric])) : '—'}</strong>}</div>
      <p className="history-chart__note">{selected.label} · {ticks.length} bounded samples · values are recalculated from the recorded branch event histories.</p>
    </div>
  </section>
}

function BatchHistory({ batches }: { batches: ExperimentBatch[] }) {
  return <section className="panel batch-history"><PanelHeader title="Multi-seed robustness" subtitle="Paired seeds run serially within a bounded computational budget" action={<StatusPill tone="green">{batches.length} saved</StatusPill>} />{batches.length ? <div className="batch-list">{batches.slice(0, 6).map((batch) => {
    const statusTone = batch.status === 'completed' ? 'green' : batch.status === 'failed' ? 'red' : batch.status === 'completed_with_failures' ? 'amber' : 'violet'
    const objective = batch.aggregate?.objective
    const summary = objective ? batch.aggregate?.metrics[objective.metric] : undefined
    return <article className="batch-row" key={batch.batchId}><div className="batch-row__main"><StatusPill tone={statusTone}>{batch.status.replaceAll('_', ' ')}</StatusPill><div><strong>{batch.experimentId.replaceAll('-', ' ')}</strong><span>{batch.completedRuns}/{batch.totalRuns} pairs · base seed {String(batch.config?.base_seed ?? 'unavailable')}</span></div></div><div className="batch-row__progress"><div><span>Progress</span><strong>{Math.round(batch.progress?.percentComplete ?? 0)}%</strong></div><ProgressBar value={(batch.progress?.percentComplete ?? 0) / 100} tone="teal" /><small>{batch.progress?.executionMode === 'serial_synchronous' ? 'serial / synchronous' : 'bounded execution'}</small></div><div className="batch-row__result">{objective ? <><span>{objective.metric.replaceAll('_', ' ')} success</span><strong>{formatPercent(batch.aggregate?.objectiveSuccessRate ?? 0)}</strong><small>{objective.successes}/{objective.evaluated} pairs · Δ mean {summary?.pairedDifference.mean?.toFixed(3) ?? '—'}</small></> : <span>Aggregate pending</span>}</div></article>
  })}</div> : <EmptyState title="No multi-seed batches yet" body="Use Run 5-seed batch on a catalog scenario to measure the paired result across deterministic seeds." />}</section>
}

 function ExperimentCard({ experiment, running, batchRunning, onRun, onBatch }: { experiment: Experiment; running: boolean; batchRunning: boolean; onRun: () => void; onBatch: () => void }) {
  return <article className="experiment-card"><div className="experiment-card__top"><StatusPill tone={experiment.status === 'complete' ? 'green' : experiment.status === 'paused' ? 'amber' : 'violet'}>{running || batchRunning ? 'running' : experiment.status}</StatusPill><span className="experiment-code">{experiment.id}</span></div><h3>{experiment.name}</h3><p>{experiment.description}</p><div className="tag-list">{experiment.variables.map((variable) => <span className="tag" key={variable}>{variable}</span>)}</div><div className="experiment-card__bottom"><div><span>Catalog readout</span><strong>{experiment.projectedImpact}</strong>{experiment.lastRun && <small>Last run {experiment.lastRun}</small>}</div><div className="experiment-card__actions"><button className="button small primary" onClick={onRun} disabled={running || batchRunning}><Icon name={running ? 'pause' : 'play'} size={13} />{running ? 'Running' : 'Run experiment'}</button><button className="text-button" onClick={onBatch} disabled={running || batchRunning}><Icon name="activity" size={13} />{batchRunning ? 'Batch running' : 'Run 5-seed batch'}</button></div></div></article>
}

function ProtocolStep({ index, title, text }: { index: string; title: string; text: string }) {
  return <div className="protocol-step"><span>{index}</span><div><strong>{title}</strong><p>{text}</p></div></div>
}

export function InterventionConsoleView({ state, submitting, onSubmit }: { state: UniverseState; submitting: boolean; onSubmit: (input: { action: string; target: string; intensity: number; rationale: string }) => void }) {
  const [action, setAction] = useState<keyof typeof consoleActions>('context_card')
  const [target, setTarget] = useState(state.nodes[0]?.id || '')
  const [intensity, setIntensity] = useState(0.35)
  const [rationale, setRationale] = useState('')
  const effectiveTarget = state.nodes.some((node) => node.id === target) ? target : state.nodes[0]?.id ?? ''
  const canSubmit = consoleInputValid({ action, target: effectiveTarget, intensity, rationale }, state.nodes.map((node) => node.id)) && !submitting
  return <>
    <SectionHeader eyebrow="Operator tools / deliberate change" title="Intervention Console" description="Inspect the actual simulator mechanism before applying a change." action={<StatusPill tone="amber">{formatPercent(state.metrics.interventionBudget)} intervention indicator</StatusPill>} />
    <div className="console-grid"><section className="panel intervention-form">
      <PanelHeader title="Compose an intervention" subtitle="Recorded against the selected live universe." />
      <label className="form-field"><span>Intervention type</span><select value={action} onChange={(event) => setAction(event.target.value as keyof typeof consoleActions)}>{Object.entries(consoleActions).map(([key, item]) => <option key={key} value={key}>{item.label}</option>)}</select></label>
      <p className="builder-note">{consoleActions[action].description}</p>
      <label className="form-field"><span>{action === 'highlight_care' ? 'Target citizen' : 'Compatibility target (unused by this action)'}</span><select value={effectiveTarget} disabled={action !== 'highlight_care'} onChange={(event) => setTarget(event.target.value)}>{state.nodes.map((node) => <option key={node.id} value={node.id}>{node.label} · {node.kind}</option>)}</select></label>
      <label className="form-field"><span>Intensity <output>{formatPercent(intensity)}</output></span><input type="range" min="0" max="1" step="0.05" value={intensity} onChange={(event) => setIntensity(Number(event.target.value))} /></label>
      <p className="builder-note">For publishing and fact-check actions, effective strength is at least 5%, including at zero intensity.</p>
      <label className="form-field"><span>Rationale <small>5–280 characters required</small></span><textarea value={rationale} maxLength={280} onChange={(event) => setRationale(event.target.value)} placeholder="What mechanism are you testing?" rows={4} /></label>
      <div className="form-submit"><span>{rationale.trim().length < 5 ? 'Add a short rationale to continue.' : 'Ready for observation.'}</span><button className="button primary" onClick={() => onSubmit({ action, target: effectiveTarget, intensity, rationale: rationale.trim() })} disabled={!canSubmit}>{submitting ? <><span className="spinner" />Sending…</> : <><Icon name="send" size={14} />Apply intervention</>}</button></div>
    </section><aside className="panel intervention-log"><PanelHeader title="Intervention log" subtitle="Recorded actions; application does not prove an outcome" />{state.interventions.length ? <div className="intervention-list">{state.interventions.map((intervention) => <InterventionRow key={intervention.id} intervention={intervention} />)}</div> : <EmptyState title="No interventions yet" body="Recorded changes will appear here after application." />}</aside></div>
  </>
}

function InterventionRow({ intervention }: { intervention: InterventionRecord }) {
  return <div className="intervention-row"><div className={`intervention-marker ${intervention.state}`}><Icon name={intervention.state === 'observed' ? 'eye' : intervention.state === 'applied' ? 'play' : 'pause'} size={13} /></div><div><strong>{intervention.label}</strong><span>{intervention.target} · {intervention.timestamp}</span>{intervention.effect && <p>{intervention.effect}</p>}</div><StatusPill tone={intervention.state === 'observed' ? 'green' : intervention.state === 'applied' ? 'violet' : 'amber'}>{intervention.state}</StatusPill></div>
}

function AgentMindView({ state }: { state: UniverseState }) {
  const [selectedId, setSelectedId] = useState(state.minds[0]?.id)
  useEffect(() => {
    if (!state.minds.some((mind) => mind.id === selectedId)) setSelectedId(state.minds[0]?.id)
  }, [state.universeId, state.minds, selectedId])
  const selected = state.minds.find((mind) => mind.id === selectedId)
  return <>
    <SectionHeader eyebrow="Cognition / internal state" title="Agent Mind Explorer" description="Inspect the attention, openness, conviction, and memory signals returned by the simulation." action={<StatusPill tone="violet">{state.minds.length} minds observable</StatusPill>} />
    <div className="mind-layout"><section className="panel mind-roster"><PanelHeader title="Agent roster" subtitle="Select a mind to open its current state" />{state.minds.length ? <div className="mind-list">{state.minds.map((mind) => <button key={mind.id} className={`mind-row ${selectedId === mind.id ? 'selected' : ''}`} onClick={() => setSelectedId(mind.id)}><div className="mind-avatar"><Icon name="brain" size={16} /></div><div><strong>{mind.name}</strong><span>{mind.role}</span></div><span className="mind-mood">{mind.mood}</span><Icon name="chevron" size={15} /></button>)}</div> : <EmptyState title="No agent minds" body="The current state has no cognitive profiles to inspect." />}</section><section className="panel mind-detail">{selected ? <MindProfile mind={selected} /> : <EmptyState title="Select a mind" body="Choose an agent from the roster." />}</section></div>
  </>
}

function MindProfile({ mind }: { mind: UniverseState['minds'][number] }) {
  return <><div className="mind-detail__top"><div className="mind-avatar large"><Icon name="brain" size={22} /></div><div><span className="eyebrow">Active cognitive frame</span><h3>{mind.name}</h3><p>{mind.role} · {mind.mood}</p></div></div><div className="mind-metrics"><MindMetric label="Attention" value={mind.attention} tone="violet" /><MindMetric label="Openness" value={mind.openness} tone="teal" /><MindMetric label="Conviction" value={mind.conviction} tone="amber" /></div><div className="mind-columns"><div className="profile-block"><span className="eyebrow">Recent memory</span>{mind.memory.length ? <ul className="signal-list">{mind.memory.map((memory) => <li key={memory}><span /><p>{memory}</p></li>)}</ul> : <p className="muted">No recent memory signals.</p>}</div><div className="profile-block"><span className="eyebrow">Active drivers</span><div className="tag-list">{mind.activeDrivers.map((driver) => <span className="tag accent" key={driver}>{driver}</span>)}</div></div></div>{mind.latestTrace && <div className="mind-trace"><span className="eyebrow">Latest executed decision · tick {mind.latestTrace.tick}</span><div><strong>{mind.latestTrace.phase}</strong><span>{mind.latestTrace.action || 'no action'}</span></div><p>Structured evidence: {Object.entries(mind.latestTrace.details).map(([key, value]) => `${key}=${String(value)}`).join(' · ')}</p></div>}</>
}

function MindMetric({ label, value, tone }: { label: string; value: number; tone: string }) {
  return <div className="mind-metric"><div><span>{label}</span><strong>{formatPercent(value)}</strong></div><ProgressBar value={value} tone={tone} /></div>
}

function LoadingScreen() {
  return <div className="loading-screen"><div className="loading-mark"><Icon name="spark" size={26} /></div><span className="eyebrow">Initializing simulation</span><h1>Finding the signal.</h1><div className="loading-line"><span /></div><p>Connecting to the civilization state…</p></div>
}

export default function App() {
  const [activeView, setActiveView] = useState<ViewId>('overview')
  const [activeUniverse, setActiveUniverse] = useState<UniverseId>('prime')
  const [stateByUniverse, setStateByUniverse] = useState<Partial<Record<UniverseId, UniverseState>>>({})
  const [offline, setOffline] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const [demoRunning, setDemoRunning] = useState(false)
  const [selectedCitizen, setSelectedCitizen] = useState<SelectedCitizen>()
  const [comparison, setComparison] = useState<ExperimentComparison>()
  const [causalComparison, setCausalComparison] = useState<BranchComparison>()
  const [impactSummary, setImpactSummary] = useState<ImpactSummary>()
  const [causalGraph, setCausalGraph] = useState<CausalGraph>()
  const [experimentHistory, setExperimentHistory] = useState<ExperimentHistory>()
  const [experimentRuns, setExperimentRuns] = useState<ExperimentRun[]>([])
  const [experimentBatches, setExperimentBatches] = useState<ExperimentBatch[]>([])
  const [toast, setToast] = useState<Toast>(null)
  const [error, setError] = useState<string | null>(null)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const operation = useRef(false)
  const labGeneration = useRef(0)
  const activeUniverseRef = useRef(activeUniverse)
  activeUniverseRef.current = activeUniverse
  const offlineRef = useRef(offline)
  offlineRef.current = offline
  const toastTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const state = stateByUniverse[activeUniverse]
  const otherUniverse: UniverseId = activeUniverse === 'prime' ? 'echo' : 'prime'
  const otherState = stateByUniverse[otherUniverse]

  const setUniverseState = useCallback((universeId: UniverseId, next: UniverseState) => {
    setStateByUniverse((current) => ({ ...current, [universeId]: next }))
  }, [])

  const showToast = useCallback((next: Toast) => {
    clearTimeout(toastTimer.current)
    setToast(next)
    toastTimer.current = setTimeout(() => setToast(null), 4800)
  }, [])

  useEffect(() => () => { clearTimeout(toastTimer.current); labGeneration.current++ }, [])

  const clearLab = useCallback(() => {
    labGeneration.current++
    setExperimentRuns([]); setExperimentBatches([]); setExperimentHistory(undefined)
    setComparison(undefined); setCausalComparison(undefined); setImpactSummary(undefined); setCausalGraph(undefined)
  }, [])

  const enterOfflinePreview = useCallback((message = 'Simulation API is unreachable. Showing deterministic offline preview data.') => {
    offlineRef.current = true
    labGeneration.current++
    setOffline(true)
    setSelectedCitizen(undefined)
    setExperimentRuns([])
    setExperimentBatches([])
    setExperimentHistory(undefined)
    setComparison(undefined)
    setCausalComparison(undefined)
    setImpactSummary(undefined)
    setCausalGraph(undefined)
    setStateByUniverse({ prime: buildFallbackState('prime'), echo: buildFallbackState('echo') })
    showToast({ tone: 'warning', message })
  }, [showToast])

  const loadExperimentHistory = useCallback(async (universeId: UniverseId, preferredRunId?: string) => {
    if (offlineRef.current) return
    const generation = ++labGeneration.current
    const current = () => generation === labGeneration.current && activeUniverseRef.current === universeId && !offlineRef.current
    try {
      const [runs, batches] = await Promise.all([api.getExperimentRuns(universeId), api.getExperimentBatches(universeId)])
      if (!current()) return
      setExperimentRuns(runs)
      setExperimentBatches(batches)
      const latest = runs.find((run) => run.runId === preferredRunId) ?? runs.find((run) => run.status === 'completed' && run.result)
      if (latest?.result) {
        // Publish one run's artifacts together, never mix an older graph/history
        // with a newly selected result or another universe's late response.
        const artifacts = await Promise.allSettled([api.getExperimentHistory(latest.runId), api.getBranchComparison(latest.runId), api.getImpactSummary(latest.runId), api.getCausalGraph(latest.runId)])
        if (!current()) return
        const [history, branch, impact, graph] = artifacts
        setComparison({ ...latest.result, run_id: latest.runId, status: latest.status })
        setCausalComparison(branch.status === 'fulfilled' ? branch.value : latest.result.branch_comparison)
        setImpactSummary(impact.status === 'fulfilled' ? impact.value : latest.result.impact_summary)
        setCausalGraph(graph.status === 'fulfilled' ? graph.value : undefined)
        setExperimentHistory(history.status === 'fulfilled' ? history.value : undefined)
        const failures = artifacts.filter((item) => item.status === 'rejected' && !(item.reason instanceof ApiResponseError && item.reason.status === 404))
        if (failures.length) setError('The saved result is available, but some history or causal artifacts could not be loaded. Reopen Butterfly Lab to retry.')
      } else {
        setComparison(undefined)
        setCausalComparison(undefined)
        setImpactSummary(undefined)
        setCausalGraph(undefined)
        setExperimentHistory(undefined)
      }
    } catch (requestError) {
      if (!current()) return
      if (isUnavailable(requestError)) enterOfflinePreview()
      else setError(requestError instanceof Error ? requestError.message : 'Could not load experiment history.')
    }
  }, [enterOfflinePreview])

  const loadUniverse = useCallback(async (universeId: UniverseId, showLoader = false) => {
    if (offline) {
      setUniverseState(universeId, buildFallbackState(universeId))
      return
    }
    if (showLoader) setBusyAction(`load-${universeId}`)
    try {
      const next = await api.getState(universeId)
      setUniverseState(universeId, next)
      setError(null)
    } catch (requestError) {
      if (isUnavailable(requestError)) enterOfflinePreview()
      else setError(requestError instanceof Error ? requestError.message : 'Could not load universe state.')
    } finally {
      if (showLoader) setBusyAction(null)
    }
  }, [enterOfflinePreview, offline, setUniverseState])

  useEffect(() => {
    let cancelled = false
    const boot = async () => {
      try {
        const [health, primeState, echoState] = await Promise.all([api.getHealth(), api.getState('prime'), api.getState('echo')])
        if (cancelled) return
        setUniverseState('prime', primeState)
        setUniverseState('echo', echoState)
        setOffline(false)
        setError(null)
        if (health.status && health.status.toLowerCase() !== 'ok' && health.status.toLowerCase() !== 'healthy') showToast({ tone: 'warning', message: `API health reports ${health.status}.` })
      } catch (requestError) {
        if (cancelled) return
        if (isUnavailable(requestError)) enterOfflinePreview()
        else setError(requestError instanceof Error ? requestError.message : 'Could not initialize the simulation.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void boot()
    return () => { cancelled = true }
  }, [enterOfflinePreview, setUniverseState, showToast])

  // /state already contains the roster. A second roster fetch used to replace
  // the entire state with its stale pre-request snapshot after a tick/reset.
  const selectedCitizenId = selectedCitizen?.id
  useEffect(() => {
    if (!selectedCitizenId || !state) return
    const citizen = state.citizens.find((item) => item.id === selectedCitizenId)
    setSelectedCitizen(citizen)
    if (!citizen || offline) return
    const controller = new AbortController()
    void api.getCitizen(activeUniverse, citizen.id, controller.signal).then((detail) => {
      if (!controller.signal.aborted) setSelectedCitizen(detail)
    }).catch((requestError: unknown) => {
      if (controller.signal.aborted) return
      setError(requestError instanceof Error ? requestError.message : 'Could not load citizen detail.')
    })
    return () => controller.abort()
  }, [selectedCitizenId, state, activeUniverse, offline])

  useEffect(() => {
    if (activeView === 'lab' && !offline) void loadExperimentHistory(activeUniverse)
    return () => { labGeneration.current++ }
  }, [activeView, activeUniverse, loadExperimentHistory, offline])

  const selectUniverse = (universeId: UniverseId) => {
    if (operation.current || universeId === activeUniverse) return
    activeUniverseRef.current = universeId
    clearLab()
    setActiveUniverse(universeId)
    setSelectedCitizen(undefined)
    setMobileNavOpen(false)
    if (!stateByUniverse[universeId]) void loadUniverse(universeId, true)
  }

  const beginOperation = (label: string, allowOffline = false) => {
    if (operation.current || (offlineRef.current && !allowOffline)) return false
    operation.current = true
    labGeneration.current++
    setBusyAction(label); setError(null)
    return true
  }
  const finishOperation = () => { operation.current = false; setBusyAction(null) }
  const builderBusy = useCallback((busy: boolean) => {
    operation.current = busy
    setBusyAction(busy ? 'builder' : null)
  }, [])

  const refresh = async () => {
    if (!beginOperation('refresh', true)) return
    try {
      const [health, primeState, echoState] = await Promise.all([api.getHealth(), api.getState('prime'), api.getState('echo')])
      setUniverseState('prime', primeState)
      setUniverseState('echo', echoState)
      setError(null)
      showToast({ tone: 'success', message: `Synced both universes at tick ${activeUniverse === 'prime' ? primeState.tick : echoState.tick}.` })
      if (health.status) { offlineRef.current = false; setOffline(false) }
      setSelectedCitizen(undefined)
      if (activeView === 'lab') await loadExperimentHistory(activeUniverse)
    } catch (requestError) {
      if (isUnavailable(requestError)) enterOfflinePreview()
      else setError(requestError instanceof Error ? requestError.message : 'Sync failed.')
    } finally {
      finishOperation()
    }
  }

  const tick = async (universeId = activeUniverse, steps = 1) => {
    if (!beginOperation(`tick-${universeId}`, true)) return
    try {
      if (offline) {
        const current = stateByUniverse[universeId] || buildFallbackState(universeId)
        setUniverseState(universeId, { ...current, tick: current.tick + steps, updatedAt: new Date().toISOString(), phase: steps > 1 ? 'Comparative propagation' : current.phase })
        showToast({ tone: 'warning', message: 'Preview tick advanced locally; no live outcome was recorded.' })
      } else {
        await api.tick(universeId, steps)
        const next = await api.getState(universeId)
        setUniverseState(universeId, next)
        showToast({ tone: 'success', message: `Advanced ${universeId} to tick ${next.tick}.` })
      }
    } catch (requestError) {
      if (isUnavailable(requestError)) enterOfflinePreview()
      else setError(requestError instanceof Error ? requestError.message : 'Tick failed.')
    } finally {
      finishOperation()
    }
  }

  const reset = async (universeId = activeUniverse, silent = false) => {
    if (!beginOperation(`reset-${universeId}`, true)) return
    try {
      if (offline) {
        setUniverseState(universeId, buildFallbackState(universeId))
      } else {
        await api.reset(universeId)
        setUniverseState(universeId, await api.getState(universeId))
      }
      if (!silent) showToast({ tone: offline ? 'warning' : 'success', message: `${universeId} universe reset${offline ? ' in preview' : ''}.` })
    } catch (requestError) {
      if (isUnavailable(requestError)) enterOfflinePreview()
      else setError(requestError instanceof Error ? requestError.message : 'Reset failed.')
    } finally { finishOperation() }
  }

  const runDemo = async () => {
    if (!beginOperation('demo', true)) return
    setDemoRunning(true)
    setActiveView('overview')
    setError(null)
    try {
      if (offline) {
        setUniverseState('prime', { ...buildFallbackState('prime'), tick: buildFallbackState('prime').tick + DEMO_TICKS, phase: 'Comparative propagation', updatedAt: new Date().toISOString() })
        setUniverseState('echo', { ...buildFallbackState('echo'), tick: buildFallbackState('echo').tick + DEMO_TICKS, phase: 'Comparative propagation', updatedAt: new Date().toISOString() })
        showToast({ tone: 'warning', message: `Preview demo complete after ${DEMO_TICKS} paired ticks.` })
      } else {
        // Fail the sequence immediately if either reset fails.
        await api.reset('prime')
        await api.reset('echo')
        await Promise.all([api.tick('prime', DEMO_TICKS), api.tick('echo', DEMO_TICKS)])
        // The actual counterfactual is forked from the synchronized prime
        // snapshot. Control receives only the claim; treatment receives
        // delayed credible counterevidence. The branch result is not UI copy.
         await api.runExperiment('prime', 'rumor-counterevidence')
        const [prime, echo] = await Promise.all([api.getState('prime'), api.getState('echo')])
        setUniverseState('prime', prime)
        setUniverseState('echo', echo)
        activeUniverseRef.current = 'prime'
        setActiveUniverse('prime'); setSelectedCitizen(undefined); clearLab(); setActiveView('lab')
        showToast({ tone: 'success', message: 'Demo complete. Butterfly Lab shows the saved Prime control/treatment branches.' })
      }
    } catch (requestError) {
      if (isUnavailable(requestError)) enterOfflinePreview('The API disconnected during the demo. Preview universes are available for recovery.')
      else setError(requestError instanceof Error ? requestError.message : 'Demo flow failed.')
    } finally {
      setDemoRunning(false)
      finishOperation()
    }
  }

  const runExperiment = async (experiment: Experiment) => {
    if (!beginOperation(`experiment-${experiment.id}`, true)) return
    try {
      if (offline) {
        showToast({ tone: 'warning', message: 'Live experiment execution is unavailable in offline preview; no result was recorded.' })
      } else {
        const run = await api.createExperimentRun(activeUniverse, experiment.id)
        if (run.status !== 'completed') throw new Error(run.error || `Experiment status: ${run.status}`)
        await loadExperimentHistory(activeUniverse, run.runId)
        setUniverseState(activeUniverse, await api.getState(activeUniverse))
        showToast({ tone: 'success', message: `${experiment.name} completed.` })
      }
    } catch (requestError) {
      if (isUnavailable(requestError)) enterOfflinePreview()
      else setError(requestError instanceof Error ? requestError.message : 'Experiment failed.')
    } finally {
      finishOperation()
    }
  }

  const runBatch = async (experiment: Experiment) => {
    if (!beginOperation(`batch-${experiment.id}`, true)) return
    try {
      if (offline) {
        showToast({ tone: 'warning', message: 'Batch preview is unavailable offline; no live aggregate was recorded.' })
      } else {
        const batch = await api.createExperimentBatch({
          universeId: activeUniverse,
          experimentId: experiment.id,
          population: Math.max(1, Math.min(stateByUniverse[activeUniverse]?.metrics.population ?? 100, 100)),
          ticks: 6,
          repetitions: 5,
          computationalBudget: 250000,
          confidenceLevel: 0.95,
          objective: { metric: 'correction_reach', direction: 'increase', threshold: 0.05 },
        })
        setExperimentBatches((current) => [batch, ...current.filter((item) => item.batchId !== batch.batchId)])
        showToast({ tone: batch.status === 'completed' ? 'success' : 'warning', message: `${experiment.name}: ${batch.status.replaceAll('_', ' ')}; ${batch.completedRuns}/${batch.totalRuns} pairs completed, ${batch.failedRuns} failed.` })
      }
    } catch (requestError) {
      if (isUnavailable(requestError)) enterOfflinePreview()
      else setError(requestError instanceof Error ? requestError.message : 'Batch experiment failed.')
    } finally {
      finishOperation()
    }
  }

  const applyIntervention = async (input: { action: string; target: string; intensity: number; rationale: string }) => {
    if (!beginOperation('intervention', true)) return
    try {
      if (offline) {
        const current = stateByUniverse[activeUniverse] || buildFallbackState(activeUniverse)
        const record: InterventionRecord = { id: `preview-${Date.now()}`, label: input.action.replaceAll('_', ' '), target: input.target, state: 'queued', timestamp: 'just now', effect: 'Awaiting live observation' }
        setUniverseState(activeUniverse, { ...current, interventions: [record, ...current.interventions] })
        showToast({ tone: 'warning', message: 'Intervention queued in preview only.' })
      } else {
        await api.intervene(activeUniverse, input)
        setUniverseState(activeUniverse, await api.getState(activeUniverse))
        showToast({ tone: 'success', message: 'Intervention applied. Watch the next tick for its wake.' })
      }
    } catch (requestError) {
      if (isUnavailable(requestError)) enterOfflinePreview()
      else setError(requestError instanceof Error ? requestError.message : 'Intervention failed.')
    } finally {
      finishOperation()
    }
  }

  const selectCitizen = (citizen: Citizen) => {
    setSelectedCitizen(citizen)
    if (offline) {
      const fallback = getFallbackCitizen(activeUniverse, citizen.id)
      if (fallback) setSelectedCitizen(fallback)
      return
    }
  }

  if (loading) return <LoadingScreen />
  if (!state || !otherState) return <div className="loading-screen"><div className="loading-mark"><Icon name="activity" size={24} /></div><span className="eyebrow">Simulation unavailable</span><h1>State did not arrive.</h1><p>{error || 'The API returned no universe snapshots.'}</p><button className="button primary" onClick={() => void refresh()} disabled={busyAction === 'refresh'}><Icon name="refresh" size={14} /> Retry connection</button></div>

  const renderView = () => {
    switch (activeView) {
      case 'overview': return <OverviewView state={state} otherState={otherState} offline={offline} onDemo={() => void runDemo()} demoRunning={demoRunning} onNavigate={setActiveView} onTick={() => void tick()} onReset={() => void reset()} />
      case 'social': return <SocialUniverseView state={state} />
      case 'citizens': return <CitizenObservatoryView state={state} selected={selectedCitizen} onSelect={(citizen) => void selectCitizen(citizen)} onNavigate={setActiveView} />
      case 'information': return <InformationEcosystemView state={state} />
       case 'lab': return <ButterflyLabView state={state} otherState={otherState} universeId={activeUniverse} offline={offline} comparison={comparison} causalComparison={causalComparison} impactSummary={impactSummary} causalGraph={causalGraph} history={experimentRuns} experimentHistory={experimentHistory} batches={experimentBatches} runningId={busyAction?.startsWith('experiment-') ? busyAction.replace('experiment-', '') : undefined} batchRunningId={busyAction?.startsWith('batch-') ? busyAction.replace('batch-', '') : undefined} onRun={(experiment) => void runExperiment(experiment)} onRunBatch={(experiment) => void runBatch(experiment)} onBuilderBusy={builderBusy} />
       case 'interventions': return <fieldset className="view-lock" disabled={offline}><InterventionConsoleView key={activeUniverse} state={state} submitting={busyAction === 'intervention'} onSubmit={(input) => void applyIntervention(input)} /></fieldset>
      case 'mind': return <AgentMindView state={state} />
    }
  }

  const navButton = (view: ViewId) => <button key={view} disabled={Boolean(busyAction)} aria-current={activeView === view ? 'page' : undefined} className={`nav-item ${activeView === view ? 'active' : ''}`} onClick={() => { setActiveView(view); setMobileNavOpen(false) }}><Icon name={viewMeta[view].icon} size={17} /><span>{viewMeta[view].label}</span>{activeView === view && <i />}</button>

  return <div className="app-shell">
    <a className="skip-link" href="#screen-content">Skip to screen content</a>
    <aside className={`sidebar ${mobileNavOpen ? 'open' : ''}`}>
      <div className="brand"><div className="brand-mark"><Icon name="spark" size={19} /></div><div><strong>NPC<span>·</span>Internet</strong><small>THE BUTTERFLY EFFECT</small></div><button className="mobile-close icon-button" aria-label="Close navigation" onClick={() => setMobileNavOpen(false)}><Icon name="close" size={18} /></button></div>
      <div className="universe-switcher"><span className="sidebar-label">Active universe</span><div className="universe-tabs">{(['prime', 'echo'] as UniverseId[]).map((universeId) => <button key={universeId} disabled={Boolean(busyAction)} aria-pressed={activeUniverse === universeId} className={activeUniverse === universeId ? 'active' : ''} onClick={() => selectUniverse(universeId)}><span className={`universe-glyph ${universeId}`} />{universeId}<small>live universe</small></button>)}</div></div>
      <nav className="main-nav" aria-label="Screens"><span className="sidebar-label">Observe</span>{primaryNav.map(navButton)}<span className="sidebar-label nav-section-label">Experiment</span>{experimentNav.map(navButton)}</nav>
      <div className="sidebar-bottom"><div className="api-status"><span className={`api-status__dot ${offline ? 'offline' : 'online'}`} /><div><strong>{offline ? 'Offline preview' : 'API connected'}</strong><small>{offline ? 'illustrative sample data' : API_URL.replace(/^https?:\/\//, '') || 'same origin'}</small></div></div><button className="sidebar-settings" disabled={Boolean(busyAction)} onClick={() => setActiveView('lab')}><Icon name="settings" size={16} /><span>Experiment configuration</span></button></div>
    </aside>
    {mobileNavOpen && <button className="sidebar-scrim" aria-label="Close navigation" onClick={() => setMobileNavOpen(false)} />}
    <main className="main-content"><header className="topbar"><div className="topbar-left"><button className="mobile-menu icon-button" aria-label="Open navigation" aria-expanded={mobileNavOpen} onClick={() => setMobileNavOpen(true)}><Icon name="menu" size={20} /></button><div className="breadcrumb"><span>{viewMeta[activeView].eyebrow}</span><Icon name="chevron" size={13} /><strong>{viewMeta[activeView].label}</strong></div></div><div className="topbar-actions"><StatusPill tone={offline ? 'amber' : 'green'}>{offline ? 'Offline preview' : 'Live connection'}</StatusPill><span className="topbar-divider" /><span className="tick-readout">TICK <strong>{formatNumber(state.tick)}</strong></span><button className="icon-button refresh-button" onClick={() => void refresh()} disabled={Boolean(busyAction)} aria-label="Sync with API"><Icon name="refresh" size={17} className={busyAction === 'refresh' ? 'spin' : ''} /></button></div></header>
      <div className="content-wrap">{error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}{toast && <div className={`toast ${toast.tone}`} role="status"><Icon name={toast.tone === 'success' ? 'spark' : 'activity'} size={15} />{toast.message}<button className="icon-button" onClick={() => setToast(null)} aria-label="Dismiss notification"><Icon name="close" size={14} /></button></div>}
        {offline && <p className="builder-alert warning" role="status">Illustrative offline preview · these samples are not recorded simulation outcomes. Live actions require Sync with API.</p>}
        {busyAction && <p className="sr-only" role="status">Working: {busyAction.replaceAll('-', ' ')}. Other simulation actions are temporarily disabled.</p>}
        <fieldset id="screen-content" tabIndex={-1} className="view-content view-lock" disabled={Boolean(busyAction)} aria-busy={Boolean(busyAction)}>{renderView()}</fieldset>
        <footer className="app-footer"><span>NPC INTERNET · THE BUTTERFLY EFFECT</span><span>Data refreshes at tick boundaries · <button disabled={Boolean(busyAction)} onClick={() => setActiveView('overview')}>Read metric definitions</button></span></footer>
      </div>
    </main>
  </div>
}
