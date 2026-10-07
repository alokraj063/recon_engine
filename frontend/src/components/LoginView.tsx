import { useEffect, useState, type FormEvent } from 'react'
import { LogIn, ShieldCheck } from 'lucide-react'
import { fetchProviders, signIn } from '../api'
import { ApiError, type AuthUser } from '../types'
import logo from '../assets/jouletowatts_logo.png'

interface Props {
  onSignedIn: (user: AuthUser) => void
  /** set when a live session ended under the user rather than them
   *  arriving cold — worth saying, because the page they were on vanished */
  expired?: boolean
}

/** Message for the code the backend sent. The server deliberately gives
 *  the same answer for an unknown address, a wrong password and a
 *  deactivated account, so this must not invent a distinction it was not
 *  told about. */
function messageFor(error: ApiError): string {
  switch (error.code) {
    case 'INVALID_CREDENTIALS':
      return 'Email or password is not correct.'
    case 'TOO_MANY_ATTEMPTS':
      return error.message
    case 'NETWORK':
      return 'Could not reach the backend.'
    default:
      return error.message || 'Sign-in failed.'
  }
}

/** Why an Okta sign-in came back without a session (?auth_error=<code>). */
const OKTA_ERRORS: Record<string, string> = {
  NO_ACCESS: 'Please contact Admin for access.',
  INACTIVE: 'This account is deactivated. Please contact Admin.',
  OKTA_NOT_CONFIGURED: 'Okta sign-in is not set up.',
  FAILED: 'Okta sign-in failed. Try again.',
}

/** Read the code once and drop it from the address bar, so a reload does
 *  not show the same message again. */
function takeAuthError(): string | null {
  const code = new URLSearchParams(window.location.search).get('auth_error')
  if (!code) return null
  window.history.replaceState(null, '', window.location.pathname + window.location.hash)
  return OKTA_ERRORS[code] ?? OKTA_ERRORS.FAILED
}

export function LoginView({ onSignedIn, expired }: Props) {
  const [okta, setOkta] = useState(false)
  const [oktaError] = useState<string | null>(takeAuthError)
  useEffect(() => {
    fetchProviders().then((p) => setOkta(p.okta)).catch(() => setOkta(false))
  }, [])

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      onSignedIn(await signIn(email, password))
    } catch (err) {
      setError(err instanceof ApiError ? messageFor(err) : 'Sign-in failed.')
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="login-head">
          <span className="logo-plate">
            <img className="logo-img" src={logo} alt="Joules to Watts" />
          </span>
          <h1>Recon <span className="amp">Alpha</span></h1>
        </div>

        {expired && !error && (
          <p className="login-note">Your session ended. Sign in to continue.</p>
        )}

        <label className="login-field">
          <span>Email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            autoFocus
            required
          />
        </label>

        <label className="login-field">
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>

        {(error || oktaError) && <p className="login-error" role="alert">{error ?? oktaError}</p>}

        <button className="btn-run btn-ic" type="submit" disabled={busy || !email || !password}>
          <LogIn size={15} strokeWidth={1.75} />
          {busy ? 'Signing in…' : 'Sign in'}
        </button>

        {okta && (
          <>
            <p className="login-or"><span>or</span></p>
            <a className="ui-btn btn-ic login-okta" href="/api/auth/okta/login">
              <ShieldCheck size={15} strokeWidth={1.75} />
              Sign in with Okta
            </a>
          </>
        )}

        <p className="login-foot">
          Contact your administrator for access.
        </p>
      </form>
    </div>
  )
}
