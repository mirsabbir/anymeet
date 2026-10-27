import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { call, on } from './bridge'
import { clock, dateTime, dayLabel, duration, time } from './format'
import { Icon } from './icons'
import { RecordBar } from './RecordBar'
import { UpdateBanner } from './Update'
import type { Meeting, MeetingFile, MeetingSummary, Segment, TranscriptStatus } from './types'

/** What the in-app player shows; n changes on every request, so the same time can be played twice. */
type Playing = { file: MeetingFile; at: number; n: number }
const TRACK_OF: Record<string, string> = { Me: 'mic', Others: 'system' }

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
  const [keyMissing, setKeyMissing] = useState(false)
  useEffect(() => {
    call('getSettings').then((s) => setKeyMissing(!s.apiKeySet && !!s.transcribers.find((t) => t.id === s.transcriber)?.needsKey))
  }, [])

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
        <div className="sidebar-footer">
          <a className="tab" href="#/settings"><Icon name="gear" />Settings</a>
        </div>
      </nav>
      <main className="content">
        <UpdateBanner />
        {keyMissing && (
          <div className="banner" role="status">
            <Icon name="key" />
            <span style={{ flex: 1 }}><strong>Add your Gemini API key</strong> to get transcripts. Recordings work without it.</span>
            <a className="btn primary" href="#/settings/transcription">Open Settings</a>
          </div>
        )}
        {selected ? <MeetingDetail key={selected} id={selected} query={q} onDeleted={() => { setSelected(undefined); refresh() }} /> : (
          <div className="empty">
            <h2>No meetings yet</h2>
            <p>Click Start recording to record your first meeting.</p>
          </div>
        )}
      </main>
    </div>
  )
}

function MeetingDetail({ id, query, onDeleted }: { id: string; query: string; onDeleted: () => void }) {
  const [meeting, setMeeting] = useState<Meeting>()
  const [copied, setCopied] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const [playing, setPlaying] = useState<Playing>()
  useEffect(() => setPlaying(undefined), [id])

  const load = useCallback(() => { call('getMeeting', { id }).then(setMeeting) }, [id])
  useEffect(() => { load(); return on('meetingsChanged', load) }, [load])
  // While a transcript is being written (live during a recording, or after it), show it as it grows.
  const growing = meeting?.status === 'transcribing'
  useEffect(() => {
    if (!growing) return
    const timer = setInterval(load, 3000)
    return () => clearInterval(timer)
  }, [growing, load])
  if (!meeting) return null

  const failed = meeting.segments.filter((s) => s.failed).length
  const play = (file: MeetingFile, at = 0) =>
    file.url ? setPlaying((p) => ({ file, at, n: (p?.n ?? 0) + 1 })) : call('openPath', { path: file.path })
  // A segment plays from its speaker's own track: Me → mic, Others → system.
  const playSegment = (s: Segment) => {
    const audio = meeting.files.filter((f) => f.kind === 'audio')
    const file = audio.find((f) => f.name.split('.')[0] === (TRACK_OF[s.speaker] ?? s.speaker)) ?? audio[0]
    if (file) play(file, s.start)
  }
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
            {confirming ? (
              <span className="confirm" role="group" aria-label="Confirm delete">
                <span>Move this meeting to the Trash?</span>
                <button className="btn danger" onClick={() => call('deleteMeeting', { id }).then(onDeleted, (e: Error) => { setDeleteError(e.message); setConfirming(false) })}>Delete</button>
                <button className="btn" onClick={() => setConfirming(false)}>Cancel</button>
              </span>
            ) : (
              <button className="btn" onClick={() => { setDeleteError(''); setConfirming(true) }} disabled={meeting.status === 'transcribing'}><Icon name="trash" />Delete</button>
            )}
            {deleteError && <span className="small" style={{ flexBasis: '100%', color: 'var(--warn)' }}>{deleteError}</span>}
          </div>
        </header>
        {playing && <Player key={playing.file.name} {...playing} onClose={() => setPlaying(undefined)} />}
        <Transcript meeting={meeting} query={query} onTranscribe={transcribe} onPlay={playSegment} />
      </div>
      <aside className="files" aria-label="Recorded files">
        {meeting.files.some((f) => f.kind === 'display') && <div className="eyebrow" style={{ padding: 0 }}>Displays</div>}
        {meeting.files.filter((f) => f.kind === 'display').map((f) => (
          <button key={f.name} className="thumb" style={f.thumbnail ? { backgroundImage: `url(${f.thumbnail})` } : undefined}
            onClick={() => play(f)} aria-label={`Play ${f.name}`} aria-current={playing?.file.name === f.name || undefined}>
            <span className="play"><Icon name="play" /></span>
            <span className="label">{f.name} · {f.info}</span>
          </button>
        ))}
        <div className="eyebrow" style={{ padding: '8px 0 0' }}>Audio tracks</div>
        {meeting.files.filter((f) => f.kind === 'audio').map((f) => (
          <button key={f.name} className="track" onClick={() => play(f)} aria-current={playing?.file.name === f.name || undefined}>
            <span className={`dot ${f.name.startsWith('system') ? 'others' : ''}`} />
            <span style={{ flex: 1 }}><span style={{ fontWeight: 600, fontSize: 13 }}>{f.name}</span><br /><span className="small muted">{f.info}</span></span>
            <Icon name="play" />
          </button>
        ))}
      </aside>
    </div>
  )
}

function Player({ file, at, n, onClose }: Playing & { onClose: () => void }) {
  const ref = useRef<HTMLVideoElement & HTMLAudioElement>(null)
  useEffect(() => {
    const media = ref.current
    if (!media) return
    const go = () => { media.currentTime = at; media.play().catch(() => {}) }
    if (media.readyState >= 1) go()
    else media.addEventListener('loadedmetadata', go, { once: true })
    return () => media.removeEventListener('loadedmetadata', go)
  }, [at, n])
  return (
    <div className="player" aria-label="Player">
      {file.kind === 'display' ? <video ref={ref} src={file.url} controls /> : <audio ref={ref} src={file.url} controls />}
      <div className="player-bar">
        <span className="small"><b>{file.name}</b> · {file.info}</span>
        <button className="btn" onClick={onClose}><Icon name="close" />Close</button>
      </div>
    </div>
  )
}

function Transcript({ meeting, query, onTranscribe, onPlay }: { meeting: Meeting; query: string; onTranscribe: () => void; onPlay: (s: Segment) => void }) {
  if (meeting.status === 'transcribing' && meeting.segments.length === 0) {
    return <div className="empty"><div className="spinner" /><h2>Transcribing…</h2><p>Text shows up here as each part is done.</p></div>
  }
  if (meeting.segments.length === 0) {
    return (
      <div className="empty">
        <h2>{meeting.status === 'failed' ? 'Transcription failed' : 'Not transcribed yet'}</h2>
        <p>{meeting.status === 'failed' ? 'The recording is safe. Fix the problem below, then try again.' : 'The recording is saved. Transcribe it now to read and search it.'}</p>
        {meeting.status === 'failed' && meeting.error && <p className="failed" style={{ flex: 'none', maxWidth: 560 }}>{meeting.error}</p>}
        <button className="btn primary" onClick={onTranscribe}>Transcribe now</button>
      </div>
    )
  }
  return (
    <section className="transcript" aria-label="Transcript">
      {meeting.segments.map((s, i) => <SegmentRow key={i} s={s} query={query} onRetry={onTranscribe} onPlay={onPlay} />)}
      {meeting.status === 'transcribing' && <p className="live-note small muted"><span className="spinner small-spinner" />Transcribing… new text appears as each part is done</p>}
    </section>
  )
}

function SegmentRow({ s, query, onRetry, onPlay }: { s: Segment; query: string; onRetry: () => void; onPlay: (s: Segment) => void }) {
  return (
    <div className="segment">
      <button className="at" onClick={() => onPlay(s)} title="Play from here" aria-label={`Play from ${clock(s.start)}`}><Icon name="play" />{clock(s.start)}</button>
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
