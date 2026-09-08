import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Download } from 'lucide-react'
import {
  fetchCustomers, fetchLedger, fetchRun, fetchRuns, reconcileFromGold, workbookUrl,
} from './api'
import {
  ApiError,
  type CustomerInfo, type FrameName, type GoldFrameName, type IngestResponse,
  type LedgerViewData, type ReconResponse, type RunListItem, type RunMode,
} from './types'
import { amountsFromRows, combinedRows, countsFromRows,
         type SelectedRun } from './combineRuns'
import { ArchitectureView } from './components/ArchitectureView'
import { ARReconciliationView } from './components/ARReconciliationView'
import { AuditTrailView } from './components/AuditTrailView'
import { CommandCenter } from './components/CommandCenter'
import { ErrorBanner } from './components/ErrorBanner'
import { ExceptionQueue } from './components/ExceptionQueue'
import { GoldTable } from './components/GoldTable'
import { IngestForm } from './components/IngestForm'
import { LedgerView } from './components/LedgerView'
import { MatchedTable } from './components/MatchedTable'
import { ReconcileForm } from './components/ReconcileForm'
import { RunPicker, runLabel } from './components/RunPicker'
import { Sidebar, type View } from './components/Sidebar'
import {
  type DataPage, type DataScope, dataPageOf, readScopePref, scopeOf, viewForScope,
  writeScopePref,
} from './dataPages'
import { SourceTable } from './components/SourceTable'
import { SummaryDashboard } from './components/SummaryDashboard'

const FRAME_VIEWS: FrameName[] = ['bank', 'bills', 'bills_enriched', 'recoveries']

const GOLD_VIEWS: Record<string, GoldFrameName> = {
  gold_bank: 'bank',
  gold_bills: 'bills',
  gold_recoveries: 'recoveries',
  gold_lineage: 'lineage',
}

const VIEW_TITLES: Record<
  Exclude<View, 'command' | 'ingest' | 'reconcile' | 'ledger' | 'ar' | 'audit'
    | 'architecture' | 'gold_bank' | 'gold_bills' | 'gold_recoveries'
    | 'gold_lineage'>,
  string
> = {
  summary: 'Summary',
  matched: 'Matched',
  exceptions: 'Exception queue',
  bank: 'Bank Transactions',
  bills: 'Bills',
  bills_enriched: 'Bills',
  recoveries: 'Recoveries',
}

const GOLD_TITLES: Record<GoldFrameName, string> = {
  bank: 'Bank Transactions',
  bills: 'Bills',
  recoveries: 'Recoveries',
  lineage: 'Lineage docs',
}

const CUSTOMER_KEY = 'recon.customer'
const FALLBACK_CUSTOMERS: CustomerInfo[] = [
  { key: 'default', name: 'Default', sources: {} },
]

// views whose ROWS are selection-filtered/combined across runs
const FILTERED_VIEWS = new Set<View>(['summary', 'matched', 'exceptions'])
// views that carry the run picker — the Data pages' run scope included
// (their frames render the deduped union of the whole selection)
const PICKER_VIEWS = new Set<View>([...FILTERED_VIEWS, ...FRAME_VIEWS])

// loaded run payloads, kept across selections (payloads are immutable)
const payloadCache = new Map<string, ReconResponse>()

const VALID_VIEWS = new Set<View>([
  'command', 'ingest', 'reconcile', 'ledger', 'ar', 'audit', 'architecture',
  'summary', 'matched', 'exceptions', 'bank', 'bills', 'bills_enriched',
  'recoveries', 'gold_bank', 'gold_bills', 'gold_recoveries', 'gold_lineage',
])

/** The URL hash is the whole navigation state:
 *    `#view=<view>&run=<id>`            one run (a permalink to evidence)
 *    `#view=<view>&runs=incremental`    a named bulk selection (also
 *                                       `snapshot` / `all`) — re-resolved
 *                                       against the run list on load, so
 *                                       "all incremental" stays ALL of
 *                                       them as new runs land
 *    `#view=<view>&runs=<id>,<id>,...`  a hand-picked subset
 *  Refreshing anywhere reopens the SAME view and the SAME selection.
 *  Legacy `#run=<id>` links (no view) still open the run's Summary. */
function parseHash(): { view: View | null; run: string | null; runs: string | null } {
  const params = new URLSearchParams(window.location.hash.slice(1))
  const v = params.get('view')
  return {
    view: v && VALID_VIEWS.has(v as View) ? (v as View) : null,
    run: params.get('run'),
    runs: params.get('runs'),
  }
}

/** `runs=` token -> run ids, against the current run list. */
function resolveRunsParam(runs: string, list: RunListItem[]): string[] {
  if (runs === 'all') return list.map((r) => r.run_id)
  if (runs === 'incremental' || runs === 'snapshot') {
    return list.filter((r) => r.mode === runs).map((r) => r.run_id)
  }
  return runs.split(',').filter(Boolean)
}

/** Selection -> the hash's run part. Mirrors RunPicker's summary logic:
 *  a selection that exactly equals a mode (or everything) gets its NAME,
 *  not 15 uuids. Returns null when a multi selection can't be named yet
 *  (run list still loading) so the sync effect skips that write. */
function selectionParam(selection: string[], runList: RunListItem[]): string | null {
  if (selection.length === 0) return ''
  if (selection.length === 1) return `&run=${selection[0]}`
  if (runList.length === 0) return null
  const sel = new Set(selection)
  if (selection.length === runList.length
      && runList.every((r) => sel.has(r.run_id))) return '&runs=all'
  for (const mode of ['incremental', 'snapshot'] as const) {
    const ids = runList.filter((r) => r.mode === mode).map((r) => r.run_id)
    if (ids.length === selection.length && ids.every((id) => sel.has(id))) {
      return `&runs=${mode}`
    }
  }
  return `&runs=${selection.join(',')}`
}

const sameIds = (a: string[], b: string[]) =>
  a.length === b.length && a.every((id) => b.includes(id))

const PAGE_TITLES: Record<string, string> = {
  command: 'Command Center',
  ingest: 'Ingest documents',
  reconcile: 'Initiate Reconciliation',
  ledger: 'Analyst queue',
  ar: 'AR Reconciliation',
  audit: 'Audit trail',
  architecture: 'Architecture',
}

export default function App() {
  const [running, setRunning] = useState(false)
  const [restoring, setRestoring] = useState(false)
  const [view, setView] = useState<View>(() => {
    const { view: hv, run } = parseHash()
    // legacy `#run=` links (no view) keep opening the run's Summary
    return hv ?? (run ? 'summary' : 'command')
  })
  const [error, setError] = useState<ApiError | null>(null)
  const [customers, setCustomers] = useState<CustomerInfo[]>(FALLBACK_CUSTOMERS)
  const [customerId, setCustomerIdState] = useState<string>(
    () => localStorage.getItem(CUSTOMER_KEY) ?? 'default',
  )
  // which scope a Data-group sidebar click opens (Current | As of run);
  // the ACTIVE scope is always derived from the view itself (scopeOf)
  const [dataScope, setDataScope] = useState<DataScope>(readScopePref)
  const setScope = (page: DataPage, scope: DataScope) => {
    setDataScope(scope)
    writeScopePref(scope)
    setView(viewForScope(page, scope))
  }
  // bumped after every successful ingest so gold-fed dropdowns/lists refetch
  const [ingestEpoch, setIngestEpoch] = useState(0)
  // bumped after every ledger decision taken OUTSIDE the Analyst queue
  // (the Exception queue's MatchDecision) so every ledger-fed view —
  // Command Center tiles, AR, Audit trail, the overlay below — refetches
  const [ledgerEpoch, setLedgerEpoch] = useState(0)
  // live match_ledger state overlaid on the frozen Exception queue frame:
  // fetched only while that view is open, keyed by the same epochs
  const [ledger, setLedger] = useState<LedgerViewData | null>(null)
  // run filter: succeeded runs for the picker + the loaded selection
  const [runList, setRunList] = useState<RunListItem[]>([])
  const [selectedRuns, setSelectedRuns] = useState<SelectedRun[] | null>(null)
  // match_ledger id the Analyst queue should highlight (set by the
  // Exception queue's "Decide in Analyst queue" link)
  const [ledgerFocus, setLedgerFocus] = useState<string | null>(null)

  const primary = selectedRuns?.[0]?.payload ?? null
  const selection = selectedRuns?.map((r) => r.runId) ?? []
  const multi = selection.length > 1

  const refreshRunList = useCallback(() => {
    fetchRuns(customerId)
      .then((rs) => setRunList(rs.filter((r) => r.status === 'succeeded')))
      .catch(() => setRunList([]))
  }, [customerId])

  useEffect(refreshRunList, [refreshRunList, ingestEpoch])

  useEffect(() => {
    if (view !== 'exceptions') return
    let live = true
    fetchLedger(customerId)
      .then((d) => { if (live) setLedger(d) })
      .catch(() => { if (live) setLedger(null) })
    return () => { live = false }
  }, [view, customerId, ingestEpoch, ledgerEpoch])

  const setCustomerId = (key: string) => {
    if (key !== customerId) {
      // a selection belongs to one customer's run history; the hash-sync
      // effect drops the run part once the selection clears. The run list
      // is cleared too, so the auto-load below can never pick the OLD
      // customer's latest run while the new list is still in flight.
      setSelectedRuns(null)
      setRunList([])
    }
    setCustomerIdState(key)
    localStorage.setItem(CUSTOMER_KEY, key)
  }

  const labelFor = useCallback(
    (runId: string) => {
      const item = runList.find((r) => r.run_id === runId)
      return item ? runLabel(item) : runId.slice(0, 8)
    },
    [runList],
  )

  /** Load (from cache or API) and activate a selection, newest first. */
  const applySelection = useCallback(
    async (ids: string[]) => {
      if (!ids.length) return
      setRestoring(true)
      setError(null)
      try {
        const payloads = await Promise.all(
          ids.map(async (id) => {
            const hit = payloadCache.get(id)
            if (hit) return hit
            const p = await fetchRun(id)
            payloadCache.set(id, p)
            return p
          }),
        )
        setSelectedRuns(ids.map((id, i) => ({
          runId: id, label: labelFor(id), payload: payloads[i],
        })))
      } catch (e) {
        setError(e instanceof ApiError ? e : new ApiError('UNKNOWN', String(e)))
      } finally {
        setRestoring(false)
      }
    },
    [labelFor],
  )

  // true while a hash-named selection is still being restored, so the
  // latest-run auto-load below does not race it
  const hashRestore = useRef(!!(parseHash().run || parseHash().runs))

  useEffect(() => {
    fetchCustomers().then((cs) => cs.length && setCustomers(cs)).catch(() => {})
    // restore the selection named in the hash WITHOUT changing the view —
    // the view was already read from the hash, so a refresh stays put
    const { run, runs } = parseHash()
    if (runs) {
      void (async () => {
        try {
          const list = (await fetchRuns(customerId))
            .filter((r) => r.status === 'succeeded')
          const ids = resolveRunsParam(runs, list)
          if (ids.length) await applySelection(ids)
        } catch { /* run list unavailable; empty state guides the user */ }
        hashRestore.current = false
      })()
    } else if (run) {
      void (async () => {
        await applySelection([run])
        hashRestore.current = false
        if (!payloadCache.has(run)) setView('reconcile')  // restore failed
      })()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // A result view opened with nothing loaded shows the LATEST run rather
  // than a dead end — the picker in its header then switches runs. One
  // attempt per run id: a run that fails to load must not be retried
  // forever (the empty state + error banner take over).
  const autoLoadTried = useRef<string | null>(null)
  useEffect(() => {
    if (selectedRuns || restoring || hashRestore.current) return
    if (!FILTERED_VIEWS.has(view) && !FRAME_VIEWS.includes(view as FrameName)) return
    const latest = runList[0]?.run_id
    if (!latest || autoLoadTried.current === latest) return
    autoLoadTried.current = latest
    void applySelection([latest])
  }, [view, selectedRuns, restoring, runList, applySelection])

  // hash <- state: the single writer. Guarded so applying a hash the
  // user navigated to (back/forward) never re-writes an identical value;
  // a null param (multi selection before the run list loads) skips the
  // write rather than spelling out ids a named token will replace.
  const runParam = selectionParam(selection, runList)
  useEffect(() => {
    if (runParam === null) return
    const target = `view=${view}${runParam}`
    if (window.location.hash.slice(1) !== target) {
      window.location.hash = target
    }
  }, [view, runParam])

  // state <- hash: browser back/forward (and hand-edited links) navigate
  useEffect(() => {
    const onHashChange = () => {
      const { view: hv, run, runs } = parseHash()
      if (hv && hv !== view) setView(hv)
      const want = runs ? resolveRunsParam(runs, runList) : run ? [run] : null
      if (want && want.length && !sameIds(want, selection)) {
        void applySelection(want)
      }
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, selection.join(','), runList, applySelection])

  // browser-tab title follows the active view
  useEffect(() => {
    const goldName = GOLD_VIEWS[view]
    const title = PAGE_TITLES[view]
      ?? (goldName ? GOLD_TITLES[goldName]
          : VIEW_TITLES[view as keyof typeof VIEW_TITLES])
    document.title = title ? `${title} — Recon Engine` : 'Recon Engine'
  }, [view])

  const onIngested = (_r: IngestResponse) => {
    setIngestEpoch((n) => n + 1)
  }

  const onReconcile = async (statementBronzeId: number, mode: RunMode) => {
    setRunning(true)
    setError(null)
    try {
      // tunables deliberately omitted: the customer's saved matching
      // config governs the run (API-side merge)
      const res = await reconcileFromGold({
        customer_id: customerId,
        statement_bronze_id: statementBronzeId,
        mode,
      })
      payloadCache.set(res.run_id, res)
      refreshRunList()
      setSelectedRuns([{ runId: res.run_id, label: 'this run', payload: res }])
      setView('summary')
    } catch (e) {
      // deliberately keep any loaded result: a 409 RUN_IN_PROGRESS or a
      // failed re-run should not wipe what is already on screen
      setError(e instanceof ApiError ? e : new ApiError('UNKNOWN', String(e)))
    } finally {
      setRunning(false)
    }
  }

  // labels resolve against the freshest run list (the just-reconciled run
  // gets its timestamped label as soon as the list refresh lands)
  useEffect(() => {
    setSelectedRuns((prev) =>
      prev ? prev.map((r) => ({ ...r, label: labelFor(r.runId) })) : prev)
  }, [labelFor])

  const goldFrame = GOLD_VIEWS[view]
  const dataPage = dataPageOf(view)
  const activeScope = scopeOf(view)

  // Head row shared by BOTH scopes of a Data page (and by the no-run
  // empty state in run scope, so switching back is one click): title,
  // the scope switch, then the scope's own context — run stamp + picker
  // in run scope, the customer stamp in current scope.
  const dataHead = (page: DataPage, withPicker: boolean) => (
    <div className="result-head">
      <h2 className="page-title">{page.label}</h2>
      <span className="file-note">
        {page.run ? (
          <span className="seg seg-scope">
            <button className={activeScope === 'current' ? 'on' : ''}
                    onClick={() => setScope(page, 'current')}>Current</button>
            <button className={activeScope === 'run' ? 'on' : ''}
                    onClick={() => setScope(page, 'run')}>As of run</button>
          </span>
        ) : (
          <span className="chip-note">current data only — runs keep no lineage snapshot</span>
        )}
        {activeScope === 'run' && page.runTrail && page.run && (
          <span className="seg seg-scope">
            <button className={view === page.run ? 'on' : ''}
                    onClick={() => setView(page.run as View)}>Table</button>
            <button className={view === page.runTrail ? 'on' : ''}
                    onClick={() => setView(page.runTrail as View)}>With lineage trail</button>
          </span>
        )}
        {activeScope === 'current' && <span className="stamp head-stamp">customer: {customerId}</span>}
        {activeScope === 'run' && primary?.meta.mode && !multi && (
          <span className="stamp head-stamp">{primary.meta.mode}</span>
        )}
        {activeScope === 'run' && restoring && <span className="chip-note">loading runs…</span>}
        {activeScope === 'run' && withPicker && (
          <RunPicker runs={runList} selection={selection}
                     onChange={(ids) => void applySelection(ids)} />
        )}
      </span>
    </div>
  )

  const showResult =
    primary && (view === 'summary' || view === 'matched' || view === 'exceptions'
      || FRAME_VIEWS.includes(view as FrameName))

  // combined rows for the whole selection (exceptions deduped across
  // runs: an open exception re-reported by several incremental runs
  // shows once, in its latest state)
  const matchedRows = useMemo(
    () => (selectedRuns ? combinedRows(selectedRuns, 'matched') : []),
    [selectedRuns])
  const exceptionRows = useMemo(
    () => (selectedRuns ? combinedRows(selectedRuns, 'exceptions') : []),
    [selectedRuns])

  // Sidebar badges reflect the whole selection AND agree with the tables;
  // everything run-specific (workbook, filenames) stays primary. Frame
  // badges disappear on multi selection: the Run data tabs show a lazily
  // computed deduped union, so no fixed count is known up front — and
  // summing 28 copies of one statement would read as duplicated data.
  const displayResult = primary && selectedRuns
    ? (multi
        ? { ...primary,
            meta: { ...primary.meta,
                    counts: {
                      ...countsFromRows(
                        matchedRows, exceptionRows,
                        selectedRuns.map((r) => r.payload.meta)),
                      bank_txns: undefined,
                      bills: undefined,
                      bills_grouped: undefined,
                      recoveries: undefined,
                    } } }
        : primary)
    : null

  // Why a result table is empty, in the run's own terms — composed here
  // because only App holds the mode + (selection-aggregated) counts.
  const runScope = multi && selectedRuns
    ? `across the ${selectedRuns.length} selected runs`
    : `in this ${primary?.meta.mode ?? ''} run`.replace('  ', ' ')
  const counts = displayResult?.meta.counts
  const openExceptions = counts
    ? counts.bank_only + counts.bill_only + (counts.match_review ?? 0) : 0
  const matchedEmptyNote = (
    <div className="frame-note table-empty-note">
      <p><strong>No matched reconciliations {runScope}.</strong></p>
      {primary?.meta.mode === 'incremental' && (
        <p>
          Credits and bills locked by an earlier run never re-enter the matching pool, so a
          statement that was already reconciled matches nothing new — that is expected, not a
          failure.
        </p>
      )}
      {counts && openExceptions > 0 && (
        <p>
          {counts.bank_only} bank-only and {counts.bill_only} bill-only exception
          {counts.bank_only + counts.bill_only === 1 ? '' : 's'} went to the{' '}
          <button className="link-btn" onClick={() => setView('exceptions')}>Exception queue</button>.
        </p>
      )}
    </div>
  )
  const exceptionsEmptyNote = (
    <div className="frame-note table-empty-note">
      <p><strong>No exceptions {runScope}.</strong></p>
      <p>
        Every credit on the statement found its bill and every advised bill found its credit.
        {counts && counts.matched > 0 && (
          <>
            {' '}See the {counts.matched} matched row{counts.matched === 1 ? '' : 's'} under{' '}
            <button className="link-btn" onClick={() => setView('matched')}>Matched</button>.
          </>
        )}
      </p>
    </div>
  )

  return (
    <div className="layout">
      <Sidebar view={view} onNavigate={setView} result={displayResult}
               dataScope={dataScope} />

      <main className="content">
        {/* Always mounted, never destroyed by a view switch — unlike every
            other view below (deliberately remounted via key={view} for the
            entrance animation), the Ingest form holds file selections and
            per-slot state that a browser can never restore once lost, so
            navigating away and back must not unmount it. Visibility is
            CSS-only (`hidden`), not conditional rendering. */}
        <div hidden={view !== 'ingest'}>
          <IngestForm
            customers={customers}
            customerId={customerId}
            onCustomerChange={setCustomerId}
            onCustomersChanged={() =>
              fetchCustomers().then((cs) => cs.length && setCustomers(cs)).catch(() => {})}
            onIngested={onIngested}
          />
          {restoring && <p className="footer-note">Restoring run…</p>}
        </div>

        {/* keyed on the view so every navigation replays the entrance */}
        <div key={dataPageOf(view)?.id ?? view} className="view-enter">
        {view === 'command' && (
          <CommandCenter
            customers={customers}
            customerId={customerId}
            onCustomerChange={setCustomerId}
            onNavigate={setView}
            refreshKey={ingestEpoch + ledgerEpoch + (selectedRuns?.length ?? 0)}
          />
        )}

        {view === 'reconcile' && (
          <>
            <ReconcileForm
              running={running}
              customers={customers}
              customerId={customerId}
              onCustomerChange={setCustomerId}
              onReconcile={onReconcile}
              onGoToIngest={() => setView('ingest')}
              refreshKey={ingestEpoch}
            />
            {error && <ErrorBanner error={error} />}
            {primary && (
              <p className="footer-note">
                A run is loaded — pick a view from the left, or run again.
              </p>
            )}
          </>
        )}

        {view === 'ledger' && (
          <LedgerView
            customerId={customerId}
            focusId={ledgerFocus}
            onFocusHandled={() => setLedgerFocus(null)}
          />
        )}

        {view === 'ar' && (
          <ARReconciliationView
            customerId={customerId}
            refreshKey={ingestEpoch + ledgerEpoch + (selectedRuns?.length ?? 0)}
            onOpenInQueue={(id) => {
              setLedgerFocus(id)
              setView('ledger')
            }}
          />
        )}

        {view === 'architecture' && (
          <ArchitectureView customerId={customerId} onNavigate={setView} />
        )}

        {view === 'audit' && (
          <AuditTrailView
            customers={customers}
            customerId={customerId}
            onCustomerChange={setCustomerId}
            refreshKey={ingestEpoch + ledgerEpoch + (selectedRuns?.length ?? 0)}
            onOpenMatch={(id) => {
              setLedgerFocus(id)
              setView('ledger')
            }}
          />
        )}

        {goldFrame && dataPage && (
          <>
            {dataHead(dataPage, false)}
            <div className="view-card">
              <GoldTable key={`${customerId}:${goldFrame}:${ingestEpoch}`}
                         customerId={customerId} frame={goldFrame} />
            </div>
          </>
        )}

        {/* a result view with no run loaded (restore failed, customer
            switched, or a stale link): guide instead of a blank page */}
        {!showResult
          && (FILTERED_VIEWS.has(view) || FRAME_VIEWS.includes(view as FrameName))
          && (restoring ? (
            <>
              {dataPage && dataHead(dataPage, false)}
              <p className="footer-note">Restoring run…</p>
            </>
          ) : (
            <>
            {dataPage && dataHead(dataPage, false)}
            <div className="view-card empty-state">
              <h3>{runList.length ? 'No run loaded' : 'No runs yet'}</h3>
              <p>
                {runList.length
                  ? 'This view shows a reconciliation result. Pick a past run, or run a new one.'
                  : 'This view shows a reconciliation result. Ingest documents, then initiate a reconciliation for this customer.'}
              </p>
              <div className="empty-state-actions">
                <button className="btn-run" onClick={() => setView('reconcile')}>
                  Initiate reconciliation
                </button>
                {runList.length > 0 && (
                  <RunPicker runs={runList} selection={selection}
                             onChange={(ids) => void applySelection(ids)} />
                )}
              </div>
              {error && <ErrorBanner error={error} />}
            </div>
            </>
          ))}

        {showResult && primary && selectedRuns && (
          <>
            {dataPage ? dataHead(dataPage, true) : (
            <div className="result-head">
              <h2 className="page-title">{VIEW_TITLES[view as keyof typeof VIEW_TITLES]}</h2>
              <span className="file-note">
                {FILTERED_VIEWS.has(view) && (
                  <a className="btn-download btn-ic"
                     href={workbookUrl(primary.run_id)} download
                     title={'Full workbook for this run — Summary, Matched, '
                       + 'Exception Queue, Recovery Detail'
                       + (multi ? ' (primary run of the selection)' : '')}>
                    <Download size={13} strokeWidth={1.75} /> Export (.xlsx)
                  </a>
                )}
                {primary.meta.mode && !multi && (
                  <span className="stamp head-stamp">{primary.meta.mode}</span>
                )}
                {primary.meta.customer && primary.meta.customer !== 'default' && (
                  <span className="stamp head-stamp">{primary.meta.customer}</span>
                )}
                {restoring && <span className="chip-note">loading runs…</span>}
                {PICKER_VIEWS.has(view) && (
                  <RunPicker runs={runList} selection={selection}
                             onChange={(ids) => void applySelection(ids)} />
                )}
              </span>
            </div>
            )}

            {error && PICKER_VIEWS.has(view) && <ErrorBanner error={error} />}

            <div className="view-card">
              {view === 'summary' && (
                <SummaryDashboard
                  runs={selectedRuns.map((r) => ({
                    runId: r.runId, label: r.label,
                    summary: r.payload.summary, meta: r.payload.meta,
                  }))}
                  aggregate={multi && displayResult
                    ? { counts: displayResult.meta.counts,
                        amounts: amountsFromRows(matchedRows, exceptionRows) }
                    : undefined}
                />
              )}
              {view === 'matched' && (
                <MatchedTable rows={matchedRows} emptyNote={matchedEmptyNote} />
              )}
              {view === 'exceptions' && (
                <ExceptionQueue
                  rows={exceptionRows}
                  emptyNote={exceptionsEmptyNote}
                  primaryRunId={primary?.run_id ?? null}
                  ledger={ledger}
                  customerId={customerId}
                  onLedgerChanged={() => setLedgerEpoch((n) => n + 1)}
                  onOpenInQueue={(id) => {
                    setLedgerFocus(id)
                    setView('ledger')
                  }}
                />
              )}
              {FRAME_VIEWS.includes(view as FrameName) && (
                <SourceTable
                  runs={selectedRuns.map((r) => ({ runId: r.runId, label: r.label }))}
                  name={view as FrameName}
                />
              )}
            </div>

            {view === 'exceptions' && (
              <p className="footer-note">
                Both sides are sources of truth: BANK ONLY rows carry no bill fields and BILL ONLY rows
                no bank fields by design. A credit whose best pairing was claimed by another credit is
                not allowed to settle for a worse one — it falls to this queue instead, because a
                missing match you can investigate beats a wrong match you cannot see.
              </p>
            )}
          </>
        )}
        </div>
      </main>
    </div>
  )
}
