import type { ComponentType } from 'react'
import {
  Boxes, FileSearch, FileStack, Gauge, GitBranch, GitMerge, Landmark,
  LayoutDashboard, Link2, ListChecks, ListMinus, ReceiptText,
  TriangleAlert, Upload, Download,
} from 'lucide-react'
import type { ReconResponse } from '../types'
import { workbookUrl } from '../api'
import logo from '../assets/jouletowatts_logo.png'

export type View =
  | 'command'
  | 'ingest'
  | 'reconcile'
  | 'ledger'
  | 'audit'
  | 'architecture'
  | 'summary'
  | 'matched'
  | 'exceptions'
  | 'bank'
  | 'bills'
  | 'bills_enriched'
  | 'recoveries'
  | 'gold_bank'
  | 'gold_bills'
  | 'gold_recoveries'
  | 'gold_lineage'

type IconType = ComponentType<{ size?: number | string; strokeWidth?: number | string }>

interface NavItem {
  view: View
  label: string
  icon: IconType
  count?: number
}

interface Props {
  view: View
  onNavigate: (v: View) => void
  result: ReconResponse | null
}

function NavIcon({ icon: Icon }: { icon: IconType }) {
  return (
    <span className="nav-ic" aria-hidden>
      <Icon size={15} strokeWidth={1.75} />
    </span>
  )
}

export function Sidebar({ view, onNavigate, result }: Props) {
  const counts = result?.meta.counts

  const resultItems: NavItem[] = [
    { view: 'summary', label: 'Summary', icon: Gauge },
    { view: 'matched', label: 'Matched', icon: Link2, count: counts?.matched },
    {
      view: 'exceptions',
      label: 'Exception queue',
      icon: TriangleAlert,
      count: counts ? counts.bank_only + counts.bill_only + (counts.match_review ?? 0) : undefined,
    },
  ]
  // frozen per-run evidence, disabled until a run is loaded
  const runDataItems: NavItem[] = [
    { view: 'bank', label: 'Bank statement', icon: Landmark, count: counts?.bank_txns },
    { view: 'bills', label: 'Bills', icon: ReceiptText, count: counts?.bills },
    { view: 'bills_enriched', label: 'Bills + lineage', icon: GitBranch, count: counts?.bills_grouped },
    { view: 'recoveries', label: 'Recoveries', icon: ListMinus, count: counts?.recoveries },
  ]
  const goldItems: NavItem[] = [
    { view: 'gold_bank', label: 'Bank txns', icon: Landmark },
    { view: 'gold_bills', label: 'Bills', icon: ReceiptText },
    { view: 'gold_recoveries', label: 'Recoveries', icon: ListMinus },
    { view: 'gold_lineage', label: 'Lineage docs', icon: FileStack },
  ]

  const item = ({ view: v, label, icon, count }: NavItem) => (
    <button
      key={v}
      className={`nav-item${view === v ? ' active' : ''}`}
      disabled={!result}
      onClick={() => onNavigate(v)}
    >
      <span className="nav-main"><NavIcon icon={icon} />{label}</span>
      {count !== undefined && <span className="nav-count">{count}</span>}
    </button>
  )

  // views that work with no run loaded are never disabled
  const openItem = ({ view: v, label, icon }: NavItem) => (
    <button
      key={v}
      className={`nav-item${view === v ? ' active' : ''}`}
      onClick={() => onNavigate(v)}
    >
      <span className="nav-main"><NavIcon icon={icon} />{label}</span>
    </button>
  )

  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <span className="logo-plate">
          <img className="logo-img" src={logo} alt="Joules to Watts" />
        </span>
        <h1>
          Recon <span className="amp">Engine</span>
        </h1>
        <p className="strapline">receivables</p>
      </div>

      <nav className="sidebar-nav">
        <div className="nav-group-label">Operate</div>
        {openItem({ view: 'command', label: 'Command Center', icon: LayoutDashboard })}
        {openItem({ view: 'ingest', label: 'Ingest files', icon: Upload })}
        {openItem({ view: 'reconcile', label: 'Run reconciliation', icon: GitMerge })}

        <div className="nav-group-label">Workspace</div>
        {openItem({ view: 'ledger', label: 'Analyst queue', icon: ListChecks })}
        {openItem({ view: 'audit', label: 'Audit trail', icon: FileSearch })}

        <div className="nav-group-label">Reconciliation result</div>
        {resultItems.map(item)}
        <div className="nav-sub-label">Run data</div>
        {runDataItems.map(item)}

        <div className="nav-group-label">Gold data</div>
        {goldItems.map(openItem)}

        <div className="nav-group-label">Platform</div>
        {openItem({ view: 'architecture', label: 'Architecture', icon: Boxes })}
      </nav>

      <div className="sidebar-foot">
        {result ? (
          <a className="btn-download btn-download-side btn-ic" href={workbookUrl(result.run_id)} download>
            <Download size={14} strokeWidth={1.75} /> Workbook (.xlsx)
          </a>
        ) : (
          <p className="sidebar-note">Ingest files, then run a reconciliation.</p>
        )}
      </div>
    </aside>
  )
}
