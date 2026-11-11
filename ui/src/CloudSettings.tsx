import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { call, on } from './bridge'
import { cloudView, needsBackup } from './cloud'
import { bytes } from './format'
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
