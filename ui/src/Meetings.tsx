import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { call, on } from './bridge'
import { activeJob, cloudView, JOB_TEXT, type CloudView } from './cloud'
import { bytes, clock, dateTime, dayLabel, duration, time } from './format'
import { Icon } from './icons'
import { RecordBar } from './RecordBar'
import { SummaryCard } from './Summary'
import { UpdateBanner } from './Update'
import type { Meeting, MeetingFile, MeetingJob, MeetingSummary, Segment, Settings, TranscribeProgress, TranscriptStatus } from './types'

/** What the in-app player shows; n changes on every request, so the same time can be played twice. */
type Playing = { file: MeetingFile; at: number; n: number }
const TRACK_OF: Record<string, string> = { Me: 'mic', Others: 'system' } // transcripts without `track`
const TONES = ['others', 'tone2', 'tone3', 'tone4', 'tone5']

/** Each speaker's colour: "Me" keeps the accent, the others take tones in order of first words. */
function speakerTones(segments: Segment[]): Map<string, string> {
  const others = [...new Set(segments.map((s) => s.speaker))].filter((name) => name !== 'Me')
  return new Map(others.map((name, i) => [name, TONES[i % TONES.length]]))
}

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
  const [settings, setSettings] = useState<Settings>()
  useEffect(() => {
    const load = () => { call('getSettings').then(setSettings) }
    load()
    return on('settingsChanged', load)
  }, [])
  const keyMissing = !!settings && !settings.apiKeySet && !!settings.transcribers.find((t) => t.id === settings.transcriber)?.needsKey

  const refresh = useCallback(() => {
    call('listMeetings').then((items) => {
      setList(items)
      setSelected((id) => id ?? items[0]?.id)
    })
  }, [])
  useEffect(() => { refresh(); return on('meetingsChanged', refresh) }, [refresh])
  // A cloud job moves on its own; follow it in the list while one runs.
  const jobRunning = list.some((m) => activeJob(m.job))
  useEffect(() => {
    if (!jobRunning) return
    const timer = setInterval(refresh, 3000)
    return () => clearInterval(timer)
  }, [jobRunning, refresh])

  const q = query.trim().toLowerCase()
  const shown = q ? list.filter((m) => `${m.id} ${m.title ?? ''} ${m.preview}`.toLowerCase().includes(q)) : list
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
                <div className="title">
                  <span>{time(m.startedAt)}{m.durationSec > 0 && ` · ${duration(m.durationSec)}`}</span>
                  <CloudMark m={m} />
                  {cloudView(m).state === 'cloudOnly' ? <span className="pill none">In the cloud</span> : <StatusPill status={m.status} />}
                </div>
                {m.title && <div className="item-title">{m.title}</div>}
                <div className="preview">{m.preview || (m.title ? '' : m.id)}</div>
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
        {selected ? <MeetingDetail key={selected} id={selected} query={q} settings={settings} onDeleted={() => { setSelected(undefined); refresh() }} /> : (
          <div className="empty">
            <h2>No meetings yet</h2>
            <p>Click Start recording to record your first meeting.</p>
          </div>
        )}
      </main>
    </div>
  )
}

/** The list's small sign that a meeting is in the cloud, or on its way. */
function CloudMark({ m }: { m: MeetingSummary }) {
  const v = cloudView(m)
  if (v.job && v.job.kind !== 'summary') {
    const pct = v.job.total ? Math.floor((100 * (v.job.done ?? 0)) / v.job.total) : 0
    return <span className="cloud-mark busy" title={JOB_TEXT[v.job.kind][0]}><Icon name="cloud" />{v.job.waiting ? '…' : `${pct}%`}</span>
  }
  if (v.failed && v.failed.kind !== 'summary') return <span className="cloud-mark bad" title={v.failed.error}><Icon name="cloud" />!</span>
  if (v.state === 'saved') return <span className="cloud-mark ok" title="Backed up to the cloud"><Icon name="cloud" /></span>
  return null
}

function MeetingDetail({ id, query, settings, onDeleted }: { id: string; query: string; settings?: Settings; onDeleted: () => void }) {
  const [meeting, setMeeting] = useState<Meeting>()
  const [copied, setCopied] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const [playing, setPlaying] = useState<Playing>()
  useEffect(() => setPlaying(undefined), [id])

  const load = useCallback(() => { call('getMeeting', { id }).then(setMeeting) }, [id])
  useEffect(() => { load(); return on('meetingsChanged', load) }, [load])
  // While a transcript is being written (live during a recording, or after it), show it as it grows.
  // …and a cloud job or summary as it goes; for a few seconds after asking for one, until its job file shows up.
  const [asked, setAsked] = useState(0)
  const growing = meeting?.status === 'transcribing' || !!activeJob(meeting?.job) || Date.now() - asked < 8000
  useEffect(() => {
    if (!growing) return
    const timer = setInterval(load, 2000)
    return () => clearInterval(timer)
  }, [growing, load])
  const [actionError, setActionError] = useState('')
  const [stopping, setStopping] = useState(false)
  useEffect(() => { if (!growing) setStopping(false) }, [growing])
  if (!meeting) return null

  const failed = meeting.segments.filter((s) => s.failed).length
  const cloud = cloudView(meeting)
  const cloudOn = !!settings?.cloudUrl && !!settings.cloudTokenSet
  // Cloud jobs and summaries: started here, followed through meeting.job.
  const job = (method: 'backup' | 'freeUp' | 'restore' | 'summarize' | 'stopJob', files?: string[]) => {
    setActionError('')
    setAsked(Date.now())
    const params = method === 'backup' || method === 'freeUp' ? { ids: [id] } : method === 'restore' ? { id, files } : { id }
    call(method, params as never).then(load, (e: Error) => setActionError(e.message))
  }
  const play = (file: MeetingFile, at = 0) =>
    file.url ? setPlaying((p) => ({ file, at, n: (p?.n ?? 0) + 1 })) : call('openPath', { path: file.path })
  // A segment plays from the track it was heard on (mic or system), whatever its speaker is called now.
  const playSegment = (s: Segment) => {
    const audio = meeting.files.filter((f) => f.kind === 'audio')
    const file = audio.find((f) => f.name.split('.')[0] === (s.track ?? TRACK_OF[s.speaker] ?? s.speaker)) ?? audio[0]
    if (file) play(file, s.start)
  }
  // onlyFailed: Try again sends only the parts that were not transcribed; the rest stays as it is.
  const transcribe = (onlyFailed = false) => {
    setActionError('')
    call('transcribe', { id, onlyFailed }).then(load, (e: Error) => setActionError(e.message))
  }
  const stop = () => {
    setStopping(true)
    call('stopTranscribing', { id }).then(load, (e: Error) => { setStopping(false); setActionError(e.message) })
  }
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
              {meeting.durationSec > 0 && <span>{duration(meeting.durationSec)}</span>}
              {meeting.model && <span>{meeting.model}</span>}
              {failed > 0 && <span style={{ color: 'var(--warn)' }}>{failed} part{failed > 1 ? 's' : ''} not transcribed</span>}
              {cloudOn && <CloudLine v={cloud} />}
            </div>
          </div>
          <div className="actions">
            <button className="btn" onClick={() => call('openPath', { path: meeting.folder })}><Icon name="folder" />Show in Finder</button>
            {cloudOn && !cloud.job && (cloud.state === 'none' || cloud.state === 'partial') && (
              <button className="btn" onClick={() => job('backup')} disabled={meeting.status === 'transcribing'}><Icon name="upload" />Back up</button>
            )}
            {cloudOn && !cloud.job && cloud.freeable > 0 && (
              <button className="btn" onClick={() => job('freeUp')} disabled={meeting.status === 'transcribing'}
                title="Deletes the video and audio from this computer; they stay in the cloud, and the transcript stays here">
                <Icon name="cloud" />Free up {bytes(cloud.freeable)}
              </button>
            )}
            {cloudOn && !cloud.job && cloud.missing.length > 0 && (
              <button className="btn" onClick={() => job('restore', cloud.missing)}><Icon name="download" />Download recordings ({bytes(cloud.missingBytes)})</button>
            )}
            {(meeting.status === 'done' || meeting.status === 'failed') && meeting.files.some((f) => f.kind === 'audio') ? (
              <>
                <button className="btn" onClick={() => transcribe()} title="Transcribe the whole meeting from the start">
                  <Icon name="refresh" />Transcribe again
                </button>
                <button className="btn primary" onClick={copy}><Icon name={copied ? 'check' : 'copy'} />{copied ? 'Copied' : 'Copy transcript'}</button>
              </>
            ) : null}
            {confirming ? (
              <span className="confirm" role="group" aria-label="Confirm delete">
                <span>{cloud.state === 'none' ? 'Move this meeting to the Trash?'
                  : cloud.state === 'cloudOnly' ? 'Delete this meeting from the cloud? This can’t be undone here.'
                  : 'Delete this meeting here and in the cloud? To keep it and only free space, use Free up.'}</span>
                <button className="btn danger" onClick={() => call('deleteMeeting', { id, cloudToo: cloud.state !== 'none' })
                  .then(onDeleted, (e: Error) => { setDeleteError(e.message); setConfirming(false) })}>Delete</button>
                <button className="btn" onClick={() => setConfirming(false)}>Cancel</button>
              </span>
            ) : (
              <button className="btn" onClick={() => { setDeleteError(''); setConfirming(true) }} disabled={meeting.status === 'transcribing' || !!cloud.job}><Icon name="trash" />Delete</button>
            )}
            {deleteError && <span className="small" style={{ flexBasis: '100%', color: 'var(--warn)' }}>{deleteError}</span>}
          </div>
        </header>
        <RunBanner meeting={meeting} failed={failed} stopping={stopping} onStop={stop} onRetry={() => transcribe(true)} />
        <JobBanner v={cloud} onStop={() => job('stopJob')}
          onRetry={(kind) => kind === 'summary' ? job('summarize') : kind === 'restore' ? job('restore') : kind === 'free' ? job('freeUp') : job('backup')} />
        {actionError && <p className="banner warn" role="alert">{actionError}</p>}
        <SpeakerNames meeting={meeting} onRenamed={load} />
        {playing && <Player key={playing.file.name} {...playing} onClose={() => setPlaying(undefined)} />}
        <SummaryCard meeting={meeting} keySet={!!settings?.anthropicKeySet} writing={cloud.job?.kind === 'summary'}
          onSummarize={() => job('summarize')} onPlay={(at) => { const f = meeting.files.find((f) => f.kind === 'display') ?? meeting.files[0]; if (f) play(f, at) }} />
        {cloud.transcriptInCloud && meeting.segments.length === 0 && !cloud.job ? (
          <div className="empty">
            <h2>The transcript is in the cloud</h2>
            <p>It isn’t on this computer. Download it to read and search it here.</p>
            <button className="btn primary" onClick={() => job('restore', ['transcript.json', ...(meeting.cloud?.['transcript.md'] ? ['transcript.md'] : []), ...(meeting.cloud?.['summary.json'] && !meeting.local['summary.json'] ? ['summary.json'] : [])])}>
              <Icon name="download" />Download the transcript
            </button>
          </div>
        ) : (
          <Transcript meeting={meeting} query={query} onTranscribe={() => transcribe()} onRetry={() => transcribe(true)} onPlay={playSegment}
            canTranscribe={meeting.files.some((f) => f.kind === 'audio')} />
        )}
      </div>
      <aside className="files" aria-label="Recorded files">
        {meeting.files.some((f) => f.kind === 'display') && <div className="eyebrow" style={{ padding: 0 }}>Displays</div>}
        {meeting.files.filter((f) => f.kind === 'display').map((f) => (
          <button key={f.name} className="thumb" style={f.thumbnail ? { backgroundImage: `url(${f.thumbnail})` } : undefined}
            onClick={() => play(f)} aria-label={`Play ${f.name}`} aria-current={playing?.file.name === f.name || undefined}>
            <span className="play"><Icon name="play" /></span>
            <span className="label">{f.name} · {f.info}{meeting.cloud?.[f.name] && <> · <Icon name="cloud" label="Also in the cloud" /></>}</span>
          </button>
        ))}
        {meeting.files.some((f) => f.kind === 'audio') && <div className="eyebrow" style={{ padding: '8px 0 0' }}>Audio tracks</div>}
        {meeting.files.filter((f) => f.kind === 'audio').map((f) => (
          <button key={f.name} className="track" onClick={() => play(f)} aria-current={playing?.file.name === f.name || undefined}>
            <span className={`dot ${f.name.startsWith('system') ? 'others' : ''}`} />
            <span style={{ flex: 1 }}><span style={{ fontWeight: 600, fontSize: 13 }}>{f.name}</span><br /><span className="small muted">{f.info}</span></span>
            {meeting.cloud?.[f.name] && <span title="Also in the cloud"><Icon name="cloud" label="Also in the cloud" /></span>}
            <Icon name="play" />
          </button>
        ))}
        {cloud.missing.length > 0 && <div className="eyebrow" style={{ padding: '8px 0 0' }}>Only in the cloud</div>}
        {cloud.missing.map((name) => (
          <button key={name} className="track cloud-only" onClick={() => job('restore', [name])} disabled={!!cloud.job || !cloudOn}
            title="Download it to this computer to play it">
            <Icon name="cloud" />
            <span style={{ flex: 1 }}><span style={{ fontWeight: 600, fontSize: 13 }}>{name}</span><br /><span className="small muted">{bytes(meeting.cloud![name].size)} · download to play</span></span>
            <Icon name="download" />
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

/** The people in this transcript; click one to rename it everywhere. A name that's already used merges the two. */
function SpeakerNames({ meeting, onRenamed }: { meeting: Meeting; onRenamed: () => void }) {
  const [editing, setEditing] = useState<string>()
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  const handled = useRef(false) // Enter, then the blur as the field goes away: rename once
  const tones = speakerTones(meeting.segments)
  const names = [...new Set(meeting.segments.map((s) => s.speaker))]
  if (names.length === 0) return null
  const busy = meeting.status === 'transcribing'

  const start = (name: string) => { handled.current = false; setEditing(name); setValue(name); setError('') }
  const save = async (e?: FormEvent) => {
    e?.preventDefault()
    if (handled.current || !editing) return
    handled.current = true
    const to = value.trim()
    setEditing(undefined)
    if (!to || to === editing) return
    try {
      await call('renameSpeaker', { id: meeting.id, from: editing, to })
      onRenamed()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <div className="speakers" role="group" aria-label="Speakers">
      <span className="small muted">{busy ? 'Speakers · rename once the transcript is finished' : 'Speakers · click a name to rename it'}</span>
      {names.map((name) => editing === name ? (
        <form key={name} onSubmit={save}>
          <label htmlFor="rename" className="visually-hidden">New name for {name}</label>
          <input id="rename" className="rename" autoFocus value={value} onFocus={(e) => e.target.select()} onChange={(e) => setValue(e.target.value)}
            onBlur={() => save()} onKeyDown={(e) => { if (e.key === 'Escape') { handled.current = true; setEditing(undefined) } }} />
        </form>
      ) : (
        <button key={name} className={`who ${tones.get(name) ?? ''}`} disabled={busy} onClick={() => start(name)} aria-label={`Rename ${name}`}>
          {name}<Icon name="pencil" />
        </button>
      ))}
      {error && <span className="small" style={{ color: 'var(--warn)' }}>{error}</span>}
    </div>
  )
}

const STAGE: Record<TranscribeProgress['stage'], string> = {
  preparing: 'Getting the audio ready…',
  speakers: 'Telling the speakers apart… This runs on your computer and takes a few minutes for a long meeting.',
  transcribing: 'Transcribing…',
}

/**
 * At the top of the meeting, so it is seen without scrolling: what a running transcription
 * is doing, how far it is, and Stop. When it has finished with parts missing: how many,
 * the transcriber's own reason, and Try again for only those parts.
 */
function RunBanner({ meeting, failed, stopping, onStop, onRetry }: {
  meeting: Meeting; failed: number; stopping: boolean; onStop: () => void; onRetry: () => void
}) {
  if (meeting.status === 'transcribing') {
    const p = meeting.progress
    const counting = p?.stage === 'transcribing' && p.total > 0
    let text = p ? STAGE[p.stage] : 'Starting…'
    if (counting) {
      text = `Transcribing part ${Math.min(p.done + 1, p.total)} of ${p.total}`
      if (p.failed) text += ` · ${p.failed} could not be transcribed so far`
    }
    return (
      <div className="banner run" role="status" aria-live="polite">
        <span className="spinner small-spinner" />
        <span style={{ flex: 1 }}>
          {stopping ? 'Stopping… the parts already sent are finishing.' : text}
          {!stopping && p && p.waitSec > 0 && <span className="small"> · Gemini asked to wait: going on in {p.waitSec} s</span>}
        </span>
        <button className="btn" onClick={onStop} disabled={stopping}><Icon name="close" />{stopping ? 'Stopping…' : 'Stop'}</button>
        {counting && (
          <div className="bar" role="progressbar" aria-label="Parts transcribed" aria-valuemin={0} aria-valuemax={p.total} aria-valuenow={p.done}>
            <span style={{ width: `${(100 * p.done) / p.total}%` }} />
          </div>
        )}
      </div>
    )
  }
  if (failed === 0 || meeting.segments.length === 0) return null
  return (
    <div className="banner warn" role="status">
      <Icon name="warning" />
      <span style={{ flex: 1 }}>
        <strong>{failed} part{failed > 1 ? 's were' : ' was'} not transcribed.</strong> The audio is saved.
        {meeting.error && <><br /><span className="small">{meeting.error}</span></>}
      </span>
      <button className="btn primary" onClick={onRetry}>{failed > 1 ? `Try again: only these ${failed} parts` : 'Try again: only this part'}</button>
    </div>
  )
}

function Transcript({ meeting, query, onTranscribe, onRetry, onPlay, canTranscribe }: {
  meeting: Meeting; query: string; onTranscribe: () => void; onRetry: () => void; onPlay: (s: Segment) => void; canTranscribe: boolean
}) {
  if (meeting.status === 'transcribing' && meeting.segments.length === 0) {
    return <div className="empty"><div className="spinner" /><h2>Transcribing…</h2><p>Text shows up here as each part is done.</p></div>
  }
  if (meeting.segments.length === 0) {
    return (
      <div className="empty">
        <h2>{meeting.status === 'failed' ? 'Transcription failed' : 'Not transcribed yet'}</h2>
        <p>{meeting.status === 'failed' ? 'The recording is safe. Fix the problem below, then try again.' : 'The recording is saved. Transcribe it now to read and search it.'}</p>
        {meeting.status === 'failed' && meeting.error && <p className="failed" style={{ flex: 'none', maxWidth: 560 }}>{meeting.error}</p>}
        {canTranscribe ? <button className="btn primary" onClick={onTranscribe}>Transcribe now</button>
          : <p className="small muted">The audio isn’t on this computer. Download the recordings first.</p>}
      </div>
    )
  }
  const tones = speakerTones(meeting.segments)
  return (
    <section className="transcript" aria-label="Transcript">
      {meeting.segments.map((s, i) => (
        <SegmentRow key={i} s={s} tone={tones.get(s.speaker) ?? ''} query={query} busy={meeting.status === 'transcribing'} onRetry={onRetry} onPlay={onPlay} />
      ))}
      {meeting.status === 'transcribing' && <p className="live-note small muted"><span className="spinner small-spinner" />Transcribing… new text appears as each part is done</p>}
    </section>
  )
}

function SegmentRow({ s, tone, query, busy, onRetry, onPlay }: {
  s: Segment; tone: string; query: string; busy: boolean; onRetry: () => void; onPlay: (s: Segment) => void
}) {
  return (
    <div className="segment">
      <button className="at" onClick={() => onPlay(s)} title="Play from here" aria-label={`Play from ${clock(s.start)}`}><Icon name="play" />{clock(s.start)}</button>
      {s.failed ? (
        <div className="failed">
          <Icon name="warning" label="Warning" />
          <span style={{ flex: 1 }}>{s.speaker}: this part could not be transcribed ({clock(s.start)}–{clock(s.end)}). The audio is saved.</span>
          {busy
            ? <span className="small muted">Waiting for the transcription above</span>
            : <button className="btn" onClick={onRetry} title="Sends again every part that was not transcribed, and nothing else">Try again</button>}
        </div>
      ) : (
        <div style={{ flex: 1 }}>
          <span className={`who ${tone}`}>{s.speaker}</span>
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

/** "In the cloud · safe to free 1.86 GB" and the like, in the meeting's header. */
function CloudLine({ v }: { v: CloudView }) {
  if (v.state === 'saved') {
    const note = v.freeable > 0 ? ` · safe to remove ${bytes(v.freeable)} from this computer` : v.missing.length > 0 ? ' · video and audio only in the cloud' : ''
    return <span className="cloud-line ok"><Icon name="cloud" />Backed up{note}</span>
  }
  if (v.state === 'cloudOnly') return <span className="cloud-line"><Icon name="cloud" />Only in the cloud</span>
  if (v.state === 'partial') return <span className="cloud-line"><Icon name="cloud" />Partly backed up</span>
  return <span className="cloud-line muted"><Icon name="cloud" />Not backed up</span>
}

/** What a cloud job or summary is doing, with Stop; or why the last one failed, with Try again. */
function JobBanner({ v, onStop, onRetry }: { v: CloudView; onStop: () => void; onRetry: (kind: MeetingJob['kind']) => void }) {
  const j = v.job
  if (j) {
    const counting = !!j.total && !j.waiting
    const text = j.waiting ? 'Waiting: another meeting goes first' : JOB_TEXT[j.kind][0]
    return (
      <div className="banner run" role="status" aria-live="polite">
        <span className="spinner small-spinner" />
        <span style={{ flex: 1 }}>
          {text}
          {counting && <span className="small"> · {bytes(j.done ?? 0)} of {bytes(j.total!)}{j.file ? ` · now ${j.file}` : ''}</span>}
        </span>
        {(j.kind === 'backup' || j.kind === 'restore') && <button className="btn" onClick={onStop}><Icon name="close" />Stop</button>}
        {counting && (
          <div className="bar" role="progressbar" aria-label={text} aria-valuemin={0} aria-valuemax={j.total} aria-valuenow={j.done}>
            <span style={{ width: `${(100 * (j.done ?? 0)) / j.total!}%` }} />
          </div>
        )}
      </div>
    )
  }
  const f = v.failed
  if (!f) return null
  return (
    <div className="banner warn" role="status">
      <Icon name="warning" />
      <span style={{ flex: 1 }}><strong>{JOB_TEXT[f.kind][1]}.</strong> {f.error}</span>
      {f.kind !== 'forget' && <button className="btn" onClick={() => onRetry(f.kind)}>Try again</button>}
    </div>
  )
}
