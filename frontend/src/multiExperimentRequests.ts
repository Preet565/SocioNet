import type { UniverseId } from './types'
import type { MultiExperimentArmDetail, MultiExperimentConfig, MultiExperimentConfigInput, MultiExperimentDetail, MultiExperimentSummary, ValidatedExperiment } from './multiExperimentTypes'

const root = '/multi-experiments'
const runPath = (id: string) => `${root}/${encodeURIComponent(id)}`
const post = (body?: unknown): RequestInit => ({ method: 'POST', ...(body === undefined ? {} : { body: JSON.stringify(body) }) })

function withoutLegacyResultsManifest(text: string): string | undefined {
  try {
    const value = JSON.parse(text) as Record<string, unknown>
    const results = value.results
    if (!results || typeof results !== 'object' || Array.isArray(results) || !('manifest' in results)) return undefined
    delete (results as Record<string, unknown>).manifest
    return JSON.stringify(value)
  } catch {
    return undefined
  }
}

async function postBundleWithCompatibility<T>(request: <T>(path: string, options?: RequestInit) => Promise<T>, path: string, text: string): Promise<T> {
  try {
    return await request<T>(path, { method: 'POST', body: text })
  } catch (error) {
    // Older Phase 6 servers exported results.manifest but rejected that
    // optional field on import. Retry only this observed schema mismatch; all
    // integrity and validation errors remain the original HTTP error.
    if (!(error instanceof Error) || !/results\.manifest.*Extra inputs are not permitted/i.test(error.message)) throw error
    const compatible = withoutLegacyResultsManifest(text)
    if (!compatible) throw error
    return request<T>(path, { method: 'POST', body: compatible })
  }
}

export function createMultiExperimentApi(request: <T>(path: string, options?: RequestInit) => Promise<T>, requestText: (path: string, options?: RequestInit) => Promise<string>) {
  return {
    schema: (signal?: AbortSignal) => request<Record<string, unknown>>(`${root}/schema`, { signal }),
    validate: (config: MultiExperimentConfigInput) => request<ValidatedExperiment>(`${root}/validate`, post(config)),
    create: (config: MultiExperimentConfig, expectedSourceHash?: string) => {
      const query = expectedSourceHash === undefined ? '' : `?${new URLSearchParams({ expected_source_hash: expectedSourceHash })}`
      return request<MultiExperimentDetail>(`${root}${query}`, post(config))
    },
    list: (universeId: UniverseId, signal?: AbortSignal) => request<{ experiments: MultiExperimentSummary[] }>(`${root}?${new URLSearchParams({ universe_id: universeId, limit: '50' })}`, { signal }),
    detail: (id: string, signal?: AbortSignal) => request<MultiExperimentDetail>(runPath(id), { signal }),
    config: (id: string) => request<MultiExperimentConfig>(`${runPath(id)}/config`),
    bundleText: (id: string) => requestText(`${runPath(id)}/export`),
    rerun: (id: string) => request<MultiExperimentDetail>(`${runPath(id)}/rerun`, post()),
     validateBundleText: (text: string) => postBundleWithCompatibility<ValidatedExperiment>(request, `${root}/import/validate`, text),
     importBundleText: (text: string) => postBundleWithCompatibility<MultiExperimentDetail>(request, `${root}/import`, text),
    arm: (id: string, armId: string, options: { fromTick?: number; toTick?: number; limit?: number; eventLimit?: number; signal?: AbortSignal } = {}) => {
      const params = new URLSearchParams({ limit: String(options.limit ?? 200) })
      if (options.eventLimit !== undefined) params.set('event_limit', String(options.eventLimit))
      if (options.fromTick !== undefined) params.set('from_tick', String(options.fromTick))
      if (options.toTick !== undefined) params.set('to_tick', String(options.toTick))
      return request<MultiExperimentArmDetail>(`${runPath(id)}/arms/${encodeURIComponent(armId)}?${params}`, { signal: options.signal })
    },
  }
}
