import { useEffect, useState, type ComponentType } from 'react'
import {
  ArrowRight, FileSearch, GitMerge, Layers, LayoutDashboard, ListChecks, Upload,
} from 'lucide-react'
import type { Overview } from '../types'
import { fetchOverview, fetchRuns } from '../api'
import { ARCHITECTURE_LAYERS, type LayerKPI } from '../architecture'
import type { View } from './Sidebar'
import { Dot, PageHeader } from './ui'

interface Props {
  customerId: string
  onNavigate: (v: View) => void
}

type IconType = ComponentType<{ size?: number | string; strokeWidth?: number | string }>

// one icon per layer, kept out of architecture.ts so the data file stays
// presentation-free; all six are already in the bundle via the sidebar
const LAYER_ICONS: Record<string, IconType> = {
  sources: Upload,
  medallion: Layers,
  engine: GitMerge,
  ledger: ListChecks,
  governance: FileSearch,
  interface: LayoutDashboard,
}

/**
 * Full-width pipeline: one node per layer across the page, arrows between,
 * the selected layer's detail below in three columns. A layer is ALWAYS
 * selected (no collapse) — the page is a map, not an accordion.
 */
export function ArchitectureView({ customerId, onNavigate }: Props) {
  const [open, setOpen] = useState<string>(ARCHITECTURE_LAYERS[0].id)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [runCount, setRunCount] = useState<number | null>(null)

  useEffect(() => {
    fetchOverview(customerId).then(setOverview).catch(() => setOverview(null))
    fetchRuns(customerId).then((rs) => setRunCount(rs.length)).catch(() => setRunCount(null))
  }, [customerId])

  const kpiValue = (k: LayerKPI): string =>
    k.live ? k.live(overview, runCount) : k.value ?? '—'

  const layer = ARCHITECTURE_LAYERS.find((l) => l.id === open) ?? ARCHITECTURE_LAYERS[0]

  // left/right arrows step through the layers when the row has focus
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    const i = ARCHITECTURE_LAYERS.findIndex((l) => l.id === open)
    const next = e.key === 'ArrowRight'
      ? Math.min(ARCHITECTURE_LAYERS.length - 1, i + 1)
      : Math.max(0, i - 1)
    setOpen(ARCHITECTURE_LAYERS[next].id)
    e.preventDefault()
  }

  return (
    <section className="ui-page">
      <PageHeader title="Architecture"
                  context={<>Sources → medallion store → matching engine → ledger → governance →
                    interface<Dot />live figures for {customerId}</>} />

      <section className="ui-card arch-card">
      <div className="arch-pipe" role="tablist" tabIndex={0} onKeyDown={onKey}>
        {ARCHITECTURE_LAYERS.map((l, i) => {
          const Icon = LAYER_ICONS[l.id]
          const on = l.id === open
          return (
            <span key={l.id} className="arch-pipe-cell">
              <button
                className={`arch-node${on ? ' on' : ''}`}
                role="tab"
                aria-selected={on}
                onClick={() => setOpen(l.id)}
              >
                <span className="arch-node-top">
                  <span className="arch-index">{l.index}</span>
                  {Icon && <span className="arch-node-ic"><Icon size={15} strokeWidth={1.75} /></span>}
                </span>
                <span className="arch-node-title">{l.short ?? l.title}</span>
                <span className="arch-node-kpis">
                  {l.kpis.slice(0, 2).map((k) => (
                    <span key={k.label} className="arch-node-kpi">
                      <span className="arch-kpi-value">{kpiValue(k)}</span>
                      <span className="arch-kpi-label">{k.label}</span>
                    </span>
                  ))}
                </span>
              </button>
              {i < ARCHITECTURE_LAYERS.length - 1 && (
                <span className="arch-arrow" aria-hidden>
                  <ArrowRight size={16} strokeWidth={1.75} />
                </span>
              )}
            </span>
          )
        })}
      </div>

      </section>

      <section className="ui-card arch-detail" role="tabpanel" key={layer.id}>
        <div className="arch-detail-head">
          <span className="arch-index">{layer.index}</span>
          <div className="arch-detail-main">
            <h3 className="arch-detail-title">{layer.title}</h3>
            <p className="arch-oneliner">{layer.oneLiner}</p>
          </div>
          {layer.linksTo.length > 0 && (
            <div className="arch-links">
              <span className="arch-label">Operating screens</span>
              {layer.linksTo.map((l) => (
                <button key={l.view} type="button" className="ui-btn is-sm" onClick={() => onNavigate(l.view)}>
                  {l.label} <ArrowRight size={13} strokeWidth={2} />
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="arch-detail-grid">
          <div>
            <div className="arch-label">About</div>
            <p className="arch-desc">{layer.description}</p>
          </div>
          <div>
            <div className="arch-label">Components</div>
            <div className="arch-components">
              {layer.components.map((c) => (
                <div key={c.name} className="arch-component">
                  <div className="arch-comp-name">{c.name}</div>
                  <div className="arch-comp-detail">{c.detail}</div>
                  {c.code && <div className="arch-comp-code">{c.code}</div>}
                </div>
              ))}
            </div>
          </div>
          <div className="arch-right">
            <div>
              <div className="arch-label">Figures</div>
              <div className="arch-kpis">
                {layer.kpis.map((k) => (
                  <div key={k.label} className="arch-kpi">
                    <div className="arch-kpi-value">{kpiValue(k)}</div>
                    <div className="arch-kpi-label">{k.label}</div>
                    {k.hint && <div className="arch-kpi-hint">{k.hint}</div>}
                  </div>
                ))}
              </div>
            </div>
            <div className="arch-flows">
              <div>
                <div className="arch-label">Flows in</div>
                {layer.flowsIn.map((f) => (<div key={f} className="arch-flow">↓ {f}</div>))}
              </div>
              <div>
                <div className="arch-label">Flows out</div>
                {layer.flowsOut.map((f) => (<div key={f} className="arch-flow">↑ {f}</div>))}
              </div>
            </div>
          </div>
        </div>
      </section>
    </section>
  )
}
