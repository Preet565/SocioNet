import test from 'node:test'
import assert from 'node:assert/strict'
import {
  canRunExperiment, configIssues, configShapeErrors, createExperimentConfig, effectiveAgentTickBudget, EXPERIMENT_LIMITS,
  formatMetric, metricKeys, metricTimeline, newIntervention, nextConfigId, numericMetric,
  parseExperimentJson, plannedAgentTicks, renameArm,
  renameClaim, validationInput,
} from '../src/multiExperimentHelpers.ts'
import { createMultiExperimentApi } from '../src/multiExperimentRequests.ts'
import { ApiResponseError, ApiUnavailableError, createJsonRequest, createTextRequest } from '../src/apiRequest.ts'

const config = () => createExperimentConfig('prime')
const validation = (draft) => ({ valid: true, config: structuredClone(draft), plan: { source_tick: 0 } })
const available = { offline: false, busy: false, jsonDirty: false, issues: [] }
const portable = (draft = config()) => ({
  bundle_version: 1, schema_version: 1, engine_version: 'socionet-phase6-v1', hash_algorithm: 'sha256',
  canonical_json: 'utf8-sort_keys-compact-ensure_ascii_false', config: draft, config_hash: 'a'.repeat(64),
  source_hash: 'b'.repeat(64), results: { id: 'saved-run', status: 'completed' },
  source_snapshot: { tick: 7, seed: draft.seed, agents: Object.fromEntries(Array.from({ length: draft.simulation.population }, (_, i) => [`citizen-${i + 1}`, {}])) },
})

test('initial builder is a bounded two-arm experiment with an explicit shared claim and author', () => {
  const draft = config()
  assert.deepEqual(configShapeErrors(draft), [])
  assert.deepEqual(configIssues(draft), [])
  assert.equal(plannedAgentTicks(draft), 360)
  assert.equal(draft.claims[0].author_id, 'citizen-001')
  assert.equal(draft.arms[1].schedule[0].claim_id, draft.claims[0].id)
  assert.equal(draft.source.universe_id, 'prime')
  assert.equal(createExperimentConfig('echo').source.universe_id, 'echo')
})

test('run is disabled until server validation and after any edited configuration', () => {
  const draft = config()
  assert.equal(canRunExperiment(draft, undefined, available), false)
  const checked = validation(draft)
  assert.equal(canRunExperiment(draft, checked, available), true)
  for (const [key, value] of [['name', 'Edited question'], ['seed', 2], ['ticks', 5], ['baseline_arm_id', 'correction']]) {
    assert.equal(canRunExperiment({ ...draft, [key]: value }, checked, available), false, key)
  }
  const changedSchedule = structuredClone(draft)
  changedSchedule.arms[1].schedule[0].at_tick = 3
  assert.equal(canRunExperiment(changedSchedule, checked, available), false)
})

test('offline, pending JSON, stale source, request loading, and invalid input all block execution', () => {
  const draft = config()
  for (const condition of [{ offline: true }, { jsonDirty: true }, { busy: true }, { staleSource: true }, { issues: ['over budget'] }]) {
    assert.equal(canRunExperiment(draft, validation(draft), { ...available, ...condition }), false)
  }
})

test('budget estimates count every arm and actual universe population', () => {
  const draft = config()
  draft.source.mode = 'universe'
  assert.equal(plannedAgentTicks(draft, 70), 840)
  assert.match(configIssues(draft, 101).join('\n'), /Source population exceeds 100/)
  draft.ticks = 20
  draft.arms = Array.from({ length: 6 }, (_, i) => ({ id: `arm-${i}`, name: `Arm ${i}`, schedule: [] }))
  draft.baseline_arm_id = 'arm-0'
  assert.match(configIssues(draft, 100).join('\n'), /12000 agent-ticks exceeds/)
})

test('effective execution cap is 2,000 even when the configured budget is 5,000', () => {
  const draft = config()
  draft.simulation.population = 100
  draft.agent_composition = { civic_participant: 100 }
  draft.ticks = 10
  draft.computational_budget = 5000
  assert.equal(EXPERIMENT_LIMITS.agentTicks, 2000)
  assert.equal(EXPERIMENT_LIMITS.computationalBudget, 5000)
  assert.equal(effectiveAgentTickBudget(5000), 2000)
  assert.equal(effectiveAgentTickBudget(1200), 1200)
  assert.equal(plannedAgentTicks(draft), 2000)
  assert.deepEqual(configIssues(draft), [], 'exact hard cap is allowed')
  draft.ticks = 11
  assert.match(configIssues(draft).join('\n'), /2200 agent-ticks exceeds the effective budget \(2000\)/)
  draft.ticks = 10
  draft.computational_budget = 1999
  assert.match(configIssues(draft).join('\n'), /effective budget \(1999\)/)
  draft.computational_budget = 5001
  assert.match(configIssues(draft).join('\n'), /computational_budget must be an integer from 2 to 5000/)
})

test('enforces arm, population, ticks, claim and per-arm schedule caps', () => {
  const draft = config()
  draft.simulation.population = 101
  draft.ticks = 21
  draft.arms = Array.from({ length: 7 }, (_, i) => ({ id: `arm-${i}`, name: `Arm ${i}`, schedule: Array.from({ length: 17 }, () => ({ at_tick: 0, intervention: newIntervention('seed_post') })) }))
  draft.claims = Array.from({ length: 17 }, (_, i) => ({ ...draft.claims[0], id: `claim-${i}` }))
  const issues = configIssues(draft).join('\n')
  for (const message of ['simulation.population', 'ticks', 'arm count', 'At most 16 claims', 'at most 16 scheduled interventions']) assert.ok(issues.includes(message), message)
})

test('generated composition must match population; universe validation captures its own configuration', () => {
  const draft = config()
  draft.agent_composition.civic_participant++
  assert.match(configIssues(draft).join('\n'), /counts must sum/)
  draft.source.mode = 'universe'
  const input = validationInput(draft)
  assert.equal('seed' in input, false)
  assert.equal('simulation' in input, false)
  assert.deepEqual(input.agent_composition, {})
  assert.equal(draft.agent_composition.civic_participant, 11, 'does not mutate editor state')
  assert.equal(validationInput(config()).seed, 9001)
})

test('renaming claim/arm IDs retains schedule references and baseline identity', () => {
  let draft = config()
  draft = renameClaim(draft, 0, 'new-key')
  assert.equal(draft.arms[1].schedule[0].claim_id, 'new-key')
  draft = renameArm(draft, 0, 'new-baseline')
  assert.equal(draft.baseline_arm_id, 'new-baseline')
  assert.deepEqual(configIssues(draft), [])
})

test('missing claim references, duplicate IDs, unmatched topics, and missing authors are actionable', () => {
  const draft = config()
  draft.arms[1].id = draft.arms[0].id
  draft.claims[0].author_id = ''
  draft.arms[1].schedule[0].claim_id = 'not-in-config'
  const issues = configIssues(draft).join('\n')
  assert.match(issues, /Arm IDs must be unique/)
  assert.match(issues, /author_id is required/)
  assert.match(issues, /claim_id must reference/)
  draft.arms[1].schedule[0].claim_id = draft.claims[0].id
  draft.arms[1].schedule[0].intervention.topic = 'other_topic'
  assert.match(configIssues(draft).join('\n'), /topic must match/)
})

test('schedule uses one relative clock; community and relationship targets obey model shape', () => {
  const draft = config()
  const entry = draft.arms[1].schedule[0]
  entry.at_tick = 7
  entry.intervention.delay_ticks = 1
  entry.target_community_id = 'community-1'
  let issues = configIssues(draft).join('\n')
  assert.match(issues, /at_tick must be an integer from 0 to 6/)
  assert.match(issues, /delay_ticks must be zero/)
  assert.match(issues, /community targets support only/)
  entry.intervention = newIntervention('suppress_relationship')
  delete entry.target_community_id
  delete entry.claim_id
  issues = configIssues(draft).join('\n')
  assert.match(issues, /source citizen is required/)
  assert.match(issues, /target citizen is required/)
  assert.deepEqual(newIntervention('suppress_citizen'), { kind: 'suppress_citizen', agent_id: '' })
})

test('import reports syntax/shape errors before malformed data can reach the form', () => {
  assert.throws(() => parseExperimentJson('{'), /Invalid JSON/)
  for (const value of [null, [], { schema_version: 2 }, { ...config(), arms: [{ id: 'one', name: 'One', schedule: [null] }] }, { ...config(), claims: [null] }, { ...config(), simulation: { population: 10, initial_network: [] } }]) {
    assert.throws(() => parseExperimentJson(JSON.stringify(value)))
  }
  const imported = parseExperimentJson(JSON.stringify(config()))
  assert.equal(imported.kind, 'config')
  assert.deepEqual(imported.config, config())
})

test('universe config-only imports can omit or defer seed and simulation until server validation', () => {
  const draft = config()
  draft.source = { mode: 'universe', universe_id: 'echo' }
  draft.agent_composition = {}
  delete draft.seed
  delete draft.simulation
  for (const deferred of [draft, { ...draft, seed: null, simulation: null }]) {
    const imported = parseExperimentJson(JSON.stringify(deferred))
    assert.equal(imported.kind, 'config')
    assert.deepEqual(imported.config, deferred, 'does not inject generation settings')
    assert.deepEqual(configIssues(imported.config), [])
    assert.deepEqual(validationInput(imported.config), draft)
    assert.equal(plannedAgentTicks(imported.config), undefined, 'unknown population stays unknown')
    assert.equal(plannedAgentTicks(imported.config, 80), 960)
  }
  assert.throws(() => parseExperimentJson(JSON.stringify({ ...draft, source: { mode: 'generated', universe_id: 'echo' } })), /seed must be a finite number/)
  assert.throws(() => parseExperimentJson(JSON.stringify({ ...draft, seed: 'invalid' })), /seed must be a finite number/)
  assert.throws(() => parseExperimentJson(JSON.stringify({ ...draft, simulation: [] })), /simulation must be an object/)
})

test('advanced simulation parameters and immutable bundle fields survive import losslessly', () => {
  const draft = config()
  draft.simulation.community_evolution = { split_size: 20, split_min_size: 3 }
  draft.simulation.archetypes = { custom: { post_bias: 1.7 } }
  const manifest = portable(draft)
  manifest.source_snapshot.contents = [{ id: 'saved-content', text: 'Immutable' }]
  const imported = parseExperimentJson(JSON.stringify(manifest))
  assert.equal(imported.kind, 'bundle')
  assert.deepEqual(imported.manifest, manifest)
  assert.throws(() => parseExperimentJson(JSON.stringify({ config: draft })), /immutable source_snapshot/)
})

test('portable validation posts exact raw bundle text and uses the authoritative plan without execution', async () => {
  const manifest = portable()
  const text = JSON.stringify(manifest).replace('"credibility":0.4', '"credibility":0.4000')
  const validated = { valid: true, config: manifest.config, plan: { source_tick: 7, source_hash: manifest.source_hash, population: 30, ticks: 6, arm_count: 2, agent_ticks: 360, baseline_arm_id: 'baseline', arms: [{ id: 'baseline', name: 'No correction', intervention_count: 0 }], warnings: ['Server warning'] } }
  const calls = []
  const api = createMultiExperimentApi(async (path, options) => { calls.push({ path, ...options }); return validated }, async () => '')
  const result = await api.validateBundleText(text)
  assert.equal(result, validated)
  assert.deepEqual(calls, [{ path: '/multi-experiments/import/validate', method: 'POST', body: text }])
  assert.equal(canRunExperiment(manifest.config, result, available), true)
})

test('server bundle integrity/reference validation failures remain actionable HTTP errors', async () => {
  const calls = []
  const fetcher = async (url, options) => { calls.push({ url, options }); return new Response(JSON.stringify({ detail: 'bundle config/source hash mismatch' }), { status: 422 }) }
  const api = createMultiExperimentApi(createJsonRequest('', fetcher), createTextRequest('', fetcher))
  await assert.rejects(api.validateBundleText(JSON.stringify(portable())), (error) => error instanceof ApiResponseError && error.status === 422 && /hash mismatch/.test(error.message))
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, '/multi-experiments/import/validate')
})

test('bundle transport retries the observed legacy results.manifest schema mismatch without hiding other 422s', async () => {
  const calls = []
  const api = createMultiExperimentApi(async (path, options) => {
    calls.push({ path, body: options.body })
    if (calls.length === 1) throw new ApiResponseError(422, 'results.manifest: Extra inputs are not permitted')
    return { valid: true, config: config(), plan: { source_tick: 0 } }
  }, async () => '')
  const manifest = portable()
  manifest.results.manifest = { engine_version: 'legacy' }
  const text = JSON.stringify(manifest)
  await api.validateBundleText(text)
  assert.equal(JSON.parse(calls[0].body).results.manifest !== undefined, true)
  assert.equal(JSON.parse(calls[1].body).results.manifest, undefined)
})

test('missing and nonnumeric measurements stay missing; real zero is shown as zero', () => {
  const metrics = { exposure_rate: 0, polarization: 0.25, nested: { number: 4 }, missing: null, string_number: '0.5', tick: 6, population: 30, nan: NaN }
  assert.equal(numericMetric(metrics, 'missing'), undefined)
  assert.equal(numericMetric(metrics, 'string_number'), undefined)
  assert.equal(formatMetric(undefined), '—')
  assert.equal(formatMetric(numericMetric(metrics, 'exposure_rate')), '0')
  assert.equal(formatMetric(-0.1, true), '-0.1')
  assert.equal(formatMetric(0.1, true), '+0.1')
  assert.deepEqual(metricKeys(metrics), ['exposure_rate', 'polarization'])
})

test('timeline sorts recorded ticks and breaks lines at missing metrics or tick gaps', () => {
  const timeline = metricTimeline([
    { tick: 4, metrics: { exposure_rate: 0.4 } },
    { tick: 0, metrics: { exposure_rate: 0 } },
    { tick: 2, metrics: {} },
    { tick: 1, metrics: { exposure_rate: 0.1 } },
    { tick: 7, metrics: { exposure_rate: 0.7 } },
  ], 'exposure_rate')
  assert.deepEqual(timeline.ticks, [0, 1, 2, 4, 7])
  assert.deepEqual(timeline.segments, [[{ tick: 0, value: 0 }, { tick: 1, value: 0.1 }], [{ tick: 4, value: 0.4 }], [{ tick: 7, value: 0.7 }]])
})

test('adding branches/claims chooses an unused config key', () => {
  assert.equal(nextConfigId('arm', ['arm-1', 'arm-3']), 'arm-2')
})

test('422 preserves validation detail and renders nested field paths', async () => {
  const detail = [{ loc: ['body', 'arms', 1, 'schedule', 0, 'at_tick'], msg: 'must not exceed ticks', type: 'value_error' }]
  const request = createJsonRequest('http://api.test', async () => new Response(JSON.stringify({ detail }), { status: 422 }))
  await assert.rejects(request('/multi-experiments/validate'), (error) => {
    assert.ok(error instanceof ApiResponseError)
    assert.equal(error.status, 422)
    assert.match(error.message, /arms.1.schedule.0.at_tick: must not exceed ticks/)
    assert.deepEqual(error.detail, detail)
    assert.equal(error instanceof ApiUnavailableError, false)
    return true
  })
})

test('network loss and HTTP failure retain distinct recovery behavior', async () => {
  await assert.rejects(createJsonRequest('', async () => { throw new TypeError('Failed to fetch') })('/health'), ApiUnavailableError)
  await assert.rejects(createJsonRequest('', async () => new Response(JSON.stringify({ detail: 'Database unavailable' }), { status: 503 }))('/multi-experiments'), (error) => error instanceof ApiResponseError && error.status === 503 && error.message === 'Database unavailable')
})

test('aborted requests are not reported as offline and invalid successful payloads fail clearly', async () => {
  const abort = new DOMException('cancelled', 'AbortError')
  await assert.rejects(createJsonRequest('', async () => { throw abort })('/health'), (error) => error === abort)
  await assert.rejects(createJsonRequest('', async () => new Response('<html>proxy page</html>'))('/multi-experiments'), /non-JSON response/)
  await assert.rejects(createTextRequest('', async () => new Response('<html>proxy page</html>'))('/multi-experiments/saved/export'), /non-JSON response/)
})

test('transport distinguishes a timed-out request from an unavailable API', async () => {
  const { ApiTimeoutError } = await import('../src/apiRequest.ts')
  const request = createJsonRequest('', async (_url, options) => await new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new DOMException('timed out', 'AbortError')), { once: true })
  }), 5)
  await assert.rejects(request('/slow'), ApiTimeoutError)
})

test('API sends configs directly, preserves manifest, and reruns without reconstructing the source', async () => {
  const calls = []
  const api = createMultiExperimentApi(async (path, options = {}) => { calls.push({ path, ...options }); return {} }, async () => '')
  const draft = config()
  const manifest = portable(draft)
  await api.validate(draft)
  await api.create(draft)
  const text = JSON.stringify(manifest)
  await api.importBundleText(text)
  await api.rerun('run/id')
  assert.deepEqual(calls.slice(0, 3).map((call) => JSON.parse(call.body)), [draft, draft, manifest])
  assert.deepEqual(calls.map((call) => call.path), ['/multi-experiments/validate', '/multi-experiments', '/multi-experiments/import', '/multi-experiments/run%2Fid/rerun'])
  assert.ok(calls.every((call) => call.method === 'POST'))
  assert.equal(calls[3].body, undefined)
})

test('execution pins the validated source hash as an encoded optional query parameter', async () => {
  const calls = []
  const api = createMultiExperimentApi(async (path, options) => { calls.push({ path, ...options }); return {} }, async () => '')
  const draft = config()
  const checked = { ...validation(draft), plan: { source_tick: 0, source_hash: 'source/hash +?&=' } }
  await api.create(checked.config, checked.plan.source_hash)
  assert.equal(calls[0].path, '/multi-experiments?expected_source_hash=source%2Fhash+%2B%3F%26%3D')
  assert.equal(new URL(calls[0].path, 'http://api.test').searchParams.get('expected_source_hash'), checked.plan.source_hash)
  assert.deepEqual(JSON.parse(calls[0].body), draft, 'hash is not injected into the config')
  const legacy = validation(draft)
  await api.create(legacy.config, legacy.plan.source_hash)
  assert.equal(calls[1].path, '/multi-experiments', 'older validation responses omit the optional query')
  assert.equal(canRunExperiment(draft, legacy, available), true)
})

test('source conflicts retain HTTP 409 for builder validation invalidation and revalidation', async () => {
  const api = createMultiExperimentApi(createJsonRequest('', async () => new Response(JSON.stringify({ detail: 'Prepared source changed since validation' }), { status: 409 })), async () => '')
  await assert.rejects(api.create(config(), 'old-hash'), (error) => error instanceof ApiResponseError && error.status === 409 && /source changed/.test(error.message))
  assert.equal(canRunExperiment(config(), undefined, available), false, 'cleared validation disables execution')
  assert.equal(canRunExperiment(config(), validation(config()), available), true, 'fresh validation enables execution')
})

test('history and branch requests encode IDs, tick-zero filters, limits and cancellation', async () => {
  const calls = []
  const api = createMultiExperimentApi(async (path, options) => { calls.push({ path, options }); return {} }, async (path) => { calls.push({ path }); return '' })
  const signal = new AbortController().signal
  await api.schema(signal)
  await api.list('echo', signal)
  await api.arm('run/id', 'arm name', { fromTick: 0, toTick: 6, limit: 50, eventLimit: 50, signal })
  await api.config('run/id')
  await api.bundleText('run/id')
  assert.equal(calls[0].path, '/multi-experiments/schema')
  assert.equal(calls[1].path, '/multi-experiments?universe_id=echo&limit=50')
  assert.equal(calls[2].path, '/multi-experiments/run%2Fid/arms/arm%20name?limit=50&event_limit=50&from_tick=0&to_tick=6')
  assert.equal(calls[2].options.signal, signal)
  assert.equal(calls[3].path, '/multi-experiments/run%2Fid/config')
  assert.equal(calls[4].path, '/multi-experiments/run%2Fid/export')
})

test('portable export/validation/import preserves server canonical numeric tokens and Unicode byte-for-byte', async () => {
  const text = '{"config":{"strength":1.0,"delta":-0.0},"source_snapshot":{"visibility":1.0,"text":"café 🌍"}}'
  const calls = []
  const fetcher = async (url, options) => { calls.push({ url, options }); return new Response(text) }
  const api = createMultiExperimentApi(createJsonRequest('http://api.test', fetcher), createTextRequest('http://api.test', fetcher))
  const exported = await api.bundleText('saved')
  assert.equal(exported, text)
  await api.validateBundleText(exported)
  await api.importBundleText(exported)
  assert.equal(calls[1].url, 'http://api.test/multi-experiments/import/validate')
  assert.equal(calls[1].options.body, text)
  assert.equal(calls[2].url, 'http://api.test/multi-experiments/import')
  assert.equal(calls[2].options.body, text)
  assert.notEqual(JSON.stringify(JSON.parse(text)), text, 'reserializing would break canonical hash inputs')
})

test('live projection transport normalizes backend snake_case history without inventing missing metrics', async () => {
  const apiModule = await import('../src/api.ts')
  const calls = []
  const request = async (path) => {
    calls.push(path)
    if (path.startsWith('/analytics/history')) return { samples: [{ tick: 0, metrics: { exposure_rate: 0, polarization: null, custom_metric: 0.25 } }], metric_definitions: { exposure_rate: 'Recorded exposures' } }
    if (path.startsWith('/network/history')) return { samples: [{ tick: 2, ties: [{ source_id: 'a', target_id: 'b', affinity: -0.2, trust: 0.8, follows: true }], degree: { a: 1 }, betweenness: { a: 0.5 } }], metric_cost: 'bounded' }
    return { samples: [{ tick: 2, memberships: { community_a: ['a', 'b'] } }], events: [{ event_id: 'e1', tick: 2, kind: 'join', community_id: 'community_a', agent_id: 'a', related_community_ids: [], member_ids: ['a'], details: 'joined' }] }
  }
  const api = apiModule.createSimulationApi(request, 'http://api.test')
  const metrics = await api.getAnalyticsHistory('prime')
  assert.equal(metrics.samples[0].metrics.exposure_rate, 0)
  assert.equal(metrics.samples[0].metrics.custom_metric, 0.25)
  assert.ok(Number.isNaN(metrics.samples[0].metrics.polarization), 'null backend metric is not silently rendered as zero')
  assert.deepEqual((await api.getNetworkHistory('prime')).samples[0].ties[0], { source_id: 'a', target_id: 'b', affinity: -0.2, trust: 0.8, follows: true })
  assert.equal((await api.getCommunityHistory('prime')).events[0].event_id, 'e1')
  assert.deepEqual(calls, ['/analytics/history?universe_id=prime', '/network/history?universe_id=prime', '/communities/history?universe_id=prime'])
})

test('catalog comparison transport preserves unknown equivalence and uses the export base URL', async () => {
  const apiModule = await import('../src/api.ts')
  const api = apiModule.createSimulationApi(async (path) => path === '/experiments/run' ? {
    experiment_id: 'rumor-counterevidence', universe_id: 'prime', seed: 9, ticks: 2,
    control_metrics: { average_belief_confidence: 0.4 }, treatment_metrics: { average_belief_confidence: 0.5 }, metric_deltas: { average_belief_confidence: 0.1 }, earliest_divergence: null,
    branch_comparison: { equivalence: 'new-server-state' },
  } : {}, 'https://api.test/')
  const result = await api.runExperiment('prime', 'rumor-counterevidence')
  assert.equal(result.branch_comparison.equivalence, 'unknown')
  assert.equal(api.experimentRunExportUrl('run/id'), 'https://api.test/experiment-runs/run%2Fid/export')
})
