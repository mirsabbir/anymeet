import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { call, on } from './bridge'
import { cloudView, JOB_TEXT, needsBackup } from './cloud'
import { bytes, dateTime } from './format'
import { Icon } from './icons'
import type { MeetingSummary, Settings, SummaryLanguage } from './types'

const LANGUAGES: [SummaryLanguage, string][] = [['auto', 'The meeting’s own language'], ['en', 'English'], ['bn', 'Bangla']]

/** Settings → Cloud & AI: the backup server, backing up and freeing space in bulk, and AI summaries. */
export function CloudAndAi({ s, reload }: { s: Settings; reload: () => void }) {
  return (
    <div className="page">
      <h1>Cloud & AI</h1>
      <p>Keep a copy of every meeting in your own cloud storage, so recordings can leave this computer and come back when you need them. Transcripts stay here.</p>
      <CloudBackup s={s} reload={reload} />
      <h2>AI summary</h2>
      <p className="small muted">Claude reads the transcript and writes what was decided, who does what, and the topics in order. About $0.20–0.50 for an hour-long meeting, on your Anthropic account.</p>
      <AiSummary s={s} reload={reload} />
    </div>
  )
}

function CloudBackup({ s, reload }: { s: Settings; reload: () => void }) {
  const [url, setUrl] = useState(s.cloudUrl)
  const [token, setToken] = useState('')
  const [status, setStatus] = useState<{ ok: boolean; text: string }>()
  const [meetings, setMeetings] = useState<MeetingSummary[]>([])
  const loadMeetings = useCallback(() => { call('listMeetings').then(setMeetings) }, [])
  useEffect(() => { loadMeetings(); return on('meetingsChanged', loadMeetings) }, [loadMeetings])
  const ready = !!s.cloudUrl && s.cloudTokenSet

  const check = async () => {
    setStatus({ ok: true, text: 'Connecting…' })
    try {
      const r = await call('checkCloud')
      setStatus({ ok: true, text: `Connected · ${r.meetings} meeting${r.meetings === 1 ? '' : 's'} · ${bytes(r.bytes)} in the cloud` })
    } catch (e) {
      setStatus({ ok: false, text: (e as Error).message })
    }
  }
  const save = async (e: FormEvent) => {
    e.preventDefault()
    await call('setCloud', { url: url.trim(), token: token.trim() })
    setToken('')
    reload()
    if (url.trim()) check()
  }

  const toBackUp = meetings.filter(needsBackup)
  const freeable = meetings.map((m) => [m, cloudView(m).freeable] as const).filter(([m, b]) => b > 0 && !cloudView(m).job)
  const freeBytes = freeable.reduce((sum, [, b]) => sum + b, 0)
  const bulk = (method: 'backup' | 'freeUp', ids: string[]) => call(method, { ids }).then(loadMeetings, (e: Error) => setStatus({ ok: false, text: e.message }))

  return (
    <>
      <form onSubmit={save}>
        <label htmlFor="cloud-url" style={{ display: 'block', fontWeight: 600, marginBottom: 6 }}>Server</label>
        <div className="field" style={{ marginBottom: 8 }}>
          <input id="cloud-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://… (your AnyMeet Cloud address)" autoComplete="off" />
        </div>
        <label htmlFor="cloud-token" className="visually-hidden">Access token</label>
        <div className="field">
          <input id="cloud-token" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)}
            placeholder={s.cloudTokenSet ? 'A token is saved. Paste a new one to replace it' : 'Paste the access token'} />
          <button className="btn dark" type="submit" disabled={!url.trim() || (!token.trim() && url.trim() === s.cloudUrl)}>Save</button>
        </div>
      </form>
      {status ? <div className={`note ${status.ok ? 'ok' : 'bad'}`}>{status.text}</div>
        : ready ? <div className="note ok">Set up on this computer. <button className="link" onClick={check}>Test the connection</button></div>
        : <div className="note bad">Not set up. Recordings stay only on this computer.</div>}

      <div className="rows">
        <div className="row">
          <div className="label">
            <div>Back up automatically</div>
            <div className="small muted">Each meeting, as soon as its transcript is ready (or when recording stops, if transcription is off). Large recordings go up in parts, and an interrupted backup carries on later.</div>
          </div>
          <button role="switch" aria-checked={s.autoBackup} aria-label="Back up automatically" className="switch" disabled={!ready}
            onClick={() => call('setAutoBackup', { on: !s.autoBackup }).then(reload)}><span /></button>
        </div>
        <div className="row" style={{ flexWrap: 'wrap' }}>
          <div className="label">
            <div>Not backed up yet</div>
            <div className="small muted">{toBackUp.length ? `${toBackUp.length} meeting${toBackUp.length === 1 ? '' : 's'} with files only on this computer` : 'Every meeting is in the cloud'}</div>
          </div>
          <button className="btn" disabled={!ready || !toBackUp.length} onClick={() => bulk('backup', toBackUp.map((m) => m.id))}>
            <Icon name="upload" />Back up {toBackUp.length > 1 ? `all ${toBackUp.length}` : 'now'}
          </button>
        </div>
        <div className="row" style={{ flexWrap: 'wrap' }}>
          <div className="label">
            <div>Free up space</div>
            <div className="small muted">{freeBytes ? `${bytes(freeBytes)} of video and audio in ${freeable.length} meeting${freeable.length === 1 ? ' is' : 's are'} safely in the cloud. Removing them keeps the transcripts here; download a recording again from its meeting page.`
              : 'Recordings appear here once they are backed up'}</div>
          </div>
          <button className="btn" disabled={!ready || !freeBytes} onClick={() => bulk('freeUp', freeable.map(([m]) => m.id))}>
            <Icon name="cloud" />Free up {freeBytes ? bytes(freeBytes) : 'space'}
          </button>
        </div>
      </div>
      {ready && <CloudStorage meetings={meetings} />}
    </>
  )
}

const WHERE: Record<string, string> = {
  cloudOnly: 'Only in the cloud', recordings: 'Recordings only in the cloud', saved: 'Here and in the cloud', partial: 'Partly in the cloud',
}

/** Everything in the cloud, with sizes; delete cloud copies in bulk. Copies here stay. */
function CloudStorage({ meetings }: { meetings: MeetingSummary[] }) {
  const [onlyCloud, setOnlyCloud] = useState(false)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState('')
  const rows = meetings.filter((m) => Object.keys(m.cloud ?? {}).length > 0).map((m) => {
    const v = cloudView(m)
    const size = Object.values(m.cloud!).reduce((sum, f) => sum + f.size, 0)
    const nowhereElse = Object.entries(m.cloud!).filter(([name]) => m.local[name] === undefined).reduce((sum, [, f]) => sum + f.size, 0)
    return { m, v, size, nowhereElse, where: v.state === 'cloudOnly' ? 'cloudOnly' : v.missing.length ? 'recordings' : v.state }
  })
  const shown = onlyCloud ? rows.filter((r) => r.where === 'cloudOnly' || r.where === 'recordings') : rows
  const total = rows.reduce((sum, r) => sum + r.size, 0)
  const chosen = rows.filter((r) => picked.has(r.m.id))
  const lost = chosen.filter((r) => r.nowhereElse > 0)
  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const forget = () => {
    setConfirming(false)
    call('forgetCloud', { ids: chosen.map((r) => r.m.id) }).then(() => setPicked(new Set()), (e: Error) => setError(e.message))
  }

  return (
    <>
      <h2>In the cloud</h2>
      <p className="small muted">
        {rows.length} meeting{rows.length === 1 ? '' : 's'} · {bytes(total)} · about ${(total / 1e9 * 0.025).toFixed(2)} a month at most, less once recordings are a month old.
        {' '}<button className="link" onClick={() => call('syncCloud')}>Refresh</button>
      </p>
      <div className="cloud-tools">
        <label className="small"><input type="checkbox" checked={onlyCloud} onChange={(e) => setOnlyCloud(e.target.checked)} /> Only what isn’t on this computer</label>
        <span style={{ flex: 1 }} />
        {confirming ? null : (
          <button className="btn" disabled={!chosen.length} onClick={() => { setError(''); setConfirming(true) }}>
            <Icon name="trash" />Delete from the cloud{chosen.length ? ` (${chosen.length})` : ''}
          </button>
        )}
      </div>
      {confirming && (
        <div className={`banner ${lost.length ? 'warn' : ''}`} role="alertdialog" aria-label="Confirm delete from the cloud">
          <span style={{ flex: 1 }}>
            Delete {chosen.length} meeting{chosen.length === 1 ? '' : 's'} ({bytes(chosen.reduce((s, r) => s + r.size, 0))}) from the cloud? Copies on this computer stay.
            {lost.length > 0 && <><br /><strong>{bytes(lost.reduce((s, r) => s + r.nowhereElse, 0))} of it isn’t on this computer</strong> ({lost.length} meeting{lost.length === 1 ? '' : 's'}): it will be gone for good.</>}
          </span>
          <button className="btn danger" onClick={forget}>Delete from the cloud</button>
          <button className="btn" onClick={() => setConfirming(false)}>Cancel</button>
        </div>
      )}
      {error && <div className="note bad">{error}</div>}
      {shown.length === 0 ? <p className="muted small">{onlyCloud ? 'Everything in the cloud is also on this computer.' : 'Nothing in the cloud yet.'}</p> : (
        <table className="cloud-table">
          <thead><tr><th aria-label="Choose" /><th>Meeting</th><th>Size</th><th>Where</th></tr></thead>
          <tbody>
            {shown.map(({ m, v, size, where }) => (
              <tr key={m.id}>
                <td><input type="checkbox" aria-label={`Choose ${m.id}`} checked={picked.has(m.id)} disabled={!!v.job} onChange={() => toggle(m.id)} /></td>
                <td>
                  <a href={`#/meetings/${m.id}`}>{dateTime(m.startedAt)}</a>
                  <div className="small muted">{v.job ? JOB_TEXT[v.job.kind][0] : m.title || m.preview}</div>
                </td>
                <td className="num">{bytes(size)}</td>
                <td className={where === 'saved' ? 'ok' : ''}>{WHERE[where]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}

function AiSummary({ s, reload }: { s: Settings; reload: () => void }) {
  const [key, setKey] = useState('')
  const [saved, setSaved] = useState(false)
  const save = async (e: FormEvent) => {
    e.preventDefault()
    await call('setAnthropicKey', { key: key.trim() })
    setKey('')
    setSaved(true)
    reload()
  }
  return (
    <>
      <form onSubmit={save}>
        <label htmlFor="anthropic-key" style={{ display: 'block', fontWeight: 600, marginBottom: 6 }}>Anthropic API key</label>
        <div className="field">
          <input id="anthropic-key" type="password" autoComplete="off" value={key} onChange={(e) => { setKey(e.target.value); setSaved(false) }}
            placeholder={s.anthropicKeySet ? 'A key is saved. Paste a new one to replace it' : 'Paste your key (sk-ant-…)'} />
          <button className="btn dark" type="submit" disabled={!key.trim()}>Save key</button>
        </div>
      </form>
      {saved ? <div className="note ok">Key saved.</div>
        : s.anthropicKeySet ? <div className="note ok">A key is saved on this computer.</div>
        : <div className="note bad">No key yet. Create one at console.anthropic.com → API keys.</div>}
      <div className="rows">
        <div className="row">
          <div className="label"><div>Summarize automatically</div><div className="small muted">When a meeting’s transcript is ready and it has no summary yet</div></div>
          <button role="switch" aria-checked={s.autoSummary} aria-label="Summarize automatically" className="switch" disabled={!s.anthropicKeySet}
            onClick={() => call('setAutoSummary', { on: !s.autoSummary }).then(reload)}><span /></button>
        </div>
        <div className="row" style={{ alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <div className="label"><div>Write summaries in</div><div className="small muted">Names and English terms stay in English letters</div></div>
          <div className="modes" role="radiogroup" aria-label="Summary language">
            {LANGUAGES.map(([id, name]) => (
              <button key={id} role="radio" aria-checked={s.summaryLanguage === id} className="mode"
                onClick={() => call('setSummaryLanguage', { language: id }).then(reload)}><span className="mode-title">{name}</span></button>
            ))}
          </div>
        </div>
      </div>
    </>
  )
}
