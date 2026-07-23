import { useMemo, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Shield, Trash2, UserPlus, Users as UsersIcon } from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import { adminApi } from '../api/admin'
import { useAuthStore } from '../store/authStore'

function extractErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'response' in error) {
    const detail = (error as { response?: { data?: { detail?: string } } }).response?.data?.detail
    if (typeof detail === 'string') return detail
  }
  return fallback
}

export default function Users() {
  const queryClient = useQueryClient()
  const role = useAuthStore((state) => state.role)
  const [inviteOpen, setInviteOpen] = useState(false)
  const [passwordTarget, setPasswordTarget] = useState<string | null>(null)
  const [passwordValue, setPasswordValue] = useState('')
  const [inviteForm, setInviteForm] = useState({ username: '', password: '', role: 'viewer' as 'admin' | 'viewer' })
  const [actionError, setActionError] = useState<string | null>(null)

  const { data: users = [] } = useQuery({
    queryKey: ['admin-users'],
    queryFn: adminApi.users,
    enabled: role === 'admin',
  })

  const refreshUsers = () => queryClient.invalidateQueries({ queryKey: ['admin-users'] })

  const inviteUser = useMutation({
    mutationFn: () => adminApi.createUser(inviteForm),
    onSuccess: () => {
      setActionError(null)
      setInviteOpen(false)
      setInviteForm({ username: '', password: '', role: 'viewer' })
      refreshUsers()
    },
    onError: (error) => setActionError(extractErrorMessage(error, 'Could not create the user.')),
  })
  const updateUser = useMutation({
    mutationFn: ({ id, body }: { id: string; body: { role?: 'admin' | 'viewer'; is_active?: boolean } }) =>
      adminApi.updateUser(id, body),
    onSuccess: () => {
      setActionError(null)
      refreshUsers()
    },
    onError: (error) => setActionError(extractErrorMessage(error, 'Could not update the user.')),
  })
  const resetPassword = useMutation({
    mutationFn: ({ id, password }: { id: string; password: string }) => adminApi.resetUserPassword(id, password),
    onSuccess: () => {
      setActionError(null)
      setPasswordTarget(null)
      setPasswordValue('')
      refreshUsers()
    },
    onError: (error) => setActionError(extractErrorMessage(error, 'Could not reset the password.')),
  })
  const deleteUser = useMutation({
    mutationFn: (id: string) => adminApi.deleteUser(id),
    onSuccess: () => {
      setActionError(null)
      refreshUsers()
    },
    onError: (error) => setActionError(extractErrorMessage(error, 'Could not delete the user.')),
  })

  const currentUsername = useAuthStore((state) => state.username)
  const rows = useMemo(() => users, [users])

  if (role !== 'admin') {
    return <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-400">Admin access required.</div>
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-900 dark:text-gray-100">Users</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-gray-500">Manage local DTSYS accounts and roles.</p>
        </div>
        <button
          onClick={() => setInviteOpen(true)}
          className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-500"
        >
          <UserPlus size={16} />
          Invite User
        </button>
      </div>

      {actionError && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-950/30 dark:text-red-300">
          <span>{actionError}</span>
          <button onClick={() => setActionError(null)} className="text-red-500 hover:text-red-700 dark:text-red-400 dark:hover:text-red-200">
            Dismiss
          </button>
        </div>
      )}

      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white dark:border-gray-800 dark:bg-gray-900">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 text-left text-slate-500 dark:bg-gray-950/60 dark:text-gray-400">
            <tr>
              <th className="px-4 py-3">Username</th>
              <th className="px-4 py-3">Role</th>
              <th className="px-4 py-3">Last Login</th>
              <th className="px-4 py-3">Created</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((account) => {
              const isSelf = account.username === currentUsername
              return (
                <tr
                  key={account.id}
                  className={`border-t border-slate-200 dark:border-gray-800 ${isSelf ? 'bg-blue-50/70 dark:bg-blue-950/20' : ''}`}
                >
                  <td className="px-4 py-3 font-medium text-slate-900 dark:text-gray-100">
                    <div className="flex items-center gap-2">
                      <UsersIcon size={15} className="text-slate-400 dark:text-gray-500" />
                      {account.username}
                      {isSelf && <span className="rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-700 dark:bg-blue-500/20 dark:text-blue-300">You</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <button
                      disabled={isSelf}
                      onClick={() => updateUser.mutate({ id: account.id, body: { role: account.role === 'admin' ? 'viewer' : 'admin' } })}
                      className="rounded-full border border-slate-200 px-2.5 py-1 text-xs text-slate-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:text-gray-300"
                    >
                      {account.role}
                    </button>
                  </td>
                  <td className="px-4 py-3 text-slate-600 dark:text-gray-300">
                    {account.last_login ? formatDistanceToNow(new Date(account.last_login), { addSuffix: true }) : 'Never'}
                  </td>
                  <td className="px-4 py-3 text-slate-600 dark:text-gray-300">
                    {account.created_at ? formatDistanceToNow(new Date(account.created_at), { addSuffix: true }) : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <button
                      disabled={isSelf}
                      onClick={() => updateUser.mutate({ id: account.id, body: { is_active: !account.is_active } })}
                      className={`rounded-full px-2.5 py-1 text-xs ${account.is_active ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300' : 'bg-slate-200 text-slate-600 dark:bg-gray-800 dark:text-gray-400'} disabled:cursor-not-allowed disabled:opacity-50`}
                    >
                      {account.is_active ? 'Active' : 'Inactive'}
                    </button>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-2">
                      <button
                        onClick={() => {
                          setPasswordTarget(account.id)
                          setPasswordValue('')
                        }}
                        className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-700 dark:border-gray-700 dark:text-gray-300"
                      >
                        Reset Password
                      </button>
                      <button
                        disabled={isSelf}
                        onClick={() => window.confirm(`Deactivate ${account.username}?`) && deleteUser.mutate(account.id)}
                        className="rounded-lg border border-red-200 px-3 py-1.5 text-xs text-red-600 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-500/30 dark:text-red-300"
                      >
                        <span className="inline-flex items-center gap-1">
                          <Trash2 size={12} />
                          Delete
                        </span>
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {inviteOpen && (
        <Modal title="Invite User" onClose={() => setInviteOpen(false)}>
          <div className="space-y-3">
            <input
              value={inviteForm.username}
              onChange={(event) => setInviteForm((current) => ({ ...current, username: event.target.value }))}
              placeholder="Username"
              className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
            />
            <input
              value={inviteForm.password}
              onChange={(event) => setInviteForm((current) => ({ ...current, password: event.target.value }))}
              placeholder="Temporary password"
              className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
            />
            <select
              value={inviteForm.role}
              onChange={(event) => setInviteForm((current) => ({ ...current, role: event.target.value as 'admin' | 'viewer' }))}
              className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
            >
              <option value="viewer">Viewer</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <button onClick={() => setInviteOpen(false)} className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 dark:border-gray-700 dark:text-gray-300">Cancel</button>
            <button
              onClick={() => inviteUser.mutate()}
              disabled={!inviteForm.username.trim() || !inviteForm.password.trim()}
              className="rounded-lg bg-blue-600 px-3 py-2 text-sm text-white disabled:opacity-50"
            >
              Create User
            </button>
          </div>
        </Modal>
      )}

      {passwordTarget && (
        <Modal title="Reset Password" onClose={() => setPasswordTarget(null)}>
          <input
            value={passwordValue}
            onChange={(event) => setPasswordValue(event.target.value)}
            placeholder="New password"
            className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
          />
          <div className="mt-4 flex justify-end gap-2">
            <button onClick={() => setPasswordTarget(null)} className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 dark:border-gray-700 dark:text-gray-300">Cancel</button>
            <button
              onClick={() => resetPassword.mutate({ id: passwordTarget, password: passwordValue })}
              disabled={passwordValue.length < 8}
              className="rounded-lg bg-blue-600 px-3 py-2 text-sm text-white disabled:opacity-50"
            >
              Save Password
            </button>
          </div>
        </Modal>
      )}
    </div>
  )
}

function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5 shadow-2xl dark:border-gray-800 dark:bg-gray-900">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Shield size={16} className="text-blue-500" />
            <h2 className="text-base font-semibold text-slate-900 dark:text-gray-100">{title}</h2>
          </div>
          <button onClick={onClose} className="text-sm text-slate-500 dark:text-gray-400">Close</button>
        </div>
        {children}
      </div>
    </div>
  )
}
