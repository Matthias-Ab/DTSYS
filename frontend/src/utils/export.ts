// Cells starting with = + - @ (or tab/CR) are executed as formulas by Excel/Sheets.
// Device hostnames, usernames and software names are attacker-influenced, so
// prefix a quote to force them to be treated as text.
export function escapeCSV(value: string) {
  const safe = /^[=+\-@\t\r]/.test(value) ? `'${value}` : value
  return `"${safe.replace(/"/g, '""')}"`
}

export function exportToCSV(filename: string, headers: string[], rows: string[][]): void {
  const csv = [headers.map(escapeCSV).join(','), ...rows.map((row) => row.map((cell) => escapeCSV(cell)).join(','))].join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export function exportToJSON(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
