import { useEffect, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import type { Overview } from '../types'
import { fetchOverview, fetchRuns } from '../api'
import { ARCHITECTURE_LAYERS, type LayerKPI } from '../architecture'
import type { View } from './Sidebar'

interface Props {
  customerId: string
  onNavigate: (v: View) => void
}

export function ArchitectureView({ customerId, onNavigate }: Props) {
  const [open, setOpen] = useState<string | null>(ARCHITECTURE_LAYERS[0].id)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [runCount, setRunCount] = useState<number | null>(null)

  useEffect(() => {
    fetchOverview(customerId).then(setOverview).catch(() => setOverview(null))
    fetchRuns(customerId).then((rs) => setRunCount(rs.length)).catch(() => setRunCount(null))
  }, [customerId])

  const kpiValue = (k: LayerKPI): string =>
    k.live ? k.live(overview, runCount) : k.value ?? '—'

  return (
    <section className="intake">
      <div className="ingest-head">
        <div>
          <h2>Architecture</h2>
          <p className="strap-note">
            the real stack — sources → medallion store → matching engine → ledger →
            governance → interface · live figures for customer {customerId}
          </p>
        </div>
      </div>

      <div className="arch-stack">
        {ARCHITECTURE_LAYERS.map((layer, i) => {
          const isOpen = open === layer.id
          return (
            <div key={layer.id}>
              <div className={`arch-card${isOpen ? ' open' : ''}`}>
                <button className="arch-face"
                        onClick={() => setOpen(isOpen ? null : layer.id)}>
                  <span className="arch-index">{layer.index}</span>
                  <span className="arch-face-main">
                    <span className="arch-title">
                      {layer.title} <ChevronRight className="chev chev-ic" size={14} strokeWidth={2} aria-hidden />
                    </span>
                    <span className="arch-oneliner">{layer.oneLiner}</span>
                  </span>
                  <span className="arch-face-kpis">
                    {layer.kpis.slice(0, 2).map((k) => (
                      <span key={k.label} className="arch-face-kpi">
                        <span className="arch-kpi-value">{kpiValue(k)}</span>
                        <span className="arch-kpi-label">{k.label}</span>
                      </span>
                    ))}
                  </span>
                </button>

                {isOpen && (
                  <div className="arch-body">
                    <div className="arch-left">
                      <p className="arch-desc">{layer.description}</p>
                      <div className="slot-label">Components</div>
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
                      <div className="slot-label">Figures</div>
                      <div className="arch-kpis">
                        {layer.kpis.map((k) => (
                          <div key={k.label} className="arch-kpi">
                            <div className="arch-kpi-value">{kpiValue(k)}</div>
                            <div className="arch-kpi-label">{k.label}</div>
                            {k.hint && <div className="arch-kpi-hint">{k.hint}</div>}
                          </div>
                        ))}
                      </div>
                      <div className="arch-flows">
                        <div>
                          <div className="slot-label">Flows in</div>
                          {layer.flowsIn.map((f) => (
                            <div key={f} className="arch-flow">↓ {f}</div>
                          ))}
                        </div>
                        <div>
                          <div className="slot-label">Flows out</div>
                          {layer.flowsOut.map((f) => (
                            <div key={f} className="arch-flow">↑ {f}</div>
                          ))}
                        </div>
                      </div>
                      {layer.linksTo.length > 0 && (
                        <div>
                          <div className="slot-label">Operating screens</div>
                          <div className="arch-links">
                            {layer.linksTo.map((l) => (
                              <button key={l.view} className="btn-open"
                                      onClick={() => onNavigate(l.view)}>
                                {l.label} ↗
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
              {i < ARCHITECTURE_LAYERS.length - 1 && (
                <div className="arch-connector">↓</div>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}
