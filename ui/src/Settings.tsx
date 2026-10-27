import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { call, on } from './bridge'
import { bytes } from './format'
import { Icon, type IconName } from './icons'
import type { ModelState, Settings as SettingsData, TranscribeMode, Transcriber } from './types'
import { InstallButton, useUpdate } from './Update'

const TABS: [Tab, string, IconName][] = [
  ['recording', 'Recording', 'record'],
  ['transcription', 'Transcription', 'mic'],
  ['glossary', 'Glossary', 'search'],
  ['about', 'About & updates', 'refresh'],
]
const MODES: [TranscribeMode, string, string][] = [
  ['live', 'Live, while recording', 'Text appears during the meeting; the transcript is ready seconds after you stop'],
  ['after', 'After the meeting', 'The whole recording is transcribed when you stop'],
  ['off', 'Off', 'Transcribe later from the meeting page'],
]
type Tab = 'recording' | 'transcription' | 'glossary' | 'about'

export function Settings({ initialTab }: { initialTab?: string }) {
  const [tab, setTab] = useState<Tab>(TABS.some(([id]) => id === initialTab) ? (initialTab as Tab) : 'recording')
  const [s, setS] = useState<SettingsData>()
  const load = useCallback(() => { call('getSettings').then(setS) }, [])
  useEffect(() => { load(); return on('settingsChanged', load) }, [load])

  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Settings sections">
        <a className="tab back" href="#/meetings"><Icon name="back" />Meetings</a>
        <div className="eyebrow">Settings</div>
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
        {tab === 'about' && <About />}
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
  const [models, setModels] = useState<ModelState[]>([])
  useEffect(() => {
    call('getModels', { refresh: true }).then(setModels)
    return on('modelsChanged', () => { call('getModels', {}).then(setModels) })
  }, [])

  // Choosing a local model that isn't on this computer yet starts its download.
  const choose = async (t: Transcriber) => {
    await call('setTranscriber', { id: t.id })
    const m = models.find((m) => m.id === t.id)
    if (m && !m.downloaded && !m.progress) await call('downloadModel', { id: t.id })
    reload()
  }

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
            onClick={() => choose(t)}>
            <div className="name">{t.name}{t.id === s.transcriber && <span className="pill busy">Selected</span>}</div>
            <div className="small muted">{t.note}</div>
            {models.filter((m) => m.id === t.id).map((m) => <Download key={m.id} m={m} />)}
          </button>
        ))}
      </div>

      {current?.needsKey && (
        <>
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

        </>
      )}
      <div className="rows">
        <div className="row" style={{ alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <div className="label"><div>When to transcribe</div><div className="small muted">Works with every model above</div></div>
          <div className="modes" role="radiogroup" aria-label="When to transcribe">
            {MODES.map(([mode, title, note]) => (
              <button key={mode} role="radio" aria-checked={s.transcribeMode === mode} className="mode"
                onClick={() => call('setTranscribeMode', { mode }).then(reload)}>
                <span className="mode-title">{title}</span>
                <span className="small muted">{note}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="row">
          <div className="label"><div>Languages</div><div className="small muted">Switching language mid-sentence is fine</div></div>
          <div className="chips"><span className="pill none">Bangla</span><span className="pill none">English</span></div>
        </div>
      </div>
    </div>
  )
}

function Download({ m }: { m: ModelState }) {
  if (m.progress) {
    const { done, total } = m.progress
    const pct = total ? Math.floor((100 * done) / total) : 0
    return (
      <div className="download">
        <div className="small">{total ? `Downloading ${pct}% · ${bytes(done)} of ${bytes(total)}` : 'Getting ready to download…'}</div>
        <div className="bar" role="progressbar" aria-label={`Downloading ${m.id}`} aria-valuenow={pct}><span style={{ width: `${pct}%` }} /></div>
      </div>
    )
  }
  if (m.downloaded) return <div className="download small ok"><Icon name="check" />Downloaded</div>
  return (
    <div className={`download small ${m.error ? 'bad' : 'muted'}`}>
      <Icon name="download" />{m.error ? `Download failed: ${m.error} · click to try again` : 'Not downloaded · click to download'}
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

function About() {
  const { info, checking, check } = useUpdate()
  return (
    <div className="page">
      <h1>About & updates</h1>
      <p>AnyMeet checks GitHub for a new release when it opens and every six hours.</p>
      <div className="rows">
        <div className="row">
          <div className="label"><div>Version</div><div className="small muted">This build</div></div>
          <div className="value">{info?.current ?? '…'}</div>
        </div>
        <div className="row">
          <div className="label">
            <div>Latest release</div>
            <div className="small muted">
              {checking ? 'Checking…' : info?.error ? `Couldn’t check: ${info.error}` : info?.available ? 'A new version is ready to install' : 'You’re up to date'}
            </div>
          </div>
          <div className="value">{info?.latest ?? '–'}</div>
        </div>
        <div className="row" style={{ flexWrap: 'wrap' }}>
          <div className="label"><div>Update</div><div className="small muted">Downloads the new version, replaces the app and reopens it. Not possible while recording.</div></div>
          <button className="btn" onClick={() => check(true)} disabled={checking}><Icon name="refresh" />Check now</button>
          {info?.available && info.latest && <InstallButton version={info.latest} />}
        </div>
      </div>
    </div>
  )
}
