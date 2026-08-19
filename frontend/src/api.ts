import {
  ApiError,
  type Defaults,
  type FrameName,
  type ReconResponse,
  type Row,
  type TunableOptions,
} from './types'

export interface UploadFiles {
  statement: File | null
  bills: File | null
  rnote: File | null
  crn: File | null
}

/** Which repo sample document backs each field when nothing is uploaded. */
export async function fetchDefaults(): Promise<Defaults> {
  const res = await fetch('/api/defaults')
  if (!res.ok) throw new ApiError('HTTP_' + res.status, 'could not load repo defaults')
  return res.json()
}

/** POST the chosen files + tunables; fields left null fall back to the
 *  repo defaults on the server. */
export async function runRecon(files: UploadFiles, options: TunableOptions): Promise<ReconResponse> {
  const form = new FormData()
  for (const field of ['statement', 'bills', 'rnote', 'crn'] as const) {
    const f = files[field]
    if (f) form.append(field, f)
  }
  for (const [k, v] of Object.entries(options)) form.append(k, String(v))

  let res: Response
  try {
    res = await fetch('/api/runs', { method: 'POST', body: form })
  } catch {
    throw new ApiError('NETWORK', 'Could not reach the backend. Is uvicorn running on port 8000?')
  }

  if (!res.ok) {
    let code = 'HTTP_' + res.status
    let detail = res.statusText
    try {
      const body = await res.json()
      // our routes wrap errors as {detail: {error, detail}}; FastAPI's own
      // validation errors are {detail: [{msg, loc}, ...]}
      if (Array.isArray(body.detail)) {
        code = 'INVALID_INPUT'
        detail = body.detail
          .map((d: { loc?: (string | number)[]; msg?: string }) =>
            `${(d.loc ?? []).slice(1).join('.')}: ${d.msg}`)
          .join('; ')
      } else if (body.detail && typeof body.detail === 'object') {
        code = body.detail.error ?? code
        detail = body.detail.detail ?? detail
      } else if (typeof body.detail === 'string') {
        detail = body.detail
      }
    } catch {
      /* non-JSON error body; keep statusText */
    }
    throw new ApiError(code, detail)
  }
  return res.json()
}

export function workbookUrl(runId: string): string {
  return `/api/runs/${runId}/workbook`
}

/** One source frame of a completed run, loaded lazily per tab. */
export async function fetchFrame(runId: string, name: FrameName): Promise<{ count: number; rows: Row[] }> {
  const res = await fetch(`/api/runs/${runId}/frames/${name}`)
  if (!res.ok) throw new ApiError('HTTP_' + res.status, `could not load frame ${name}`)
  return res.json()
}
