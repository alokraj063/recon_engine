import type { Overview } from './types'
import type { View } from './components/Sidebar'
import { fmtWhen } from './format'

/**
 * The Architecture view's content — a truthful description of THIS
 * system, layer by layer, in the presentation style of recon-alpha's
 * reference architecture. KPIs are LIVE where the overview endpoint can
 * supply them; the rest are structural facts of the codebase.
 */

export interface LayerKPI {
  label: string
  value?: string
  hint?: string
  live?: (o: Overview | null, runCount: number | null) => string
}

export interface LayerComponent {
  name: string
  detail: string
  code?: string
}

export interface LayerSpec {
  id: string
  index: number
  title: string
  oneLiner: string
  description: string
  components: LayerComponent[]
  kpis: LayerKPI[]
  flowsIn: string[]
  flowsOut: string[]
  linksTo: Array<{ label: string; view: View }>
}

const n = (v: number | undefined | null) =>
  v === null || v === undefined ? '—' : v.toLocaleString('en-IN')

export const ARCHITECTURE_LAYERS: LayerSpec[] = [
  {
    id: 'sources',
    index: 1,
    title: 'Sources & Adapters',
    oneLiner:
      'Per-source parsers behind a pluggable adapter registry — a new bank or ERP is one adapter plus a field map.',
    description:
      'Each raw document has a parser that understands its native shape: the HSBC daily statement PDF, ' +
      'the IREPS "Bill Status" block-format Excel (label-driven, not a table), and the RNOTE/CRN lineage ' +
      'reports. Adapters wrap parsers in a registry keyed by (source type × format × system), so the UI ' +
      'can offer file-format choices per customer. Each adapter owns the rename map from its source-native ' +
      'columns onto the canonical gold schema — that map IS the seam a new bank or ERP plugs into. The bank ' +
      'adapter is fail-loud: parsed credits must tie to the totals printed on the statement itself.',
    components: [
      { name: 'HSBC statement adapter', detail: 'PDF → bank transactions, with printed-total self-check', code: 'sources/hsbc_bank.py' },
      { name: 'IREPS bill status adapter', detail: 'Block-format Excel → bills + recovery lines', code: 'sources/ireps_bills.py' },
      { name: 'IREPS RNOTE / CRN adapters', detail: 'Lineage reports → unified upstream documents', code: 'sources/ireps_rnote.py · ireps_crn.py' },
      { name: 'Silver→gold rename maps', detail: 'The adapter seam: source columns onto canonical names', code: 'BILLS_TO_GOLD / RECOVERIES_TO_GOLD' },
    ],
    kpis: [
      { label: 'Adapters', value: '4', hint: 'bank + 3 ERP documents' },
      { label: 'Systems', value: '2', hint: 'HSBC · IREPS' },
      { label: 'Last ingestion', live: (o) => (o?.last_ingestion ? fmtWhen(o.last_ingestion.at) : 'none') },
    ],
    flowsIn: ['Raw files uploaded on the Ingest page (or bundled default documents)'],
    flowsOut: ['Parsed source-native rows → silver', 'Canonical frames → gold'],
    linksTo: [{ label: 'Ingest files', view: 'ingest' }],
  },
  {
    id: 'medallion',
    index: 2,
    title: 'Medallion Store — Bronze → Silver → Gold',
    oneLiner:
      'Raw bytes, source-native rows, and one canonical schema — per customer, one DATABASE_URL from laptop to RDS.',
    description:
      'Bronze keeps the original files content-addressed by hash (the same bytes never ingest twice). Silver ' +
      'keeps each parsed row exactly as the source spelled it. Gold is the single canonical snake_case schema ' +
      'every downstream consumer speaks — engine, database, API, UI and workbook use the same names ' +
      '(submission_ref, payment_order_date, net_payable_amount…). Bills are entity-upserted by ' +
      '(bill_number, submission_ref) so daily exports refresh rather than duplicate, and a bill locked into a ' +
      'settled match is protected: a newer export that tries to change it is refused and recorded as a conflict. ' +
      'Locally the three layers are separate SQLite files stitched by ATTACH; on Postgres they are real schemas — ' +
      'same code, one connection string.',
    components: [
      { name: 'Bronze', detail: 'Content-addressed raw files, dedup by sha256', code: 'db/bronze.py · db/storage.py' },
      { name: 'Silver', detail: 'One JSON row per parsed source row, per file', code: 'db/silver.py' },
      { name: 'Gold', detail: 'Canonical bank_txns / bills / recoveries / lineage_docs', code: 'recon/gold/schemas.py · db/gold.py' },
      { name: 'Idempotent ingestion', detail: 'File dedup + entity upsert + LOCKED-bill conflicts', code: 'db/ingest.py' },
    ],
    kpis: [
      { label: 'Bills', live: (o) => n(o?.gold.bills) },
      { label: 'Bank txns', live: (o) => n(o?.gold.bank_txns) },
      { label: 'Lineage docs', live: (o) => n(o?.gold.lineage_docs) },
    ],
    flowsIn: ['Adapter output (canonical frames)'],
    flowsOut: ['Reconciliation pools (engine)', 'Gold data browse tabs'],
    linksTo: [
      { label: 'Gold — Bills', view: 'gold_bills' },
      { label: 'Gold — Bank txns', view: 'gold_bank' },
      { label: 'Gold — Lineage docs', view: 'gold_lineage' },
    ],
  },
  {
    id: 'engine',
    index: 3,
    title: 'Matching Engine',
    oneLiner:
      'Three-pass matcher over configurable field signals; amount is a filter, never a guess.',
    description:
      'Pairs are only considered when amounts already agree (credit amount = bill net payable, within ' +
      'tolerance); date and exact signals then score and break ties. Pass 1 scores every amount-eligible ' +
      'pair; pass 2 assigns best-first so an early statement row cannot steal a later row\'s bill — a credit ' +
      'whose best pairing was claimed falls to the exception queue rather than settling for worse. Pass 3 ' +
      'finds batches: one credit covering several bills that sum to it. Which fields drive all of this is ' +
      'per-customer configuration (amount pair, primary/fallback dates, exact-signal pairs with weights, ' +
      'eligibility statuses). Weak matches are flagged with the exact failing check by name. Every engine ' +
      'change is gated by a golden master: six output frames byte-compared against committed snapshots.',
    components: [
      { name: 'Three-pass matcher', detail: 'Score all → assign best-first → subset-sum batches', code: 'matching/matcher.py' },
      { name: 'Field mapping', detail: 'Per-customer signals, tolerances, eligibility', code: 'rules.py · ⚙ Matching config' },
      { name: 'Two-sided exceptions', detail: 'BANK_ONLY and BILL_ONLY — both sides are truth', code: 'engine.py' },
      { name: 'Golden-master gate', detail: 'Byte-exact CSV diff on the sample documents', code: 'tests/test_golden.py' },
    ],
    kpis: [
      { label: 'Match rate', live: (o) => (o?.match_rate == null ? '—' : `${(o.match_rate * 100).toFixed(1)}%`), hint: 'credits settled' },
      { label: 'Confidence tiers', value: '6', hint: 'HIGH → BATCHED' },
      { label: 'Golden frames', value: '6', hint: 'byte-diff gated' },
    ],
    flowsIn: ['Gold pools (credits + eligible bills)', 'Customer matching config'],
    flowsOut: ['Matched frame + exception queue → results', 'Durable matches → ledger (incremental)'],
    linksTo: [{ label: 'Run reconciliation', view: 'reconcile' }],
  },
  {
    id: 'ledger',
    index: 4,
    title: 'Durable Ledger',
    oneLiner:
      'Incremental runs accumulate: locked matches stay settled, open exceptions carry forward until resolved.',
    description:
      'Snapshot runs compute and forget; incremental runs feed a durable ledger. HIGH-confidence matches ' +
      'auto-lock; review confidences wait in the Analyst queue where a human accepts (any candidate bill — ' +
      'the analyst\'s pick wins over the matcher\'s), rejects (releasing both sides back to the pool), or ' +
      'later unlocks a locked decision to reopen it. Open exceptions are re-attempted by every subsequent ' +
      'run and flip to RESOLVED when their counterpart arrives. A locked match\'s credit and bills never ' +
      're-enter any pool, and its bills are shielded from newer exports.',
    components: [
      { name: 'Match ledger', detail: 'OPEN / LOCKED / REJECTED, auto-lock on HIGH, unlock undo', code: 'db/incremental.py' },
      { name: 'Exception lifecycle', detail: 'OPEN → RESOLVED across runs, carried in every pool', code: 'exception_ledger' },
      { name: 'Decision evidence', detail: 'Signals + candidate cards pulled from the creating run', code: 'Analyst queue expand' },
    ],
    kpis: [
      { label: 'Locked', live: (o) => n(o?.matches.LOCKED) },
      { label: 'Open reviews', live: (o) => n(o?.matches.OPEN) },
      { label: 'Open exceptions', live: (o) => n(o ? o.open_exceptions.BANK_ONLY + o.open_exceptions.BILL_ONLY : null) },
    ],
    flowsIn: ['Incremental run results', 'Analyst decisions'],
    flowsOut: ['Future run pools (consumed items excluded)', 'Command Center KPIs'],
    linksTo: [{ label: 'Analyst queue', view: 'ledger' }],
  },
  {
    id: 'governance',
    index: 5,
    title: 'Governance & Audit',
    oneLiner:
      'Every action leaves a transactional audit event; every run freezes its evidence.',
    description:
      'The audit log is written in the same database transaction as the action it describes — a rollback ' +
      'erases both, so the trail cannot drift from reality. Details carry counts, ids and field names only, ' +
      'never amounts or narratives. Each run persists its exact payload, its four input frames and its ' +
      'workbook: frozen evidence of what was decided on, immune to later ingests. Structured logs mirror ' +
      'the same event vocabulary to console and a rotating JSON file.',
    components: [
      { name: 'Audit log', detail: 'Same-transaction event stream, no PII in details', code: 'db/audit.py → audit_log' },
      { name: 'Frozen run evidence', detail: 'Payload + frames + workbook per run, forever', code: 'db/runs_store.py' },
      { name: 'Structured logging', detail: 'Console + rotating JSON, request/customer/run context', code: 'logging_setup.py' },
    ],
    kpis: [
      { label: 'Runs recorded', live: (_o, runs) => (runs === null ? '—' : String(runs)) },
      { label: 'Last run', live: (o) => (o?.last_run ? fmtWhen(o.last_run.created_at) : 'none') },
      { label: 'Write model', value: 'same-txn', hint: 'event + action commit together' },
    ],
    flowsIn: ['Every ingest, run, decision and config change'],
    flowsOut: ['Audit trail view', 'Run data tabs (frozen frames)'],
    linksTo: [{ label: 'Audit trail', view: 'audit' }],
  },
  {
    id: 'interface',
    index: 6,
    title: 'API & Interface',
    oneLiner:
      'A JSON API over the whole engine, and this SPA — plus an Excel workbook for everyone else.',
    description:
      'FastAPI exposes the two-step flow (ingest → reconcile-from-gold) plus configuration, ledger ' +
      'decisions, browse endpoints and the Command Center aggregates. The React app is one vocabulary ' +
      'end-to-end with the gold schema — the column you configure is the column you see. Results are also ' +
      'written as a formatted Excel workbook per run for people who live in spreadsheets. Layers depend ' +
      'one way only: parsers know nothing of matching, the engine knows nothing of the database, the API ' +
      'wires it all together.',
    components: [
      { name: 'FastAPI routes', detail: 'ingest / reconcile / config / ledger / overview / audit', code: 'app/routes.py' },
      { name: 'React SPA', detail: 'Command Center, multi-run picker, evidence panels', code: 'frontend/src' },
      { name: 'Workbook export', detail: 'Formatted Excel per run — formatting only, decides nothing', code: 'recon/report.py' },
    ],
    kpis: [
      { label: 'Customers', value: 'multi', hint: 'config + data per customer' },
      { label: 'Modes', value: '2', hint: 'snapshot · incremental' },
      { label: 'Output', value: 'JSON + XLSX' },
    ],
    flowsIn: ['Engine results, ledger state, gold layer'],
    flowsOut: ['Everything on screen, workbook downloads'],
    linksTo: [{ label: 'Command Center', view: 'command' }],
  },
]
