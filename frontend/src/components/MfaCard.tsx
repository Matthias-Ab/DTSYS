import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { authApi } from '../api/auth'

const inputClass =
  'w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100'

function errorMessage(err: unknown) {
  return (err as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? 'Something went wrong'
}

export default function MfaCard() {
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ['mfa-status'], queryFn: authApi.mfaStatus })
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null)
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  const done = () => {
    setSetup(null)
    setCode('')
    setPassword('')
    setError('')
    queryClient.invalidateQueries({ queryKey: ['mfa-status'] })
  }

  const start = useMutation({ mutationFn: authApi.mfaSetup, onSuccess: (r) => { setSetup(r); setError('') }, onError: (e) => setError(errorMessage(e)) })
  const enable = useMutation({ mutationFn: () => authApi.mfaEnable(code), onSuccess: done, onError: (e) => setError(errorMessage(e)) })
  const disable = useMutation({ mutationFn: () => authApi.mfaDisable(password, code), onSuccess: done, onError: (e) => setError(errorMessage(e)) })

  const enabled = data?.mfa_enabled ?? false

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 dark:border-gray-800 dark:bg-gray-900">
      <h2 className="mb-1 text-sm font-semibold text-slate-900 dark:text-gray-200">Two-Factor Authentication</h2>
      <p className="mb-4 text-xs text-slate-500 dark:text-gray-500">
        {enabled ? 'Enabled — a code from your authenticator app is required at sign-in.' : 'Protect your account with a time-based code from an authenticator app.'}
      </p>
      {error && <p className="mb-3 text-xs text-red-500">{error}</p>}

      {!enabled && !setup && (
        <button onClick={() => start.mutate()} className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-500">
          Set up
        </button>
      )}

      {!enabled && setup && (
        <div className="max-w-sm space-y-3">
          <p className="text-xs text-slate-500 dark:text-gray-400">
            Add this key to your authenticator app (manual entry, time-based), then enter the 6-digit code it shows.
          </p>
          <code className="block break-all rounded-lg bg-slate-100 px-3 py-2 font-mono text-sm text-green-600 dark:bg-gray-800 dark:text-green-400">{setup.secret}</code>
          <a href={setup.otpauth_uri} className="block text-xs text-blue-500 hover:underline">Open in authenticator app</a>
          <input className={inputClass} inputMode="numeric" maxLength={6} placeholder="6-digit code" value={code} onChange={(e) => setCode(e.target.value)} />
          <div className="flex gap-2">
            <button onClick={() => enable.mutate()} disabled={code.length !== 6} className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-500 disabled:opacity-50">
              Enable
            </button>
            <button onClick={done} className="rounded-lg border border-slate-200 px-4 py-2 text-sm dark:border-gray-700">Cancel</button>
          </div>
        </div>
      )}

      {enabled && (
        <div className="max-w-sm space-y-3">
          <input className={inputClass} type="password" placeholder="Current password" value={password} onChange={(e) => setPassword(e.target.value)} />
          <input className={inputClass} inputMode="numeric" maxLength={6} placeholder="6-digit code" value={code} onChange={(e) => setCode(e.target.value)} />
          <button onClick={() => disable.mutate()} disabled={!password || code.length !== 6} className="rounded-lg bg-red-600 px-4 py-2 text-sm text-white hover:bg-red-500 disabled:opacity-50">
            Disable
          </button>
        </div>
      )}
    </section>
  )
}
