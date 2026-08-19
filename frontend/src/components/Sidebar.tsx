import type { ReconResponse } from '../types'
import { workbookUrl } from '../api'

export type View =
  | 'setup'
  | 'summary'
  | 'matched'
  | 'exceptions'
  | 'bank'
  | 'bills'
  | 'bills_enriched'
  | 'recoveries'

interface NavItem {
  view: View
  label: string
  count?: number
}

interface Props {
  view: View
  onNavigate: (v: View) => void
  result: ReconResponse | null
}

export function Sidebar({ view, onNavigate, result }: Props) {
  const counts = result?.meta.counts

  const resultItems: NavItem[] = [
    { view: 'summary', label: 'Summary' },
    { view: 'matched', label: 'Matched', count: counts?.matched },
    {
      view: 'exceptions',
      label: 'Exception queue',
      count: counts ? counts.bank_only + counts.bill_only + (counts.match_review ?? 0) : undefined,
    },
  ]
  const sourceItems: NavItem[] = [
    { view: 'bank', label: 'Bank statement', count: counts?.bank_txns },
    { view: 'bills', label: 'Bills', count: counts?.bills },
    { view: 'bills_enriched', label: 'Bills + lineage', count: counts?.bills_grouped },
    { view: 'recoveries', label: 'Recoveries', count: counts?.recoveries },
  ]

  const item = ({ view: v, label, count }: NavItem) => (
    <button
      key={v}
      className={`nav-item${view === v ? ' active' : ''}`}
      disabled={!result}
      onClick={() => onNavigate(v)}
    >
      <span>{label}</span>
      {count !== undefined && <span className="nav-count">{count}</span>}
    </button>
  )

  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <h1>
          Recon <span className="amp">Engine</span>
        </h1>
        <p className="strapline">receivables</p>
      </div>

      <nav className="sidebar-nav">
        <button
          className={`nav-item nav-setup${view === 'setup' ? ' active' : ''}`}
          onClick={() => onNavigate('setup')}
        >
          <span>⚙ Run setup</span>
        </button>

        <div className="nav-group-label">Reconciliation result</div>
        {resultItems.map(item)}

        <div className="nav-group-label">Source data</div>
        {sourceItems.map(item)}
      </nav>

      <div className="sidebar-foot">
        {result ? (
          <a className="btn-download btn-download-side" href={workbookUrl(result.run_id)} download>
            ⤓ Workbook (.xlsx)
          </a>
        ) : (
          <p className="sidebar-note">Run a reconciliation to unlock the views.</p>
        )}
      </div>
    </aside>
  )
}
