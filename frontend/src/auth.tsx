import {
  createContext, useCallback, useContext, useEffect, useState, type ReactNode,
} from 'react'
import { fetchMe, onSessionEnded, signOut as apiSignOut } from './api'
import { LoginView } from './components/LoginView'
import type { AuthUser } from './types'

/**
 * The login gate, wrapped AROUND <App/> rather than built into it.
 *
 * App.tsx holds a lot of state and effects that all call the API on
 * mount; running any of them before there is a session would just fire a
 * volley of 401s. Gating at the root means the app tree does not exist
 * until someone is signed in, so nothing inside App had to learn that
 * authentication happened — the one change there is a sign-out button in
 * the sidebar, which reads this context.
 *
 * There is no token to keep: the session is an httponly cookie the
 * browser attaches by itself. "Am I signed in" is therefore a question
 * only the server can answer, which is what the /api/auth/me call on
 * mount is for — and why nothing here is cached in localStorage, where it
 * could disagree with the cookie.
 */

interface AuthState {
  user: AuthUser
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthGate>')
  return ctx
}

type Status = 'checking' | 'anonymous' | 'signed-in'

export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>('checking')
  const [user, setUser] = useState<AuthUser | null>(null)
  // distinguishes "a session just ended" from "arrived signed out", so the
  // login screen can say which happened
  const [expired, setExpired] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetchMe()
      .then((me) => {
        if (cancelled) return
        setUser(me)
        setStatus('signed-in')
      })
      .catch(() => {
        // 401 (not signed in) and a network failure land in the same
        // place on purpose: either way the only thing to show is the
        // login form, which will report a backend that is still down
        if (!cancelled) setStatus('anonymous')
      })
    return () => { cancelled = true }
  }, [])

  // any 401 from anywhere in the app (api.ts announces it once)
  useEffect(() => onSessionEnded(() => {
    setStatus((prev) => {
      if (prev === 'signed-in') setExpired(true)
      return 'anonymous'
    })
    setUser(null)
  }), [])

  const signOut = useCallback(async () => {
    try {
      await apiSignOut()
    } finally {
      // whatever the server said, this browser is done with the session
      setUser(null)
      setExpired(false)
      setStatus('anonymous')
    }
  }, [])

  if (status === 'checking') {
    return <div className="login-page"><p className="login-checking">Loading…</p></div>
  }

  if (status !== 'signed-in' || !user) {
    return (
      <LoginView
        expired={expired}
        onSignedIn={(me) => {
          setUser(me)
          setExpired(false)
          setStatus('signed-in')
        }}
      />
    )
  }

  return (
    <AuthContext.Provider value={{ user, signOut }}>
      {children}
    </AuthContext.Provider>
  )
}
