import { useEffect, useState } from 'react'
import { ArrowRight } from 'lucide-react'
import type { AdapterRegistry, CustomerConfig, CustomerInfo } from '../types'
import { fetchAdapters, fetchCustomerConfig } from '../api'
import { MatchingConfigPanel } from './MatchingConfigPanel'
import { ZoneDirectoryPanel } from './ZoneDirectoryPanel'
import { CollectionSettingsPanel } from './CollectionSettingsPanel'
import { UsersPanel } from './UsersPanel'
import { useAuth } from '../auth'
import { CustomerSelect, Notice, PageHeader, TextLink } from './ui'

type Tab = 'matching' | 'zones' | 'collection' | 'sources' | 'users'

const TABS: Array<{ key: Tab; label: string }> = [
  { key: 'matching', label: 'Matching config' },
  { key: 'zones', label: 'Zones' },
  { key: 'collection', label: 'Daily collection' },
  { key: 'sources', label: 'Source setup' },
  { key: 'users', label: 'Users' },
]

/** the fixed slots' names; extra lineage slots read "Lineage · <key>" */
const SLOT_NAME: Record<string, string> = {
  bank_statement: 'Bank statement',
  bill_status: 'Bill status',
  lineage_rnote: 'Receipt notes',
  lineage_crn: 'CRN report',
  ar_statement: 'AR statement',
}
const slotName = (slot: string) =>
  SLOT_NAME[slot] ?? (slot.startsWith('lineage_') ? `Lineage · ${slot.slice(8)}` : slot)

/**
 * Customer-level settings, kept apart from the run workflow: the matching
 * rules every run applies (moved here from the Reconcile page) and a
 * read-out of which document format each ingest slot expects. Every save
 * is recorded in the Audit trail with the fields it changed.
 */
export function SettingsView({ customers, customerId, onCustomerChange, onGoToIngest }: {
  customers: CustomerInfo[]
  customerId: string
  onCustomerChange: (key: string) => void
  onGoToIngest: () => void
}) {
  const [tab, setTab] = useState<Tab>('matching')
  const isAdmin = useAuth().user.role === 'admin'
  const customerName = customers.find((c) => c.key === customerId)?.name ?? customerId

  return (
    <section className="ui-page">
      <PageHeader title="Settings" context={customerName}>
        <CustomerSelect customers={customers} value={customerId} onChange={onCustomerChange} />
      </PageHeader>

      <section className="ui-card">
        <div className="ui-tabbar">
          <div className="ui-tabs" role="tablist">
            {TABS.filter((t) => t.key !== 'users' || isAdmin).map((t) => (
              <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
                      className={`ui-tab${tab === t.key ? ' is-on' : ''}`} onClick={() => setTab(t.key)}>
                {t.label}
              </button>
            ))}
          </div>
          <span className="ui-tabbar-note">Every change is logged in the Audit trail</span>
        </div>
        {!isAdmin && <Notice tone="warn">Read-only. Saving settings needs the admin role.</Notice>}
        {tab === 'matching' && (
          <div className="ui-card-body config-body">
            <MatchingConfigPanel customerId={customerId} />
          </div>
        )}
        {tab === 'zones' && (
          <div className="ui-card-body config-body">
            <ZoneDirectoryPanel customerId={customerId} />
          </div>
        )}
        {tab === 'collection' && (
          <div className="ui-card-body config-body">
            <CollectionSettingsPanel customerId={customerId} />
          </div>
        )}
        {tab === 'users' && isAdmin && (
          <div className="ui-card-body config-body">
            <UsersPanel />
          </div>
        )}
        {tab === 'sources' && <SourceSetup customerId={customerId} onGoToIngest={onGoToIngest} />}
      </section>
    </section>
  )
}

function SourceSetup({ customerId, onGoToIngest }: { customerId: string; onGoToIngest: () => void }) {
  const [config, setConfig] = useState<CustomerConfig | null>(null)
  const [adapters, setAdapters] = useState<AdapterRegistry>({})
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setError(null)
    setConfig(null)
    Promise.all([fetchCustomerConfig(customerId), fetchAdapters()])
      .then(([c, a]) => { setConfig(c); setAdapters(a) })
      .catch((e) => setError(String(e.message ?? e)))
  }, [customerId])

  if (error) return <div className="ui-card-body"><Notice tone="error">Could not load sources: {error}</Notice></div>
  if (!config) return <div className="dt-loading"><span className="quill" /> Loading…</div>

  const label = (key: string) =>
    Object.values(adapters).flat().find((a) => a.key === key)
  const slots = Object.entries(config.sources)
    .sort(([a], [b]) => (SLOT_NAME[a] ? 0 : 1) - (SLOT_NAME[b] ? 0 : 1) || a.localeCompare(b))
  return (
    <div className="ui-card-body">
      <dl className="kv-list">
        {slots.map(([slot, key]) => {
          const a = label(key)
          return (
            <div key={slot}>
              <dt>{slotName(slot)}</dt>
              <dd>
                {a ? `${a.system} · ${a.label}` : key}
                {a?.file_kinds?.length ? <span className="chip-note"> {a.file_kinds.join(' ')}</span> : null}
              </dd>
            </div>
          )
        })}
      </dl>
      <p className="settings-foot">
        Formats are chosen per upload slot.{' '}
        <TextLink onClick={onGoToIngest}>Change on Ingest <ArrowRight size={13} strokeWidth={2} /></TextLink>
      </p>
    </div>
  )
}
