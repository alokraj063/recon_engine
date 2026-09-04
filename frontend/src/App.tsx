import { useCallback, useEffect, useMemo, useState } from 'react'
import { Download } from 'lucide-react'
import { fetchCustomers, fetchRun, fetchRuns, reconcileFromGold, workbookUrl } from './api'
import {
  ApiError,
  type CustomerInfo, type FrameName, type GoldFrameName, type IngestResponse,
  type ReconResponse, type RunListItem, type RunMode,
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
  bank: 'Bank statement — as reconciled in this run',
  bills: 'Bills — as reconciled in this run',
  bills_enriched: 'Bills — enriched with lineage',
  recoveries: 'Recovery detail',
}

const GOLD_TITLES: Record<GoldFrameName, string> = {
  bank: 'Gold — bank transactions',
  bills: 'Gold — bills',
  recoveries: 'Gold — recoveries',
  lineage: 'Gold — lineage documents',
}

const CUSTOMER_KEY = 'recon.customer'
const FALLBACK_CUSTOMERS: CustomerInfo[] = [
  { key: 'default', name: 'Default', sources: {} },
]

// views whose ROWS are selection-filtered/combined across runs
const FILTERED_VIEWS = new Set<View>(['summary', 'matched', 'exceptions'])
// views that carry the run picker — Run data tabs included, but their
// frames stay single-run (always the selection's primary run)
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
  ingest: 'Ingest files',
  reconcile: 'Run reconciliation',
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
  // bumped after every successful ingest so gold-fed dropdowns/lists refetch
  const [ingestEpoch, setIngestEpoch] = useState(0)
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

  const setCustomerId = (key: string) => {
    if (key !== customerId) {
      // a selection belongs to one customer's run history; the hash-sync
      // effect drops the run part once the selection clears
      setSelectedRuns(null)
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

  const openRun = async (runId: string) => {
    await applySelection([runId])
    setView(payloadCache.has(runId) ? 'summary' : 'reconcile')
  }

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
      })()
    } else if (run) {
      void (async () => {
        await applySelection([run])
        if (!payloadCache.has(run)) setView('reconcile')  // restore failed
      })()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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

  return (
    <div className="layout">
      <Sidebar view={view} onNavigate={setView} result={displayResult} />

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
        <div key={view} className="view-enter">
        {view === 'command' && (
          <CommandCenter
            customers={customers}
            customerId={customerId}
            onCustomerChange={setCustomerId}
            onNavigate={setView}
            refreshKey={ingestEpoch + (selectedRuns?.length ?? 0)}
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
            activeRunId={primary?.run_id ?? null}
            onOpenRun={openRun}
          />
        )}

        {view === 'ar' && (
          <ARReconciliationView
            customerId={customerId}
            refreshKey={ingestEpoch + (selectedRuns?.length ?? 0)}
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
            refreshKey={ingestEpoch + (selectedRuns?.length ?? 0)}
            onOpenMatch={(id) => {
              setLedgerFocus(id)
              setView('ledger')
            }}
          />
        )}

        {goldFrame && (
          <>
            <div className="result-head">
              <h2>{GOLD_TITLES[goldFrame]}</h2>
              <span className="file-note">customer: {customerId}</span>
            </div>
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
            <p className="footer-note">Restoring run…</p>
          ) : (
            <div className="view-card empty-state">
              <h3>No run loaded</h3>
              <p>
                This view shows a reconciliation result. Run one now, or reopen
                a past run.
              </p>
              <div className="empty-state-actions">
                <button className="btn-run" onClick={() => setView('reconcile')}>
                  Run reconciliation
                </button>
                {runList.length > 0 && (
                  <RunPicker runs={runList} selection={selection}
                             onChange={(ids) => void applySelection(ids)} />
                )}
              </div>
              {error && <ErrorBanner error={error} />}
            </div>
          ))}

        {showResult && primary && selectedRuns && (
          <>
            <div className="result-head">
              <h2>{VIEW_TITLES[view as keyof typeof VIEW_TITLES]}</h2>
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

            {FRAME_VIEWS.includes(view as FrameName) && (
              <p className="head-sub">
                <span className="head-sub-files">
                  {primary.meta.filenames.statement} ✕ {primary.meta.filenames.bills}
                </span>
                {multi && (
                  <span className="chip">deduped across {selectedRuns.length} runs</span>
                )}
              </p>
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
              {view === 'matched' && <MatchedTable rows={matchedRows} />}
              {view === 'exceptions' && (
                <ExceptionQueue
                  rows={exceptionRows}
                  primaryRunId={primary?.run_id ?? null}
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
