import { useEffect, useState } from 'react'
import {
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native'
import * as SecureStore from 'expo-secure-store'
import { getServerUrl } from '../api/client'
import { useAuth } from '../hooks/useAuth'

interface Props {
  onLogout: () => void
}

export default function SettingsScreen({ onLogout }: Props) {
  const { logout } = useAuth()
  const [serverUrl, setServerUrl] = useState('')
  const [username, setUsername] = useState('')

  useEffect(() => {
    getServerUrl().then(setServerUrl)
    SecureStore.getItemAsync('username').then((u) => setUsername(u ?? ''))
  }, [])

  const handleLogout = async () => {
    await logout()
    onLogout()
  }

  return (
    <View style={styles.container}>
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Account</Text>
        <View style={styles.row}>
          <Text style={styles.label}>Username</Text>
          <Text style={styles.value}>{username || '—'}</Text>
        </View>
        <View style={styles.row}>
          <Text style={styles.label}>Server</Text>
          <Text style={styles.value} numberOfLines={1}>{serverUrl}</Text>
        </View>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>App</Text>
        <View style={styles.row}>
          <Text style={styles.label}>Version</Text>
          <Text style={styles.value}>1.0.0</Text>
        </View>
      </View>

      <TouchableOpacity style={styles.logoutBtn} onPress={handleLogout}>
        <Text style={styles.logoutText}>Sign Out</Text>
      </TouchableOpacity>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f1f5f9', padding: 16 },
  section: {
    backgroundColor: '#fff',
    borderRadius: 14,
    padding: 16,
    marginBottom: 16,
    shadowColor: '#000',
    shadowOpacity: 0.05,
    shadowRadius: 6,
    elevation: 2,
  },
  sectionTitle: {
    fontWeight: '700',
    fontSize: 13,
    color: '#475569',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginBottom: 12,
  },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#f1f5f9',
  },
  label: { color: '#64748b', fontSize: 14 },
  value: { color: '#1e293b', fontSize: 14, fontWeight: '500', maxWidth: '60%', textAlign: 'right' },
  logoutBtn: {
    backgroundColor: '#fef2f2',
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#fca5a5',
  },
  logoutText: { color: '#ef4444', fontWeight: '600', fontSize: 15 },
})
