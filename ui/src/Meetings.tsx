import { useCallback, useEffect, useMemo, useState } from 'react'
import { call, on } from './bridge'
import { clock, dateTime, dayLabel, duration, time } from './format'
import { Icon } from './icons'
import { RecordBar } from './RecordBar'
import type { Meeting, MeetingSummary, Segment, TranscriptStatus } from './types'

const STATUS: Record<TranscriptStatus, [string, string]> = {
  done: ['ok', 'Transcript ready'],
  transcribing: ['busy', 'Transcribing…'],
  failed: ['bad', 'Needs attention'],
  none: ['none', 'No transcript'],
}

function StatusPill({ status }: { status: TranscriptStatus }) {
  const [tone, text] = STATUS[status]
  return <span className={`pill ${tone}`}>{text}</span>
}

export function Meetings({ initialId }: { initialId?: string }) {
  const [list, setList] = useState<MeetingSummary[]>([])
  const [selected, setSelected] = useState<string | undefined>(initialId)
  const [query, setQuery] = useState('')

  const refresh = useCallback(() => {
    call('listMeetings').then((items) => {
      setList(items)
      setSelected((id) => id ?? items[0]?.id)
    })
  }, [])
  useEffect(() => { refresh(); return on('meetingsChanged', refresh) }, [refresh])

  const q = query.trim().toLowerCase()
  const shown = q ? list.filter((m) => `${m.id} ${m.preview}`.toLowerCase().includes(q)) : list
  const groups = useMemo(() => {
    const byDay = new Map<string, MeetingSummary[]>()
    for (const m of shown) byDay.set(dayLabel(m.startedAt), [...(byDay.get(dayLabel(m.startedAt)) ?? []), m])
    return [...byDay]
  }, [shown])

  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Meetings">
        <RecordBar />
        <div className="search">
          <Icon name="search" />
          <label htmlFor="q" className="visually-hidden">Search meetings</label>
          <input id="q" placeholder="Search meetings" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        {groups.map(([day, items]) => (
          <div key={day}>
            <div className="eyebrow">{day}</div>
            {items.map((m) => (
              <button key={m.id} className="meeting-item" aria-current={m.id === selected} onClick={() => setSelected(m.id)}>
                <div className="title"><span>{time(m.startedAt)} · {duration(m.durationSec)}</span><StatusPill status={m.status} /></div>
                <div className="preview">{m.preview || m.id}</div>
              </button>
            ))}
          </div>
        ))}
        {list.length > 0 && shown.length === 0 && <p className="muted small" style={{ padding: 8 }}>No meeting matches “{query}”.</p>}
      </nav>
      <main className="content">
        {selected ? <MeetingDetail key={selected} id={selected} query={q} /> : (
          <div className="empty">
            <h2>No meetings yet</h2>
            <p>Click Start recording to record your first meeting.</p>
          </div>
        )}
      </main>
    </div>
  )
}

function MeetingDetail({ id, query }: { id: string; query: string }) {
  const [meeting, setMeeting] = useState<Meeting>()
  const [copied, setCopied] = useState(false)

  const load = useCallback(() => { call('getMeeting', { id }).then(setMeeting) }, [id])
  useEffect(() => { load(); return on('meetingsChanged', load) }, [load])
  if (!meeting) return null

  const failed = meeting.segments.filter((s) => s.failed).length
  const transcribe = () => call('transcribe', { id }).then(load)
  const copy = async () => {
    await navigator.clipboard.writeText(meeting.transcriptText)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="detail">
      <div className="detail-main">
        <header className="detail-head">
          <div style={{ flex: 1, minWidth: 240 }}>
            <h1>{dateTime(meeting.startedAt)}</h1>
            <div className="meta">
              <span>{duration(meeting.durationSec)}</span>
              {meeting.model && <span>{meeting.model}</span>}
              {failed > 0 && <span style={{ color: 'var(--warn)' }}>{failed} part{failed > 1 ? 's' : ''} not transcribed</span>}
            </div>
          </div>
          <div className="actions">
            <button className="btn" onClick={() => call('openPath', { path: meeting.folder })}><Icon name="folder" />Show in Finder</button>
            {meeting.status === 'done' || meeting.status === 'failed' ? (
              <>
                <button className="btn" onClick={transcribe}><Icon name="refresh" />Transcribe again</button>
                <button className="btn primary" onClick={copy}><Icon name={copied ? 'check' : 'copy'} />{copied ? 'Copied' : 'Copy transcript'}</button>
              </>
            ) : null}
          </div>
        </header>
        <Transcript meeting={meeting} query={query} onTranscribe={transcribe} />
      </div>
      <aside className="files" aria-label="Recorded files">
        {meeting.files.some((f) => f.kind === 'display') && <div className="eyebrow" style={{ padding: 0 }}>Displays</div>}
        {meeting.files.filter((f) => f.kind === 'display').map((f) => (
          <button key={f.name} className="thumb" style={f.thumbnail ? { backgroundImage: `url(${f.thumbnail})` } : undefined}
            onClick={() => call('openPath', { path: f.path })} aria-label={`Play ${f.name}`}>
            <span className="play"><Icon name="play" /></span>
            <span className="label">{f.name} · {f.info}</span>
          </button>
        ))}
        <div className="eyebrow" style={{ padding: '8px 0 0' }}>Audio tracks</div>
        {meeting.files.filter((f) => f.kind === 'audio').map((f) => (
          <button key={f.name} className="track" onClick={() => call('openPath', { path: f.path })}>
            <span className={`dot ${f.name.startsWith('system') ? 'others' : ''}`} />
            <span style={{ flex: 1 }}><span style={{ fontWeight: 600, fontSize: 13 }}>{f.name}</span><br /><span className="small muted">{f.info}</span></span>
            <Icon name="play" />
          </button>
        ))}
      </aside>
    </div>
  )
}

function Transcript({ meeting, query, onTranscribe }: { meeting: Meeting; query: string; onTranscribe: () => void }) {
  if (meeting.status === 'transcribing') {
    return <div className="empty"><div className="spinner" /><h2>Transcribing…</h2><p>A one-hour meeting usually takes a minute or two.</p></div>
  }
  if (meeting.segments.length === 0) {
    return (
      <div className="empty">
        <h2>{meeting.status === 'failed' ? 'Transcription failed' : 'Not transcribed yet'}</h2>
        <p>{meeting.status === 'failed' ? 'The recording is safe. Check transcribe.log in the meeting folder, then try again.' : 'The recording is saved. Transcribe it now to read and search it.'}</p>
        <button className="btn primary" onClick={onTranscribe}>Transcribe now</button>
      </div>
    )
  }
  return (
    <section className="transcript" aria-label="Transcript">
      {meeting.segments.map((s, i) => <SegmentRow key={i} s={s} query={query} onRetry={onTranscribe} />)}
    </section>
  )
}

function SegmentRow({ s, query, onRetry }: { s: Segment; query: string; onRetry: () => void }) {
  return (
    <div className="segment">
      <div className="at">{clock(s.start)}</div>
      {s.failed ? (
        <div className="failed">
          <Icon name="warning" label="Warning" />
          <span style={{ flex: 1 }}>{s.speaker}: this part could not be transcribed ({clock(s.start)}–{clock(s.end)}). The audio is saved.</span>
          <button className="btn" onClick={onRetry}>Try again</button>
        </div>
      ) : (
        <div style={{ flex: 1 }}>
          <span className={`who ${s.speaker === 'Me' ? '' : 'others'}`}>{s.speaker}</span>
          <p lang="bn">{highlight(s.text, query)}</p>
        </div>
      )}
    </div>
  )
}

function highlight(text: string, query: string) {
  if (!query) return text
  const parts = text.split(new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi'))
  return parts.map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : part))
}
