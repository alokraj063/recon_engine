import { ApiError } from '../types'

export function ErrorBanner({ error }: { error: ApiError }) {
  if (error.code === 'BANK_SELFCHECK_FAILED') {
    return (
      <div className="error-banner">
        <h3>Statement totals do not reconcile — run stopped</h3>
        <p>
          Credits parsed from the statement do not match its printed totals. No results were
          produced.
        </p>
        <p className="figures">{error.message}</p>
      </div>
    )
  }
  if (error.code === 'RUN_IN_PROGRESS') {
    return (
      <div className="error-banner">
        <h3>A reconciliation is already running for this customer</h3>
        <p>
          Only one incremental run can run at a time. Try again when it completes.
        </p>
        <p className="figures">{error.message}</p>
      </div>
    )
  }
  const titles: Record<string, string> = {
    INVALID_INPUT: 'Input rejected',
    PARSE_FAILED: 'A source document could not be parsed',
    NETWORK: 'Backend unreachable',
    RUN_NOT_FOUND: 'Run not found',
    STATEMENT_NOT_FOUND:
      'Statement not found for this customer',
    RECONCILE_FAILED: 'Reconciliation failed',
  }
  return (
    <div className="error-banner">
      <h3>{titles[error.code] ?? `Run failed (${error.code})`}</h3>
      <p className="figures">{error.message}</p>
    </div>
  )
}
