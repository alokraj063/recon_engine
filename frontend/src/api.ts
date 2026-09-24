import {
  ApiError,
  type AdapterRegistry,
  type ArView,
  type AuthUser,
  type AuditEventRow,
  type CustomerConfig,
  type ZoneDirectory,
  type ZoneEntry,
  type CustomerInfo,
  type CustomerRules,
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
  type ManualMatchResult,
  type OperatingUnits,
  type OverviewFilters,
  type RunListItem,
} from './types'
import { normalizeRows, normalizeRun } from './normalizeLegacy'

/** Files per fixed slot — a slot may carry several, processed by the
 *  server in the order attached. */
export interface UploadFiles {
  statement: File[]
  bills: File[]
  rnote: File[]
  crn: File[]
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

/** Every route but /api/auth/* and /api/health sits behind the session
 *  cookie, so a 401 from ANY of the ~30 calls in this file means one
 *  thing: the session is over (expired, signed out in another tab, or the
 *  account was deactivated). Rather than teach every caller to handle
 *  that, parseApiError announces it once and the gate in auth.tsx listens.
 *
 *  A failed sign-in is excluded — that 401 is an answer to a question the
 *  login form asked, not a session ending under someone's feet. */
const SESSION_ENDED = 'recon:session-ended'

export function onSessionEnded(handler: () => void): () => void {
  window.addEventListener(SESSION_ENDED, handler)
  return () => window.removeEventListener(SESSION_ENDED, handler)
}

function noteUnauthorized(res: Response) {
  if (res.status === 401 && !res.url.includes('/api/auth/login')) {
    window.dispatchEvent(new Event(SESSION_ENDED))
  }
}

async function getJson<T>(url: string): Promise<T> {
  let res: Response
  try {
    res = await fetch(url)
  } catch {
    throw new ApiError('NETWORK', 'Could not reach the backend. Is uvicorn running on port 8000?')
  }
  if (!res.ok) { noteUnauthorized(res); throw await parseApiError(res) }
  return res.json()
}

/** POST the chosen files for ingestion (bronze -> silver -> gold). The
 *  ingestion is exactly these files: a slot with no file is not part of
 *  it, and nothing is ever substituted server-side. */
export async function ingestFiles(
  files: UploadFiles,
  customerId: string,
  extraFiles?: Record<string, File[]>,
): Promise<IngestResponse> {
  const form = new FormData()
  // the same field repeated = several files in one slot, in this order
  for (const field of ['statement', 'bills', 'rnote', 'crn'] as const) {
    for (const f of files[field]) form.append(field, f)
  }
  // extra lineage slots upload under their slot key (source_type)
  for (const [slot, fs] of Object.entries(extraFiles ?? {})) {
    for (const f of fs) form.append(slot, f)
  }
  form.append('customer_id', customerId)

  let res: Response
  try {
    res = await fetch('/api/ingest', { method: 'POST', body: form })
  } catch {
    throw new ApiError('NETWORK', 'Could not reach the backend. Is uvicorn running on port 8000?')
  }
  if (!res.ok) { noteUnauthorized(res); throw await parseApiError(res) }
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

/** GET /api/ledger/workbook — the durable ledger as Excel (Matches incl.
 *  manual, Manual_Matches, Exceptions with how each was resolved). */
export function ledgerWorkbookUrl(customerId: string): string {
  return `/api/ledger/workbook?customer_id=${encodeURIComponent(customerId)}`
}

export async function fetchLedger(customerId: string): Promise<LedgerViewData> {
  return getJson(`/api/ledger?customer_id=${encodeURIComponent(customerId)}`)
}

/** Command Center aggregates (gold pool, ledger state, open exposure),
 *  optionally narrowed to a date window / operating units (item 2.1). */
export async function fetchOverview(customerId: string, filters?: OverviewFilters): Promise<Overview> {
  const q = new URLSearchParams({ customer_id: customerId })
  if (filters?.from) q.set('from', filters.from)
  if (filters?.to) q.set('to', filters.to)
  for (const u of filters?.operating_units ?? []) q.append('operating_unit', u)
  return getJson(`/api/overview?${q.toString()}`)
}

/** The customer's operating units (from its gold bills). */
export async function fetchOperatingUnits(customerId: string): Promise<OperatingUnits> {
  return getJson(`/api/customers/${encodeURIComponent(customerId)}/operating-units`)
}

/** The customer's audit_log event stream, newest first. */
export async function fetchAudit(customerId: string, limit?: number): Promise<AuditEventRow[]> {
  const q = new URLSearchParams({ customer_id: customerId })
  if (limit) q.set('limit', String(limit))
  return getJson(`/api/audit?${q.toString()}`)
}

/** AR working set: settled / in-review / outstanding bills + aging. */
export async function fetchAr(customerId: string): Promise<ArView> {
  return getJson(`/api/ar?customer_id=${encodeURIComponent(customerId)}`)
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
  if (!res.ok) { noteUnauthorized(res); throw await parseApiError(res) }
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

/** Adapter per slot; a null value REMOVES a lineage slot, a new
 *  lineage_<key> name adds one. */
export async function saveCustomerSources(
  key: string,
  sources: Record<string, string | null>,
): Promise<{ key: string; sources: Record<string, string> }> {
  return sendJson('PUT', `/api/customers/${encodeURIComponent(key)}/sources`, { sources })
}

export async function fetchZoneDirectory(key: string): Promise<ZoneDirectory> {
  return getJson(`/api/customers/${encodeURIComponent(key)}/zones`)
}

/** null resets the customer to the built-in directory */
export async function saveZoneDirectory(key: string, zones: ZoneEntry[] | null): Promise<ZoneDirectory> {
  return sendJson('PUT', `/api/customers/${encodeURIComponent(key)}/zones`, { zones })
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
  id: string, goldBillId?: string, note?: string,
): Promise<{ id: string; status: string; locked_by: string | null }> {
  return postJson(`/api/matches/${id}/accept`,
                  { gold_bill_id: goldBillId ?? null, note: note || null })
}

/** Reject an OPEN ledger match, releasing both sides back to the pool. */
export async function rejectMatch(id: string, note?: string): Promise<{ id: string; status: string }> {
  return postJson(`/api/matches/${id}/reject`, { note: note || null })
}

/** Reopen a LOCKED match (USER or AUTO_HIGH) — back to OPEN for review. */
export async function unlockMatch(
  id: string, note?: string,
): Promise<{ id: string; status: string; locked_by: string | null }> {
  return postJson(`/api/matches/${id}/unlock`, { note: note || null })
}

/** Bank Transactions' Source edit — the same decision as the queue's
 *  Approve (NON_IREPS) / Reject (IREPS) / Undo (null), made from the
 *  credit. 409 CREDIT_MATCHED while a live match holds the credit. */
export async function setCreditSource(
  customerId: string, goldBankTxnId: string, source: 'IREPS' | 'NON_IREPS' | null,
): Promise<{ gold_bank_txn_id: string; source_decision: string | null;
            exception_id: string | null; exception_status: string | null }> {
  return sendJson('PUT', `/api/bank/${goldBankTxnId}/source`,
                  { customer_id: customerId, source })
}

/** Approve a whole group of non-IREPS receipts in one call — rows that
 *  are no longer OPEN are skipped, and the response counts both. */
export async function approveNonIrepsBulk(
  customerId: string, exceptionIds: string[],
): Promise<{ approved: number; requested: number; skipped: number }> {
  return postJson('/api/exceptions/non-ireps/approve-bulk',
                  { customer_id: customerId, exception_ids: exceptionIds })
}

export type NonIrepsDecision = 'approve' | 'reject' | 'undo'

/** The Non-IREPS receipts tab: approve (a non-IREPS receipt — closed,
 *  never matched), reject (IREPS money — back to the IREPS queue and into
 *  matching) or undo either. 409 when the row is not in that state. */
export async function decideNonIreps(
  exceptionId: string, decision: NonIrepsDecision, note?: string,
): Promise<{ id: string; status: string; resolved_by: string | null;
            source_decision: string | null }> {
  return postJson(`/api/exceptions/${exceptionId}/non-ireps/${decision}`,
                  { note: note || null })
}

/** Pair an open credit with open bill(s) by hand. No tolerance applies;
 *  the response carries the variance. 409 ALREADY_CONSUMED when a side is
 *  held by another match. The row is LOCKED by USER, confidence MANUAL. */
export async function createManualMatch(
  customerId: string, goldBankTxnId: string, goldBillIds: string[], note?: string,
): Promise<ManualMatchResult> {
  return postJson('/api/matches/manual', {
    customer_id: customerId, gold_bank_txn_id: goldBankTxnId,
    gold_bill_ids: goldBillIds, note: note ?? null,
  })
}

/** Undo a REJECTED match — back to OPEN, re-claiming its credit and bills.
 *  409 MATCH_CONFLICT if a later run already claimed either side. */
export async function reopenMatch(
  id: string, note?: string,
): Promise<{ id: string; status: string; locked_by: string | null }> {
  return postJson(`/api/matches/${id}/reopen`, { note: note || null })
}

// --- authentication ---------------------------------------------------
// The cookie does all the work: these calls set it, clear it and ask who
// it belongs to. Nothing here handles a token, because a same-origin
// fetch() attaches the cookie by itself.

/** Who is signed in. 401 is the normal answer for a stranger. */
export async function fetchMe(): Promise<AuthUser> {
  return getJson('/api/auth/me')
}

export async function signIn(email: string, password: string): Promise<AuthUser> {
  return postJson('/api/auth/login', { email, password })
}

export async function signOut(): Promise<{ status: string }> {
  return postJson('/api/auth/logout')
}
