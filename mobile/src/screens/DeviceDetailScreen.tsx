import {
  ActivityIndicator,
  Alert,
  Modal,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native'
import { useState } from 'react'
import { useRoute, type RouteProp } from '@react-navigation/native'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { formatDistanceToNow } from 'date-fns'
import api from '../api/client'
import { commandsApi, COMMAND_LIBRARY, type Command } from '../api/commands'
import CommandOutputModal from '../components/CommandOutputModal'

type RouteParams = {
  DeviceDetail: { deviceId: string; hostname: string }
}

type Tab = 'overview' | 'commands'

function Gauge({ label, value }: { label: string; value: number | null }) {
  if (value === null) return null
  const color = value > 85 ? '#ef4444' : value > 70 ? '#f59e0b' : '#22c55e'
  return (
    <View style={styles.gauge}>
      <Text style={styles.gaugeLabel}>{label}</Text>
      <View style={styles.gaugeTrack}>
        <View style={[styles.gaugeFill, { width: `${Math.min(value, 100)}%` as any, backgroundColor: color }]} />
      </View>
      <Text style={[styles.gaugeValue, { color }]}>{Math.round(value)}%</Text>
    </View>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {children}
    </View>
  )
}

const STATUS_COLOR: Record<string, string> = {
  pending: '#f59e0b',
  sent: '#3b82f6',
  running: '#8b5cf6',
  completed: '#22c55e',
  failed: '#ef4444',
  timeout: '#94a3b8',
}

function CommandRow({ cmd, onPress }: { cmd: Command; onPress: () => void }) {
  const color = STATUS_COLOR[cmd.status] ?? '#94a3b8'
  return (
    <TouchableOpacity onPress={onPress} style={styles.commandRow}>
      <View style={{ flex: 1 }}>
        <Text style={styles.commandType}>{cmd.command_type}</Text>
        <Text style={styles.commandTime}>
          {formatDistanceToNow(new Date(cmd.created_at), { addSuffix: true })}
        </Text>
      </View>
      <View style={[styles.statusPill, { backgroundColor: color + '22', borderColor: color }]}>
        <Text style={[styles.statusPillText, { color }]}>{cmd.status}</Text>
      </View>
    </TouchableOpacity>
  )
}

function CommandLibrarySheet({
  visible,
  onClose,
  onDispatch,
}: {
  visible: boolean
  onClose: () => void
  onDispatch: (type: string, dangerous: boolean) => void
}) {
  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <View style={styles.sheetContainer}>
        <View style={styles.sheetHeader}>
          <Text style={styles.sheetTitle}>Command Library</Text>
          <TouchableOpacity onPress={onClose} hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}>
            <Text style={styles.closeBtn}>Cancel</Text>
          </TouchableOpacity>
        </View>
        <ScrollView contentContainerStyle={styles.sheetContent}>
          {COMMAND_LIBRARY.map((item) => (
            <TouchableOpacity
              key={item.type}
              style={styles.libraryRow}
              onPress={() => onDispatch(item.type, item.dangerous ?? false)}
            >
              <View style={{ flex: 1 }}>
                <Text style={[styles.libraryLabel, item.dangerous && styles.dangerText]}>{item.label}</Text>
                <Text style={styles.libraryDesc}>{item.description}</Text>
              </View>
              {item.dangerous && <Text style={styles.dangerBadge}>DANGER</Text>}
            </TouchableOpacity>
          ))}
        </ScrollView>
      </View>
    </Modal>
  )
}

export default function DeviceDetailScreen() {
  const route = useRoute<RouteProp<RouteParams, 'DeviceDetail'>>()
  const { deviceId } = route.params
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<Tab>('overview')
  const [selectedCommandId, setSelectedCommandId] = useState<string | null>(null)
  const [showLibrary, setShowLibrary] = useState(false)

  const { data: device, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['mobile-device', deviceId],
    queryFn: () => api.get(`/devices/${deviceId}`).then((r) => r.data),
    refetchInterval: 30000,
  })

  const { data: metric } = useQuery({
    queryKey: ['mobile-metric', deviceId],
    queryFn: () => api.get(`/metrics/${deviceId}/latest`).then((r) => r.data),
    refetchInterval: 30000,
  })

  const { data: alerts = [] } = useQuery({
    queryKey: ['mobile-alerts', deviceId],
    queryFn: () => api.get('/alerts', { params: { device_id: deviceId, resolved: false, limit: 20 } }).then((r) => r.data),
  })

  const { data: commands = [], refetch: refetchCmds } = useQuery<Command[]>({
    queryKey: ['mobile-commands', deviceId],
    queryFn: () => commandsApi.list(deviceId, 30),
    refetchInterval: tab === 'commands' ? 5000 : false,
  })

  const dispatch = useMutation({
    mutationFn: ({ type, payload }: { type: string; payload?: Record<string, unknown> }) =>
      commandsApi.dispatch(deviceId, type, payload ?? {}),
    onSuccess: (cmd) => {
      queryClient.invalidateQueries({ queryKey: ['mobile-commands', deviceId] })
      setShowLibrary(false)
      setSelectedCommandId(cmd.id)
    },
    onError: () => Alert.alert('Command failed', 'The command could not be dispatched. Check your connection and try again.'),
  })

  const handleDispatch = (type: string, dangerous: boolean) => {
    if (dangerous) {
      const item = COMMAND_LIBRARY.find((c) => c.type === type)
      Alert.alert(item?.label ?? type, `Are you sure you want to send "${item?.label ?? type}" to this device?`, [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Send', style: 'destructive', onPress: () => dispatch.mutate({ type }) },
      ])
    } else {
      dispatch.mutate({ type })
    }
  }

  if (isLoading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#2563eb" />
      </View>
    )
  }

  if (isError || !device) {
    return (
      <View style={styles.center}>
        <Text style={styles.emptyText}>Couldn't load this device. Check your connection and try again.</Text>
        <TouchableOpacity style={styles.retryButton} onPress={() => refetch()}>
          <Text style={styles.retryButtonText}>Retry</Text>
        </TouchableOpacity>
      </View>
    )
  }

  return (
    <View style={{ flex: 1, backgroundColor: '#f1f5f9' }}>
      {/* Tab bar */}
      <View style={styles.tabBar}>
        <TouchableOpacity
          style={[styles.tabBtn, tab === 'overview' && styles.tabBtnActive]}
          onPress={() => setTab('overview')}
        >
          <Text style={[styles.tabBtnText, tab === 'overview' && styles.tabBtnTextActive]}>Overview</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.tabBtn, tab === 'commands' && styles.tabBtnActive]}
          onPress={() => setTab('commands')}
        >
          <Text style={[styles.tabBtnText, tab === 'commands' && styles.tabBtnTextActive]}>
            Commands {commands.length > 0 ? `(${commands.length})` : ''}
          </Text>
        </TouchableOpacity>
      </View>

      {tab === 'overview' ? (
        <ScrollView
          refreshControl={<RefreshControl refreshing={isFetching} onRefresh={refetch} colors={['#2563eb']} />}
        >
          <Section title="System">
            <Gauge label="CPU" value={metric?.cpu_percent ?? null} />
            <Gauge label="RAM" value={metric?.ram_percent ?? null} />
            <Gauge label="Disk" value={metric?.disk_percent ?? null} />
            <Text style={styles.meta}>Last seen: {device?.last_seen
              ? formatDistanceToNow(new Date(device.last_seen), { addSuffix: true })
              : '—'}</Text>
            {device?.agent_version && (
              <Text style={styles.meta}>Agent: v{device.agent_version}</Text>
            )}
          </Section>

          {alerts.length > 0 && (
            <Section title={`Alerts (${alerts.length})`}>
              {alerts.map((alert: any) => (
                <View key={alert.id} style={[styles.alertRow, alert.severity === 'critical' && styles.alertCritical]}>
                  <Text style={styles.alertSeverity}>{alert.severity.toUpperCase()}</Text>
                  <Text style={styles.alertMessage} numberOfLines={2}>{alert.message}</Text>
                </View>
              ))}
            </Section>
          )}

          <Section title="Quick Actions">
            <View style={styles.actions}>
              <TouchableOpacity style={styles.actionBtn} onPress={() => handleDispatch('check_updates', false)}>
                <Text style={styles.actionBtnText}>Check Updates</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.actionBtn} onPress={() => handleDispatch('sync_time', false)}>
                <Text style={styles.actionBtnText}>Sync Time</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.actionBtn, styles.actionBtnDanger]}
                onPress={() => handleDispatch('reboot', true)}
              >
                <Text style={[styles.actionBtnText, { color: '#ef4444' }]}>Reboot</Text>
              </TouchableOpacity>
            </View>
          </Section>
        </ScrollView>
      ) : (
        <View style={{ flex: 1 }}>
          <ScrollView
            refreshControl={<RefreshControl refreshing={isFetching} onRefresh={refetchCmds} colors={['#2563eb']} />}
            contentContainerStyle={{ paddingVertical: 12 }}
          >
            {commands.length === 0 && (
              <Text style={styles.emptyText}>No commands yet. Tap + to dispatch one.</Text>
            )}
            {commands.map((cmd) => (
              <CommandRow key={cmd.id} cmd={cmd} onPress={() => setSelectedCommandId(cmd.id)} />
            ))}
          </ScrollView>

          {/* Floating action button */}
          <TouchableOpacity style={styles.fab} onPress={() => setShowLibrary(true)}>
            <Text style={styles.fabText}>+</Text>
          </TouchableOpacity>
        </View>
      )}

      <CommandOutputModal
        commandId={selectedCommandId}
        onClose={() => setSelectedCommandId(null)}
      />

      <CommandLibrarySheet
        visible={showLibrary}
        onClose={() => setShowLibrary(false)}
        onDispatch={handleDispatch}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 },
  retryButton: { backgroundColor: '#2563eb', borderRadius: 10, paddingHorizontal: 20, paddingVertical: 10, marginTop: 14 },
  retryButtonText: { color: '#fff', fontWeight: '600', fontSize: 14 },
  tabBar: {
    flexDirection: 'row',
    backgroundColor: '#fff',
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#e2e8f0',
  },
  tabBtn: { flex: 1, paddingVertical: 12, alignItems: 'center' },
  tabBtnActive: { borderBottomWidth: 2, borderBottomColor: '#2563eb' },
  tabBtnText: { fontSize: 14, color: '#64748b', fontWeight: '500' },
  tabBtnTextActive: { color: '#2563eb', fontWeight: '600' },
  section: {
    margin: 16,
    marginBottom: 0,
    backgroundColor: '#fff',
    borderRadius: 14,
    padding: 16,
    shadowColor: '#000',
    shadowOpacity: 0.05,
    shadowRadius: 6,
    elevation: 2,
  },
  sectionTitle: { fontWeight: '700', fontSize: 14, color: '#475569', marginBottom: 12, textTransform: 'uppercase', letterSpacing: 0.5 },
  gauge: { marginBottom: 10 },
  gaugeLabel: { fontSize: 12, color: '#64748b', marginBottom: 4 },
  gaugeTrack: { height: 8, backgroundColor: '#e2e8f0', borderRadius: 4, overflow: 'hidden', marginBottom: 2 },
  gaugeFill: { height: 8, borderRadius: 4 },
  gaugeValue: { fontSize: 13, fontWeight: '600', textAlign: 'right' },
  meta: { color: '#94a3b8', fontSize: 12, marginTop: 4 },
  alertRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    paddingVertical: 6,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#f1f5f9',
  },
  alertCritical: { backgroundColor: '#fef2f2', borderRadius: 8, paddingHorizontal: 8 },
  alertSeverity: { fontSize: 10, fontWeight: '700', color: '#ef4444', paddingTop: 1, minWidth: 60 },
  alertMessage: { flex: 1, fontSize: 13, color: '#374151' },
  actions: { flexDirection: 'row', gap: 8, flexWrap: 'wrap' },
  actionBtn: {
    flex: 1,
    minWidth: '30%',
    borderWidth: 1,
    borderColor: '#e2e8f0',
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: 'center',
  },
  actionBtnDanger: { borderColor: '#fca5a5' },
  actionBtnText: { fontSize: 13, fontWeight: '500', color: '#374151' },
  commandRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
    marginHorizontal: 16,
    marginBottom: 8,
    padding: 14,
    borderRadius: 12,
    shadowColor: '#000',
    shadowOpacity: 0.04,
    shadowRadius: 4,
    elevation: 1,
  },
  commandType: { fontSize: 14, color: '#1e293b', fontWeight: '500' },
  commandTime: { fontSize: 11, color: '#94a3b8', marginTop: 2 },
  statusPill: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 99,
    borderWidth: 1,
  },
  statusPillText: { fontSize: 11, fontWeight: '600' },
  emptyText: { textAlign: 'center', color: '#94a3b8', marginTop: 40, fontSize: 14 },
  fab: {
    position: 'absolute',
    bottom: 24,
    right: 24,
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: '#2563eb',
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#2563eb',
    shadowOpacity: 0.4,
    shadowRadius: 8,
    elevation: 6,
  },
  fabText: { color: '#fff', fontSize: 28, lineHeight: 32, fontWeight: '300' },
  sheetContainer: { flex: 1, backgroundColor: '#fff' },
  sheetHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 20,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#e2e8f0',
  },
  sheetTitle: { fontSize: 17, fontWeight: '600', color: '#0f172a' },
  closeBtn: { fontSize: 16, color: '#2563eb', fontWeight: '500' },
  sheetContent: { padding: 16, gap: 4 },
  libraryRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 14,
    paddingHorizontal: 4,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#f1f5f9',
  },
  libraryLabel: { fontSize: 15, fontWeight: '500', color: '#1e293b', marginBottom: 2 },
  libraryDesc: { fontSize: 12, color: '#94a3b8' },
  dangerText: { color: '#ef4444' },
  dangerBadge: {
    fontSize: 10,
    fontWeight: '700',
    color: '#ef4444',
    backgroundColor: '#fef2f2',
    paddingHorizontal: 6,
    paddingVertical: 3,
    borderRadius: 6,
  },
})
