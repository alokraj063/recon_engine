import type { ComponentType } from 'react'
import {
  ArrowDownLeft, Boxes, FileSearch, FileStack, Gauge, GitMerge,
  Landmark, LayoutDashboard, Link2, ListChecks, ListMinus, ReceiptText,
  TriangleAlert, Upload, Download,
} from 'lucide-react'
import type { ReconResponse } from '../types'
import { workbookUrl } from '../api'
import {
  DATA_PAGES, type DataPage, type DataScope, dataPageOf, scopeOf, viewForScope,
} from '../dataPages'
import logo from '../assets/jouletowatts_logo.png'

export type View =
  | 'command'
  | 'ingest'
  | 'reconcile'
  | 'ledger'
  | 'ar'
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
  /** which scope a Data-group click opens (the user's last choice) */
  dataScope: DataScope
}

const DATA_ICONS: Record<DataPage['id'], IconType> = {
  bank: Landmark, bills: ReceiptText, recoveries: ListMinus, lineage: FileStack,
}

function NavIcon({ icon: Icon }: { icon: IconType }) {
  return (
    <span className="nav-ic" aria-hidden>
      <Icon size={15} strokeWidth={1.75} />
    </span>
  )
}

export function Sidebar({ view, onNavigate, result, dataScope }: Props) {
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
  // one Data page per kind; a click opens the user's preferred scope,
  // active in EITHER scope. Row badges exist only in run scope (a run
  // payload carries its frame counts; the live gold total is only known
  // once its table loads).
  const runCount = (page: DataPage): number | undefined => {
    if (!counts || scopeOf(view) !== 'run' || dataPageOf(view) !== page) return undefined
    switch (page.id) {
      case 'bank': return counts.bank_txns
      case 'bills': return view === page.runTrail ? counts.bills_grouped : counts.bills
      case 'recoveries': return counts.recoveries
      default: return undefined
    }
  }
  const dataItem = (page: DataPage) => {
    const active = dataPageOf(view) === page
    const count = runCount(page)
    return (
      <button
        key={page.id}
        className={`nav-item${active ? ' active' : ''}`}
        onClick={() => onNavigate(viewForScope(page, dataScope))}
      >
        <span className="nav-main"><NavIcon icon={DATA_ICONS[page.id]} />{page.label}</span>
        {count !== undefined && <span className="nav-count">{count}</span>}
      </button>
    )
  }

  // result views are always reachable: with no run loaded they open on
  // the latest run (App auto-loads it) or, with no runs yet, on a guide
  // to run one — never a greyed-out item
  const item = ({ view: v, label, icon, count }: NavItem) => (
    <button
      key={v}
      className={`nav-item${view === v ? ' active' : ''}${result ? '' : ' nav-item-idle'}`}
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
      </div>

      <nav className="sidebar-nav">
        <div className="nav-group-label">Operate</div>
        {openItem({ view: 'command', label: 'Command Center', icon: LayoutDashboard })}
        {openItem({ view: 'ingest', label: 'Ingest documents', icon: Upload })}
        {openItem({ view: 'reconcile', label: 'Reconcile', icon: GitMerge })}

        <div className="nav-group-label">Workspace</div>
        {openItem({ view: 'ledger', label: 'Analyst queue', icon: ListChecks })}
        {openItem({ view: 'ar', label: 'AR Reconciliation', icon: ArrowDownLeft })}
        {openItem({ view: 'audit', label: 'Audit trail', icon: FileSearch })}

        <div className="nav-group-label">Reconciliation result</div>
        {resultItems.map(item)}

        <div className="nav-group-label">Data</div>
        {DATA_PAGES.map(dataItem)}

        <div className="nav-group-label">Platform</div>
        {openItem({ view: 'architecture', label: 'Architecture', icon: Boxes })}
      </nav>

      <div className="sidebar-foot">
        {result ? (
          <a className="btn-download btn-download-side btn-ic" href={workbookUrl(result.run_id)} download>
            <Download size={14} strokeWidth={1.75} /> Workbook (.xlsx)
          </a>
        ) : (
          <p className="sidebar-note">
            Ingest documents, then initiate a reconciliation — or open a result view to
            pick a past run.
          </p>
        )}
      </div>
    </aside>
  )
}
