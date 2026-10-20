import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { setTimeout as delay } from 'node:timers/promises'
import { createJsonRequest, createTextRequest, ApiResponseError } from '../src/apiRequest.ts'
import { createMultiExperimentApi } from '../src/multiExperimentRequests.ts'
import { createExperimentConfig, parseExperimentJson } from '../src/multiExperimentHelpers.ts'

// Opt-in: this starts a real Python HTTP server, uses the frontend transport,
// and persists only to a disposable database. No browser or external service.
test('live Phase 6 workflow: preview, three arms, histories, import, rerun and restart', {
  skip: process.env.SOCIONET_LIVE_TEST !== '1', timeout: 90_000,
}, async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'socionet-phase6-'))
  const portReservation = createServer()
  await new Promise((resolve) => portReservation.listen(0, '127.0.0.1', resolve))
  const port = portReservation.address().port
  await new Promise((resolve) => portReservation.close(resolve))
  const base = `http://127.0.0.1:${port}`
  const request = createJsonRequest(base)
  const api = createMultiExperimentApi(request, createTextRequest(base))
  let child
  let logs = ''
  const stop = async () => {
    if (!child || child.exitCode !== null) return
    const processToStop = child
    const exited = new Promise((resolve) => processToStop.once('exit', resolve))
    processToStop.kill('SIGTERM')
    await Promise.race([exited, delay(5000)])
    if (processToStop.exitCode === null) { processToStop.kill('SIGKILL'); await exited }
  }
  const start = async () => {
    let spawnError
    child = spawn(process.env.SOCIONET_TEST_PYTHON || 'python3', [
      '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port),
    ], {
      cwd: fileURLToPath(new URL('../../backend/', import.meta.url)),
      env: { ...process.env, SOCIONET_DB_PATH: join(temporary, 'workflow.sqlite3'), SOCIONET_SERVE_FRONTEND: '0' },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    child.on('error', (error) => { spawnError = error })
    for (const stream of [child.stdout, child.stderr]) stream.on('data', (chunk) => { logs = (logs + chunk).slice(-6000) })
    for (let attempt = 0; attempt < 150; attempt++) {
      if (spawnError) throw spawnError
      if (child.exitCode !== null) throw new Error(`Backend exited: ${logs}`)
      try { if ((await fetch(`${base}/health`)).ok) return } catch { /* Waiting for server startup only. */ }
      await delay(100)
    }
    throw new Error(`Backend did not become healthy: ${logs}`)
  }

  try {
    await start()
    const canonical = await request('/state')
    const config = createExperimentConfig('prime')
    Object.assign(config, { ticks: 2, simulation: { population: 6 }, agent_composition: {} })
    config.arms.push({ id: 'immediate', name: 'Immediate correction', schedule: [{ ...config.arms[1].schedule[0], at_tick: 0 }] })
    const preview = await api.validate(config)
    assert.equal(preview.plan.arm_count, 3)
    assert.equal(preview.plan.agent_ticks, 36)
    assert.match(preview.plan.source_hash, /^[0-9a-f]{64}$/)
    const run = await api.create(preview.config, preview.plan.source_hash)
    assert.equal(run.status, 'completed')
    assert.equal(run.arms.length, 3)
    assert.deepEqual([...new Set(run.arms.map((arm) => arm.initial_snapshot_hash))], [run.source_hash])
    const histories = await Promise.all(run.arms.map((arm) => api.arm(run.id, arm.id)))
    assert.ok(histories.every((arm) => arm.samples.length === 3 && arm.events.length > 0))
    const delayedEvidence = histories[1].state.contents.find((content) => content.is_counterevidence)
    assert.equal(delayedEvidence.created_tick, 2)
    assert.equal(delayedEvidence.claim_id, config.claims[0].id)
    assert.deepEqual(await request('/state'), canonical, 'multi-arm execution must not mutate Prime')
    assert.equal((await api.list('prime')).experiments[0].id, run.id)
    const exportedConfig = await api.config(run.id)
    assert.deepEqual(parseExperimentJson(JSON.stringify(exportedConfig)).config, exportedConfig)
    const bundle = await api.bundleText(run.id)
    assert.equal(parseExperimentJson(bundle).kind, 'bundle')
    assert.equal((await api.validateBundleText(bundle)).plan.source_hash, run.source_hash)
    const imported = await api.importBundleText(bundle)
    const rerun = await api.rerun(run.id)
    for (const repeated of [imported, rerun]) {
      assert.notEqual(repeated.id, run.id)
      assert.equal(repeated.source_hash, run.source_hash)
      assert.deepEqual(repeated.arms.map((arm) => arm.metrics), run.arms.map((arm) => arm.metrics))
    }
    await assert.rejects(() => api.validate({ ...config, ticks: 21 }), (error) => error instanceof ApiResponseError && error.status === 422)
    await stop()
    await start()
    assert.equal((await api.detail(run.id)).source_hash, run.source_hash)
    assert.equal((await api.arm(run.id, 'baseline')).samples.length, 3)
    assert.equal((await api.list('prime')).experiments.length, 3)
    assert.deepEqual(await request('/state'), canonical, 'restart must restore canonical Prime, not an arm')
  } finally {
    await stop()
    await rm(temporary, { recursive: true, force: true })
  }
})
