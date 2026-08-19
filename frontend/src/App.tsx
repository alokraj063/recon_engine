import { useState } from 'react'
import { runRecon, type UploadFiles } from './api'
import { ApiError, DEFAULT_OPTIONS, type FrameName, type ReconResponse, type TunableOptions } from './types'
import { ErrorBanner } from './components/ErrorBanner'
import { ExceptionQueue } from './components/ExceptionQueue'
import { MatchedTable } from './components/MatchedTable'
import { Sidebar, type View } from './components/Sidebar'
import { SourceTable } from './components/SourceTable'
import { SummaryDashboard } from './components/SummaryDashboard'
import { UploadForm } from './components/UploadForm'

const FRAME_VIEWS: FrameName[] = ['bank', 'bills', 'bills_enriched', 'recoveries']

const VIEW_TITLES: Record<Exclude<View, 'setup'>, string> = {
  summary: 'Summary',
  matched: 'Matched',
  exceptions: 'Exception queue',
  bank: 'Bank statement — as extracted',
  bills: 'Bills — as parsed from IREPS',
  bills_enriched: 'Bills — enriched with lineage',
  recoveries: 'Recovery detail',
}

export default function App() {
  const [running, setRunning] = useState(false)
  const [options, setOptions] = useState<TunableOptions>(DEFAULT_OPTIONS)
  const [result, setResult] = useState<ReconResponse | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [view, setView] = useState<View>('setup')

  const onRun = async (files: UploadFiles) => {
    setRunning(true)
    setError(null)
    try {
      const res = await runRecon(files, options)
      setResult(res)
      setView('summary')
    } catch (e) {
      setResult(null)
      setError(e instanceof ApiError ? e : new ApiError('UNKNOWN', String(e)))
    } finally {
      setRunning(false)
    }
  }

  const showResult = view !== 'setup' && result

  return (
    <div className="layout">
      <Sidebar view={view} onNavigate={setView} result={result} />

      <main className="content">
        {view === 'setup' && (
          <>
            <UploadForm running={running} options={options} onOptionsChange={setOptions} onRun={onRun} />
            {error && <ErrorBanner error={error} />}
            {result && (
              <p className="footer-note">
                A run is loaded — pick a view from the left, or run again with different inputs.
              </p>
            )}
          </>
        )}

        {showResult && (
          <>
            <div className="result-head">
              <h2>{VIEW_TITLES[view as Exclude<View, 'setup'>]}</h2>
              <span className="file-note">
                {result.meta.filenames.statement} ✕ {result.meta.filenames.bills}
              </span>
            </div>

            <div className="view-card">
              {view === 'summary' && <SummaryDashboard summary={result.summary} meta={result.meta} />}
              {view === 'matched' && <MatchedTable rows={result.matched} />}
              {view === 'exceptions' && <ExceptionQueue rows={result.exceptions} />}
              {FRAME_VIEWS.includes(view as FrameName) && (
                <SourceTable runId={result.run_id} name={view as FrameName} />
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
