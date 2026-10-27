export function clock(seconds: number): string {
  const s = Math.floor(seconds)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`
}

export function duration(seconds: number): string {
  const s = Math.round(seconds)
  if (s < 60) return `${s} sec`
  const h = Math.floor(s / 3600)
  const m = Math.round((s % 3600) / 60)
  return h ? `${h} h ${m} min` : `${m} min`
}

export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function time(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

/** "Today", "Yesterday" or a date, for grouping the meeting list. */
export function dayLabel(iso: string): string {
  const day = new Date(iso)
  const today = new Date()
  const diff = Math.round((new Date(today.toDateString()).getTime() - new Date(day.toDateString()).getTime()) / 86_400_000)
  if (diff === 0) return 'Today'
  if (diff === 1) return 'Yesterday'
  return day.toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })
}
