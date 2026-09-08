/** Confidence stamp. MANUAL is the frozen code for a match an analyst
 *  created by hand — it reads "MATCHED BY USER" but keeps its code as
 *  the CSS hook and filter value. */
export function ConfidenceBadge({ label }: { label: string }) {
  const text = label === 'MANUAL' ? 'MATCHED BY USER' : label
  return <span className={`stamp stamp-${label}`} title={label === 'MANUAL' ? 'manual match (MANUAL)' : undefined}>{text}</span>
}
