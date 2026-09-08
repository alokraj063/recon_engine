import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { createPortal } from 'react-dom'

/**
 * A small panel anchored under a button, rendered through a portal into
 * document.body so it is never clipped by a sticky table header or an
 * overflow-x:auto scroller. Closes on outside mousedown and Escape.
 * Clicks inside are stopped so a sortable <th> (React-tree ancestor of a
 * portal) does not toggle its sort.
 */
export function FilterPopover({ anchorRef, open, onClose, children, width = 220 }: {
  anchorRef: RefObject<HTMLElement | null>
  open: boolean
  onClose: () => void
  children: React.ReactNode
  width?: number
}) {
  const panel = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)

  useLayoutEffect(() => {
    if (!open || !anchorRef.current) return
    const r = anchorRef.current.getBoundingClientRect()
    let left = r.left + window.scrollX
    if (r.left + width > window.innerWidth - 8) left = r.right + window.scrollX - width
    setPos({ top: r.bottom + window.scrollY + 6, left: Math.max(8, left) })
  }, [open, anchorRef, width])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (panel.current?.contains(t) || anchorRef.current?.contains(t)) return
      onClose()
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, onClose, anchorRef])

  if (!open || !pos) return null
  return createPortal(
    <div ref={panel} className="filter-pop" style={{ top: pos.top, left: pos.left, width }}
         onClick={(e) => e.stopPropagation()}
         onMouseDown={(e) => e.stopPropagation()}>
      {children}
    </div>,
    document.body,
  )
}
