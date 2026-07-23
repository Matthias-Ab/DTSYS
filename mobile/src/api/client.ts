import axios, { type InternalAxiosRequestConfig } from 'axios'
import * as SecureStore from 'expo-secure-store'

const STORAGE_KEY_TOKEN = 'access_token'
const STORAGE_KEY_REFRESH = 'refresh_token'
const STORAGE_KEY_SERVER = 'server_url'

export async function getServerUrl(): Promise<string> {
  // No hardcoded default: the user always enters their own server address on
  // the login screen, which warns before submitting over plain http://.
  return (await SecureStore.getItemAsync(STORAGE_KEY_SERVER)) ?? ''
}

export async function getAccessToken(): Promise<string | null> {
  return SecureStore.getItemAsync(STORAGE_KEY_TOKEN)
}

export async function saveTokens(access: string, refresh: string): Promise<void> {
  await Promise.all([
    SecureStore.setItemAsync(STORAGE_KEY_TOKEN, access),
    SecureStore.setItemAsync(STORAGE_KEY_REFRESH, refresh),
  ])
}

export async function saveServerUrl(url: string): Promise<void> {
  await SecureStore.setItemAsync(STORAGE_KEY_SERVER, url.replace(/\/$/, ''))
}

export async function clearTokens(): Promise<void> {
  await Promise.all([
    SecureStore.deleteItemAsync(STORAGE_KEY_TOKEN),
    SecureStore.deleteItemAsync(STORAGE_KEY_REFRESH),
  ])
}

const api = axios.create({ timeout: 15000 })

api.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
  const [serverUrl, token] = await Promise.all([getServerUrl(), getAccessToken()])
  config.baseURL = `${serverUrl}/api/v1`
  if (token) {
    config.headers = config.headers ?? {}
    config.headers['Authorization'] = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config
    if (error.response?.status === 401 && !original._retry) {
      original._retry = true
      try {
        const refresh = await SecureStore.getItemAsync(STORAGE_KEY_REFRESH)
        if (!refresh) throw new Error('No refresh token')
        const serverUrl = await getServerUrl()
        const resp = await axios.post(`${serverUrl}/api/v1/auth/refresh`, { refresh_token: refresh })
        await saveTokens(resp.data.access_token, resp.data.refresh_token)
        original.headers['Authorization'] = `Bearer ${resp.data.access_token}`
        return api(original)
      } catch {
        await clearTokens()
        // Let the caller handle the 401
      }
    }
    return Promise.reject(error)
  }
)

export default api
