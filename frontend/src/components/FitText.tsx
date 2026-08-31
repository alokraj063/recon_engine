import { useLayoutEffect, useRef, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** floor the font never shrinks below */
  minPx?: number
}

/** Shrinks its text to fit the parent's width instead of overflowing.
 *  No-op (inherited size) whenever the text already fits. */
export function FitText({ children, minPx = 14 }: Props) {
  const ref = useRef<HTMLSpanElement>(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const fit = () => {
      el.style.fontSize = ''
      const base = parseFloat(getComputedStyle(el).fontSize)
      const avail = el.clientWidth
      const needed = el.scrollWidth
      if (needed > avail && avail > 0) {
        el.style.fontSize = `${Math.max(minPx, Math.floor(base * (avail / needed)))}px`
      }
    }
    fit()
    const ro = new ResizeObserver(fit)
    if (el.parentElement) ro.observe(el.parentElement)
    return () => ro.disconnect()
  }, [children, minPx])

  return (
    <span ref={ref} style={{ display: 'block', whiteSpace: 'nowrap', overflow: 'hidden' }}>
      {children}
    </span>
  )
}
