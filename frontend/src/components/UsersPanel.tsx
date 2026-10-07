import { useEffect, useState } from 'react'
import { Plus } from 'lucide-react'
import { ApiError, type AdminUser, type Role } from '../types'
import { createUser, fetchUsers, updateUser } from '../api'
import { useAuth } from '../auth'
import { Notice } from './ui'

const ROLES: Role[] = ['admin', 'analyst', 'viewer']

const when = (iso: string | null) => (iso ? iso.slice(0, 16).replace('T', ' ') : 'never')

/**
 * Who may sign in and with which role. A person signs in with Okta (or a
 * password, if one was set); they must be listed here with a role — an
 * Okta user who is not gets "Please contact Admin for access". Changes
 * apply on the person's next request.
 */
export function UsersPanel() {
  const me = useAuth().user
  const [users, setUsers] = useState<AdminUser[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | 'add' | null>(null)
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [role, setRole] = useState<Role>('analyst')

  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : String(e))

  useEffect(() => {
    fetchUsers().then(setUsers).catch(fail)
  }, [])

  const patch = async (u: AdminUser, change: { role?: Role; is_active?: boolean }) => {
    setBusy(u.id)
    setError(null)
    try {
      const next = await updateUser(u.id, change)
      setUsers((prev) => (prev ?? []).map((x) => (x.id === u.id ? next : x)))
    } catch (e) {
      fail(e)
    } finally {
      setBusy(null)
    }
  }

  const add = async () => {
    setBusy('add')
    setError(null)
    try {
      const created = await createUser(email.trim(), name.trim(), role)
      setUsers((prev) => [...(prev ?? []), created].sort((a, b) => a.email.localeCompare(b.email)))
      setEmail('')
      setName('')
    } catch (e) {
      fail(e)
    } finally {
      setBusy(null)
    }
  }

  if (!users) {
    return error
      ? <Notice tone="error">Could not load users: {error}</Notice>
      : <div className="dt-loading"><span className="quill" /> Loading…</div>
  }

  return (
    <div className="config-panel">
      <div className="config-section">
        <h3 className="ledger-h">Users</h3>
        <p className="hint">Admin: everything · Analyst: ingest, reconcile, decide, export · Viewer: read-only.</p>
        <table className="data users-table">
          <thead>
            <tr><th>Email</th><th>Name</th><th>Role</th><th>Sign-in</th><th>Last sign-in</th><th>Active</th></tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className={u.is_active ? '' : 'is-off'}>
                <td>{u.email}</td>
                <td>{u.name}</td>
                <td>
                  <select value={u.role ?? ''} disabled={busy === u.id}
                          onChange={(e) => void patch(u, { role: e.target.value as Role })}>
                    {u.role === null && <option value="" disabled>No role</option>}
                    {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                  </select>
                </td>
                <td>{[u.okta_linked ? 'Okta' : null, u.has_password ? 'Password' : null]
                  .filter(Boolean).join(' · ') || 'Not yet'}</td>
                <td>{when(u.last_login_at)}</td>
                <td>
                  <input type="checkbox" checked={u.is_active}
                         disabled={busy === u.id || u.id === me.id}
                         title={u.id === me.id ? 'You cannot deactivate yourself' : undefined}
                         onChange={(e) => void patch(u, { is_active: e.target.checked })} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="users-add">
          <input type="email" placeholder="Email" value={email} disabled={busy === 'add'}
                 onChange={(e) => setEmail(e.target.value)} />
          <input placeholder="Name (optional)" value={name} disabled={busy === 'add'}
                 onChange={(e) => setName(e.target.value)} />
          <select value={role} disabled={busy === 'add'} onChange={(e) => setRole(e.target.value as Role)}>
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <button type="button" className="ui-btn ui-btn-primary btn-ic"
                  disabled={busy === 'add' || !email.includes('@')} onClick={() => void add()}>
            <Plus size={13} strokeWidth={2} /> Add user
          </button>
        </div>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
    </div>
  )
}
