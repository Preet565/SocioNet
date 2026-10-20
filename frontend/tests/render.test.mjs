import { after, before, test } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

// Use the project's existing TSX pipeline. Middleware mode never opens a port.
let server
let Builder
let Results
let ConfigForm
let fallback
let createConfig
let parseJson
let Overview
let Comparison
let Intervention
let Feed
before(async () => {
  server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  ;({ default: Builder } = await server.ssrLoadModule('/src/ExperimentBuilder.tsx'))
  ;({ ExperimentResults: Results } = await server.ssrLoadModule('/src/ExperimentResults.tsx'))
  ;({ ExperimentConfigForm: ConfigForm } = await server.ssrLoadModule('/src/ExperimentConfigForm.tsx'))
  ;({ buildFallbackState: fallback } = await server.ssrLoadModule('/src/data.ts'))
  ;({ createExperimentConfig: createConfig } = await server.ssrLoadModule('/src/multiExperimentHelpers.ts'))
  ;({ parseExperimentJson: parseJson } = await server.ssrLoadModule('/src/multiExperimentHelpers.ts'))
  ;({ OverviewView: Overview, ComparisonResult: Comparison, InterventionConsoleView: Intervention, FeedItem: Feed } = await server.ssrLoadModule('/src/App.tsx'))
})
after(async () => { await server?.close() })

test('builder renders labeled controls and Run stays disabled before validation', () => {
  const markup = renderToStaticMarkup(React.createElement(Builder, { universeId: 'prime', offline: false, universeState: fallback('prime') }))
  for (const text of ['One question. Multiple possible worlds.', 'Experiment name', 'Hypothesis', 'Population (1–100)', 'Initial topology', 'Claim configuration ID', 'Use arm 1 as baseline', 'Advanced experiment configuration JSON', 'Import experiment JSON file', 'Saved multi-arm experiments']) assert.ok(markup.includes(text), text)
  assert.match(markup, /disabled=""[^>]*aria-describedby="builder-run-help"/)
  assert.match(markup, /aria-labelledby="experiment-builder-title"/)
  assert.match(markup, /≤ 2,000 agent-ticks/)
  assert.match(markup, /Agent-ticks \/ effective budget/)
  assert.match(markup, /Effective budget: min\(2,000, configured budget\)/)
  assert.doesNotMatch(markup, /≤ 5,000 agent-ticks|local portable-source preview|preview \(local\)/i)
  assert.doesNotMatch(markup, /Saved experiment \/|Recorded final metrics/)
})

test('universe inputs without seed or simulation render safely and defer generation settings', () => {
  const draft = createConfig('echo')
  draft.source.mode = 'universe'
  draft.agent_composition = {}
  delete draft.seed
  delete draft.simulation
  const imported = parseJson(JSON.stringify(draft))
  const markup = renderToStaticMarkup(React.createElement(ConfigForm, { config: imported.config, onChange: () => {}, universeState: fallback('echo') }))
  assert.match(markup, /captured on validation/)
  assert.match(markup, /prepared source hash/)
  assert.match(markup, /Generation settings below apply to generated sources/)
  assert.doesNotMatch(markup, /NaN|undefined/)
})

test('offline builder explains recovery and disables API actions without fabricating a result', () => {
  const markup = renderToStaticMarkup(React.createElement(Builder, { universeId: 'echo', offline: true, universeState: fallback('echo') }))
  assert.match(markup, /Offline preview/)
  assert.match(markup, /Sync with API/)
  assert.match(markup, /Saved experiments are available when the API reconnects/)
  assert.match(markup, /disabled="">Validate &amp; preview plan/)
  assert.match(markup, /disabled=""[^>]*aria-describedby="builder-run-help"/)
  assert.doesNotMatch(markup, /Recorded final metrics/)
})

test('saved results display API metrics/deltas, missing values, failures and exact provenance', () => {
  const draft = createConfig('prime')
  const run = {
    id: 'test-record', name: 'Recorded comparison', status: 'failed', created_at: '2026-10-20T12:00:00Z',
    config: draft, config_hash: 'saved-config-hash', source_hash: 'saved-source-hash', source_snapshot_id: 11,
    source_tick: 7, baseline_arm_id: 'baseline', error: 'One arm failed.',
    arms: [
      { id: 'baseline', name: 'No correction', status: 'completed', initial_snapshot_hash: 'saved-source-hash', final_snapshot_id: 12, metrics: { exposure_rate: 0, fact_checks: 4 }, metric_deltas: { exposure_rate: 0, fact_checks: 0 }, earliest_divergence: null },
      { id: 'correction', name: 'Delayed correction', status: 'failed', initial_snapshot_hash: 'saved-source-hash', final_snapshot_id: null, metrics: { exposure_rate: 0.25 }, metric_deltas: { exposure_rate: 0.25 }, error: 'Branch execution failed.', earliest_divergence: null },
    ],
  }
  const markup = renderToStaticMarkup(React.createElement(Results, { run, offline: true }))
  for (const text of ['saved-config-hash', 'saved-source-hash', 'One arm failed.', 'Branch execution failed.', 'Final metrics', 'Branch to inspect', 'From recorded tick', 'No correction (baseline)']) assert.ok(markup.includes(text), text)
  assert.match(markup, /<td>0<\/td>/, 'real zero is retained')
  assert.match(markup, /Δ \+0.25/)
  assert.match(markup, /<td>—<small>Δ —<\/small><\/td>/, 'missing measurements stay missing')
  assert.match(markup, /Offline: reconnect to retrieve branch history/)
})

test('legacy screens disclose calculated projections and detached branch provenance', () => {
  const state = fallback('prime')
  const overview = renderToStaticMarkup(React.createElement(Overview, { state, otherState: fallback('echo'), offline: false, onDemo: () => {}, demoRunning: false, onNavigate: () => {}, onTick: () => {}, onReset: () => {} }))
  assert.match(overview, /Relationship affinity/)
  assert.match(overview, /Evidence complement/)
  assert.match(overview, /detached-branch demo/)
  const comparison = renderToStaticMarkup(React.createElement(Comparison, { comparison: { experiment_id: 'run', universe_id: 'prime', seed: 1, ticks: 2, run_id: 'run-1', control_metrics: { average_belief_confidence: 0.2, fact_checks: 0 }, treatment_metrics: { average_belief_confidence: 0.4, fact_checks: 1 }, metric_deltas: { average_belief_confidence: 0.2, fact_checks: 1 }, earliest_divergence: null } }))
  assert.match(comparison, /saved branches are not the live Prime\/Echo snapshots/)
  const intervention = renderToStaticMarkup(React.createElement(Intervention, { state, submitting: false, onSubmit: () => {} }))
  assert.match(intervention, /Publish counterevidence/)
  assert.match(intervention, /target is not used/i)
  const post = state.posts?.[0] ?? { id: 'post', author_id: 'citizen', author: 'Citizen', kind: 'post', topic: 'topic', text: 'sample', claim_truth: false, parent_id: null, created_tick: 0, credibility: 0.4, likes: 0, fact_checked: false, is_counterevidence: false, claim_id: null, exposureCount: 0 }
  const feed = renderToStaticMarkup(React.createElement(Feed, { post }))
  assert.match(feed, /simulated truth: false|no truth label/)
})
