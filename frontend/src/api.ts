import {
  ApiError,
  type AdapterRegistry,
  type AuditEventRow,
  type CustomerConfig,
  type CustomerInfo,
  type CustomerRules,
  type Defaults,
  type FrameName,
  type GoldFileInfo,
  type GoldFrameName,
  type GoldSchema,
  type IngestionListItem,
  type IngestResponse,
  type LedgerViewData,
  type Overview,
  type ReconcileParams,
  type ReconResponse,
  type Row,
  type RunListItem,
} from './types'
import { normalizeRows, normalizeRun } from './normalizeLegacy'

export interface UploadFiles {
  statement: File | null
  bills: File | null
  rnote: File | null
  crn: File | null
}

/** Turn a non-2xx response into an ApiError carrying the backend's
 *  {detail:{error,detail}} code where present. */
async function parseApiError(res: Response): Promise<ApiError> {
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
  return new ApiError(code, detail)
}

async function getJson<T>(url: string): Promise<T> {
  let res: Response
  try {
    res = await fetch(url)
  } catch {
    throw new ApiError('NETWORK', 'Could not reach the backend. Is uvicorn running on port 8000?')
  }
  if (!res.ok) throw await parseApiError(res)
  return res.json()
}

/** Which repo sample document backs each field when nothing is uploaded.
 *  Samples back the default customer only — other customers get nulls. */
export async function fetchDefaults(customerId: string): Promise<Defaults> {
  return getJson(`/api/defaults?customer_id=${encodeURIComponent(customerId)}`)
}

/** POST the chosen files for ingestion (bronze -> silver -> gold);
 *  fields left null fall back to the repo defaults on the server. */
export async function ingestFiles(
  files: UploadFiles,
  customerId: string,
  slots: string[],
): Promise<IngestResponse> {
  const form = new FormData()
  for (const field of ['statement', 'bills', 'rnote', 'crn'] as const) {
    const f = files[field]
    if (f && slots.includes(field)) form.append(field, f)
  }
  form.append('customer_id', customerId)
  form.append('slots', slots.join(','))

  let res: Response
  try {
    res = await fetch('/api/ingest', { method: 'POST', body: form })
  } catch {
    throw new ApiError('NETWORK', 'Could not reach the backend. Is uvicorn running on port 8000?')
  }
  if (!res.ok) throw await parseApiError(res)
  return res.json()
}

export function workbookUrl(runId: string): string {
  return `/api/runs/${runId}/workbook`
}

/** One source frame of a completed run, loaded lazily per tab.
 *  Pre-canonicalization runs come back under their old column names —
 *  normalized here so the display layer sees one vocabulary. */
export async function fetchFrame(runId: string, name: FrameName): Promise<{ count: number; rows: Row[] }> {
  const d = await getJson<{ count: number; rows: Row[] }>(`/api/runs/${runId}/frames/${name}`)
  return { ...d, rows: normalizeRows(d.rows) }
}

/** Rehydrate a persisted run (survives backend restarts). Old runs'
 *  frozen payloads keep their historical column names server-side;
 *  normalizeRun translates them to the canonical vocabulary on load. */
export async function fetchRun(runId: string): Promise<ReconResponse> {
  const payload = await getJson<ReconResponse>(`/api/runs/${runId}`)
  // persisted payloads carry run_id, but inject defensively
  return normalizeRun({ ...payload, run_id: payload.run_id || runId })
}

export async function fetchRuns(customerId?: string, limit = 50): Promise<RunListItem[]> {
  const qs = new URLSearchParams()
  if (customerId) qs.set('customer_id', customerId)
  qs.set('limit', String(limit))
  return getJson(`/api/runs?${qs}`)
}

export async function fetchCustomers(): Promise<CustomerInfo[]> {
  return getJson('/api/customers')
}

export async function fetchLedger(customerId: string): Promise<LedgerViewData> {
  return getJson(`/api/ledger?customer_id=${encodeURIComponent(customerId)}`)
}

/** Command Center aggregates (gold pool, ledger state, open exposure). */
export async function fetchOverview(customerId: string): Promise<Overview> {
  return getJson(`/api/overview?customer_id=${encodeURIComponent(customerId)}`)
}

/** The customer's audit_log event stream, newest first. */
export async function fetchAudit(customerId: string): Promise<AuditEventRow[]> {
  return getJson(`/api/audit?customer_id=${encodeURIComponent(customerId)}`)
}

async function sendJson<T>(method: string, url: string, body?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, {
      method,
      ...(body !== undefined
        ? { headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body) }
        : {}),
    })
  } catch {
    throw new ApiError('NETWORK', 'Could not reach the backend. Is uvicorn running on port 8000?')
  }
  if (!res.ok) throw await parseApiError(res)
  return res.json()
}

const postJson = <T,>(url: string, body?: unknown) => sendJson<T>('POST', url, body)

export async function fetchAdapters(): Promise<AdapterRegistry> {
  return getJson('/api/adapters')
}

export async function fetchGoldSchema(): Promise<GoldSchema> {
  return getJson('/api/gold/schema')
}

export async function fetchCustomerConfig(key: string): Promise<CustomerConfig> {
  return getJson(`/api/customers/${encodeURIComponent(key)}/config`)
}

export async function saveCustomerConfig(key: string, rules: CustomerRules): Promise<CustomerConfig> {
  return sendJson('PUT', `/api/customers/${encodeURIComponent(key)}/config`, rules)
}

export async function saveCustomerSources(
  key: string,
  sources: Record<string, string>,
): Promise<{ key: string; sources: Record<string, string> }> {
  return sendJson('PUT', `/api/customers/${encodeURIComponent(key)}/sources`, { sources })
}

export async function createCustomer(key: string, name: string): Promise<CustomerInfo> {
  return postJson('/api/customers', { key, name })
}

/** Run a reconciliation purely from the gold layer — no uploads. */
export async function reconcileFromGold(params: ReconcileParams): Promise<ReconResponse> {
  return postJson('/api/reconcile', params)
}

export async function fetchIngestions(customerId: string): Promise<IngestionListItem[]> {
  return getJson(`/api/ingestions?customer_id=${encodeURIComponent(customerId)}`)
}

/** Bronze files owning gold rows — feeds the statement picker and the
 *  gold tabs' ingestion filters. */
export async function fetchGoldFiles(customerId: string): Promise<GoldFileInfo[]> {
  return getJson(`/api/gold/files?customer_id=${encodeURIComponent(customerId)}`)
}

export async function fetchGoldFrame(
  customerId: string,
  frame: GoldFrameName,
  bronzeFileId?: number,
): Promise<{ name: string; count: number; total: number; rows: Row[] }> {
  const qs = new URLSearchParams({ customer_id: customerId })
  if (bronzeFileId !== undefined) qs.set('bronze_file_id', String(bronzeFileId))
  return getJson(`/api/gold/${frame}?${qs}`)
}

/** Lock an OPEN (review-confidence) ledger match. Idempotent.
 *  goldBillId overrides an ambiguous pick: the chosen candidate becomes
 *  the settled bill (must belong to the match; 400 otherwise). */
export async function acceptMatch(
  id: string, goldBillId?: string,
): Promise<{ id: string; status: string; locked_by: string | null }> {
  return postJson(`/api/matches/${id}/accept`,
                  goldBillId ? { gold_bill_id: goldBillId } : undefined)
}

/** Reject an OPEN ledger match, releasing both sides back to the pool. */
export async function rejectMatch(id: string): Promise<{ id: string; status: string }> {
  return postJson(`/api/matches/${id}/reject`)
}

/** Reopen a LOCKED match (USER or AUTO_HIGH) — back to OPEN for review. */
export async function unlockMatch(
  id: string,
): Promise<{ id: string; status: string; locked_by: string | null }> {
  return postJson(`/api/matches/${id}/unlock`)
}

/** Undo a REJECTED match — back to OPEN, re-claiming its credit and bills.
 *  409 MATCH_CONFLICT if a later run already claimed either side. */
export async function reopenMatch(
  id: string,
): Promise<{ id: string; status: string; locked_by: string | null }> {
  return postJson(`/api/matches/${id}/reopen`)
}
