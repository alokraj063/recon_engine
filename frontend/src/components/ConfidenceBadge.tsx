export function ConfidenceBadge({ label }: { label: string }) {
  return <span className={`stamp stamp-${label}`}>{label}</span>
}
