/** Shared transport: HTTP/validation failures are distinct from lost connectivity. */
export class ApiUnavailableError extends Error {
  constructor(message = 'The simulation API is unreachable.') {
    super(message)
    this.name = 'ApiUnavailableError'
  }
}

export class ApiResponseError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiResponseError'
    this.status = status
    this.detail = detail
  }
}

export class ApiTimeoutError extends Error {
  constructor() {
    super('The API request timed out. The server may still be processing it; sync or refresh saved history before retrying a write.')
    this.name = 'ApiTimeoutError'
  }
}

export function responseErrorMessage(payload: unknown, status: number): string {
  if (typeof payload === 'string' && payload.trim()) return payload
  const root = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {}
  const detail = root.detail ?? root.message
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((item: unknown) => {
      if (!item || typeof item !== 'object') return String(item)
      const issue = item as Record<string, unknown>
      const path = Array.isArray(issue.loc) ? issue.loc.filter((part) => part !== 'body').join('.') : ''
      return `${path ? `${path}: ` : ''}${String(issue.msg ?? issue.message ?? 'Invalid value')}`
    }).join('\n')
  }
  if (detail && typeof detail === 'object') return JSON.stringify(detail)
  return `Request failed with ${status}`
}

function createRequest(baseUrl: string, fetcher: typeof fetch, rawText: boolean, timeoutMs?: number) {
  return async <T>(path: string, options: RequestInit = {}): Promise<T> => {
    let response: Response
    let text: string
    const controller = new AbortController()
    const signal = options.signal ? AbortSignal.any([options.signal, controller.signal]) : controller.signal
    const timer = setTimeout(() => controller.abort(), timeoutMs ?? (options.method && options.method !== 'GET' ? 180_000 : 30_000))
    const headers = new Headers(options.headers)
    if (options.body !== undefined && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
    try {
      response = await fetcher(`${baseUrl}${path}`, {
        ...options,
        headers,
        signal,
      })
      text = await response.text()
    } catch (error) {
      if (options.signal?.aborted) throw options.signal.reason
      if (controller.signal.aborted) throw new ApiTimeoutError()
      if (error instanceof Error && error.name === 'AbortError') throw error
      throw new ApiUnavailableError()
    } finally { clearTimeout(timer) }
    let payload: unknown = undefined
    if (text) {
      try { payload = JSON.parse(text) as unknown } catch { payload = text }
    }
    if (!response.ok) {
      const detail = payload && typeof payload === 'object' ? (payload as Record<string, unknown>).detail : payload
      throw new ApiResponseError(response.status, responseErrorMessage(payload, response.status), detail)
    }
    if (text && typeof payload === 'string') throw new ApiResponseError(response.status, 'The API returned a non-JSON response.')
    if (!text && response.status !== 204 && response.status !== 205) throw new ApiResponseError(response.status, 'The API returned an empty response.')
    if (rawText) return text as T
    return payload as T
  }
}

export const createJsonRequest = (baseUrl: string, fetcher: typeof fetch = fetch, timeoutMs?: number) => createRequest(baseUrl, fetcher, false, timeoutMs)

/** Portable JSON must retain numeric tokens (e.g. 1.0) used by server canonical hashes. */
export function createTextRequest(baseUrl: string, fetcher: typeof fetch = fetch, timeoutMs?: number) {
  const request = createRequest(baseUrl, fetcher, true, timeoutMs)
  return (path: string, options?: RequestInit) => request<string>(path, options)
}
