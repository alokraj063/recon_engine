import { ApiError } from '../types'

export function ErrorBanner({ error }: { error: ApiError }) {
  if (error.code === 'BANK_SELFCHECK_FAILED') {
    return (
      <div className="error-banner">
        <h3>Statement parse did not tie out — results were not produced</h3>
        <p>
          The credits parsed from the PDF do not match the totals HSBC prints on the last page.
          Everything downstream depends on that parse, so the engine stopped rather than produce a
          plausible-looking wrong answer.
        </p>
        <p className="figures">{error.message}</p>
      </div>
    )
  }
  const titles: Record<string, string> = {
    INVALID_INPUT: 'Input rejected',
    PARSE_FAILED: 'A source document could not be parsed',
    NETWORK: 'Backend unreachable',
  }
  return (
    <div className="error-banner">
      <h3>{titles[error.code] ?? `Run failed (${error.code})`}</h3>
      <p className="figures">{error.message}</p>
    </div>
  )
}
