import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { call, on } from './bridge'
import { Icon, type IconName } from './icons'
import type { Settings as SettingsData } from './types'

const TABS: [Tab, string, IconName][] = [
  ['recording', 'Recording', 'record'],
  ['transcription', 'Transcription', 'mic'],
  ['glossary', 'Glossary', 'search'],
]
type Tab = 'recording' | 'transcription' | 'glossary'

export function Settings() {
  const [tab, setTab] = useState<Tab>('recording')
  const [s, setS] = useState<SettingsData>()
  const load = useCallback(() => { call('getSettings').then(setS) }, [])
  useEffect(() => { load(); return on('settingsChanged', load) }, [load])

  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Settings sections">
        {TABS.map(([id, label, icon]) => (
          <button key={id} className="tab" aria-current={tab === id ? 'page' : undefined} onClick={() => setTab(id)}>
            <Icon name={icon} />{label}
          </button>
        ))}
      </nav>
      <main className="content">
        {s && tab === 'recording' && <Recording s={s} />}
        {s && tab === 'transcription' && <Transcription s={s} reload={load} />}
        {s && tab === 'glossary' && <Glossary s={s} reload={load} />}
      </main>
    </div>
  )
}

function Recording({ s }: { s: SettingsData }) {
  return (
    <div className="page">
      <h1>Recording</h1>
      <p>One click in the menu bar records every display to its own video and every audio source to its own track.</p>
      <div className="rows">
        <div className="row">
          <div className="label"><div>Displays</div><div className="small muted">All connected displays are recorded</div></div>
          <div className="value">{s.displays.length ? s.displays.map((d) => <div key={d}>{d}</div>) : 'None found'}</div>
        </div>
        <div className="row">
          <div className="label"><div>Video</div><div className="small muted">Full resolution, HEVC, about 0.5 GB per display per hour</div></div>
          <div className="value">15 fps</div>
        </div>
        <div className="row">
          <div className="label"><div>Microphone</div><div className="small muted">Labelled “Me” in the transcript</div></div>
          <div className="value">{s.microphone || 'None found'}</div>
        </div>
        <div className="row">
          <div className="label"><div>System audio</div><div className="small muted">Zoom, Teams, Meet and other apps · labelled “Others”</div></div>
          <div className="value">Recorded</div>
        </div>
        <div className="row">
          <div className="label"><div>Saved to</div><div className="small muted">One folder per meeting, named by date and time</div></div>
          <button className="btn" onClick={() => call('openPath', { path: s.meetingsFolder })}><Icon name="folder" />{s.meetingsFolder}</button>
        </div>
      </div>
    </div>
  )
}

function Transcription({ s, reload }: { s: SettingsData; reload: () => void }) {
  const [key, setKey] = useState('')
  const [saved, setSaved] = useState(false)
  const current = s.transcribers.find((t) => t.id === s.transcriber)

  const saveKey = async (e: FormEvent) => {
    e.preventDefault()
    await call('setApiKey', { key: key.trim() })
    setKey('')
    setSaved(true)
    reload()
  }

  return (
    <div className="page">
      <h1>Transcription</h1>
      <p>The model that turns each recording into text. Bangla, English and mixed speech.</p>
      <div className="cards" role="group" aria-label="Transcription model">
        {s.transcribers.map((t) => (
          <button key={t.id} className="card" aria-pressed={t.id === s.transcriber} disabled={!t.available}
            onClick={() => call('setTranscriber', { id: t.id }).then(reload)}>
            <div className="name">{t.name}{t.id === s.transcriber && <span className="pill busy">Selected</span>}</div>
            <div className="small muted">{t.note}</div>
          </button>
        ))}
      </div>

      <form onSubmit={saveKey}>
        <label htmlFor="key" style={{ display: 'block', fontWeight: 600, marginBottom: 6 }}>{current?.name ?? 'Model'} API key</label>
        <div className="field">
          <input id="key" type="password" autoComplete="off" value={key} onChange={(e) => { setKey(e.target.value); setSaved(false) }}
            placeholder={s.apiKeySet ? 'A key is saved. Paste a new one to replace it' : 'Paste your API key'} />
          <button className="btn dark" type="submit" disabled={!key.trim()}>Save key</button>
        </div>
      </form>
      {saved ? <div className="note ok">Key saved.</div>
        : s.apiKeySet ? <div className="note ok">A key is saved on this computer.</div>
        : <div className="note bad">No key yet. Recordings are kept, but they can’t be transcribed.</div>}

      <div className="rows">
        <div className="row">
          <div className="label"><div>Transcribe after each recording</div><div className="small muted">When off, transcribe from the Meetings window</div></div>
          <button className="switch" role="switch" aria-checked={s.autoTranscribe} aria-label="Transcribe after each recording"
            onClick={() => call('setAutoTranscribe', { value: !s.autoTranscribe }).then(reload)} />
        </div>
        <div className="row">
          <div className="label"><div>Languages</div><div className="small muted">Switching language mid-sentence is fine</div></div>
          <div className="chips"><span className="pill none">Bangla</span><span className="pill none">English</span></div>
        </div>
      </div>
    </div>
  )
}

function Glossary({ s, reload }: { s: SettingsData; reload: () => void }) {
  const [term, setTerm] = useState('')
  const save = (terms: string[]) => call('setGlossary', { terms }).then(reload)

  const add = (e: FormEvent) => {
    e.preventDefault()
    const t = term.trim()
    if (t && !s.glossary.includes(t)) save([...s.glossary, t])
    setTerm('')
  }

  return (
    <div className="page">
      <h1>Glossary</h1>
      <p>Names and technical terms are written exactly like this. It also keeps English words in English letters. Up to about 100 terms works best.</p>
      <form className="field" onSubmit={add} style={{ marginBottom: 18 }}>
        <label htmlFor="term" className="visually-hidden">New term</label>
        <input id="term" value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Add a name, product or project" />
        <button className="btn dark" type="submit" disabled={!term.trim()}>Add</button>
      </form>
      {s.glossary.length === 0 ? <p className="muted">No terms yet.</p> : (
        <div className="chips">
          {s.glossary.map((t) => (
            <span key={t} className="chip">{t}
              <button aria-label={`Remove ${t}`} onClick={() => save(s.glossary.filter((x) => x !== t))}><Icon name="close" /></button>
            </span>
          ))}
        </div>
      )}
      <p className="small muted" style={{ marginTop: 20 }}>{s.glossary.length} term{s.glossary.length === 1 ? '' : 's'}</p>
    </div>
  )
}
