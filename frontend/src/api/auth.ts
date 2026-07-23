import api from './client'
import type { AuthTokens } from '../types'

export const authApi = {
  login: (username: string, password: string) =>
    api.post<AuthTokens>('/auth/login', { username, password }).then((r) => r.data),
  // No refresh_token argument: the server reads it from the httpOnly cookie
  // it set on login, so the browser never needs to hold or send it directly.
  refresh: () => api.post<AuthTokens>('/auth/refresh', {}).then((r) => r.data),
  logout: () => api.post('/auth/logout', {}).then((r) => r.data),
}
