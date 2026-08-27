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
    | 'architecture' | 'gold_bank' | 'gold_bills' | 'gold_recoveries' | 'gold_lineage'>,
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

function hashRunId(): string | null {
  const m = window.location.hash.match(/run=([\w-]+)/)
  return m ? m[1] : null
}

export default function App() {
  const [running, setRunning] = useState(false)
  const [restoring, setRestoring] = useState(false)
  const [view, setView] = useState<View>('command')
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
      // a selection belongs to one customer's run history
      setSelectedRuns(null)
      window.location.hash = ''
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
        window.location.hash = 'run=' + ids[0]
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
    if (payloadCache.has(runId)) {
      setView('summary')
    } else {
      window.location.hash = ''
      setView('reconcile')
    }
  }

  useEffect(() => {
    fetchCustomers().then((cs) => cs.length && setCustomers(cs)).catch(() => {})
    const id = hashRunId()
    if (id) void openRun(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
      window.location.hash = 'run=' + res.run_id
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
        {view === 'command' && (
          <CommandCenter
            customers={customers}
            customerId={customerId}
            onCustomerChange={setCustomerId}
            onNavigate={setView}
            refreshKey={ingestEpoch + (selectedRuns?.length ?? 0)}
          />
        )}

        {view === 'ingest' && (
          <>
            <IngestForm
              customers={customers}
              customerId={customerId}
              onCustomerChange={setCustomerId}
              onCustomersChanged={() =>
                fetchCustomers().then((cs) => cs.length && setCustomers(cs)).catch(() => {})}
              onIngested={onIngested}
            />
            {restoring && <p className="footer-note">Restoring run…</p>}
          </>
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
      </main>
    </div>
  )
}
