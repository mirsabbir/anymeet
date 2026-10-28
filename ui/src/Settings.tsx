import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { call, on } from './bridge'
import { bytes } from './format'
import { Icon, type IconName } from './icons'
import type { ModelState, Processor, Settings as SettingsData, TranscribeMode, Transcriber } from './types'
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
const SPEAKER_MODEL = 'speakers-pyannote'
const SPEAKER_MODEL_PAGE = 'https://huggingface.co/pyannote/speaker-diarization-community-1'
const TOKENS_PAGE = 'https://huggingface.co/settings/tokens'
const GPU_SUPPORT = 'gpu-support' // Windows only: PyTorch for NVIDIA cards

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
        <div className="row" style={{ alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <div className="label"><div>Run local models on</div><div className="small muted">Local Whisper and telling speakers apart</div></div>
          <Processors s={s} models={models} reload={reload} />
        </div>
      </div>

      <h2>Speakers</h2>
      <p className="small muted">Your microphone is always “Me”. Everyone else is in the system audio.</p>
      <Speakers s={s} model={models.find((m) => m.id === SPEAKER_MODEL)} reload={reload} />
    </div>
  )
}

function Processors({ s, models, reload }: { s: SettingsData; models: ModelState[]; reload: () => void }) {
  const gpuSupport = models.find((m) => m.id === GPU_SUPPORT)
  const choose = async (p: Processor) => {
    await call('setProcessor', { id: p.id })
    if (p.id === 'gpu' && gpuSupport && !gpuSupport.downloaded && !gpuSupport.progress) await call('downloadModel', { id: GPU_SUPPORT })
    reload()
  }
  return (
    <div className="modes" role="radiogroup" aria-label="Run local models on">
      {s.processors.map((p) => (
        <button key={p.id} role="radio" aria-checked={s.processor === p.id} className="mode" disabled={!p.available} onClick={() => choose(p)}>
          <span className="mode-title">{p.id === 'gpu' ? 'Graphics card (GPU)' : 'Processor (CPU)'} · {p.name}</span>
          <span className="small muted">{p.note}</span>
          {p.id === 'gpu' && p.available && gpuSupport && <Download m={gpuSupport} />}
        </button>
      ))}
    </div>
  )
}

function Speakers({ s, model, reload }: { s: SettingsData; model?: ModelState; reload: () => void }) {
  const [token, setToken] = useState('')
  const [saved, setSaved] = useState(false)
  const download = () => call('downloadModel', { id: SPEAKER_MODEL })
  const needsDownload = model && !model.downloaded && !model.progress

  const toggle = async () => {
    await call('setSpeakers', { on: !s.speakers })
    if (!s.speakers && s.hfTokenSet && needsDownload) await download()
    reload()
  }
  const saveToken = async (e: FormEvent) => {
    e.preventDefault()
    await call('setHfToken', { token: token.trim() })
    setToken('')
    setSaved(true)
    if (needsDownload) await download()
    reload()
  }

  return (
    <>
      <div className="rows">
        <div className="row">
          <div className="label">
            <div>Tell speakers apart</div>
            <div className="small muted">Splits “Others” into Speaker 1, Speaker 2… by voice, on this computer, when the meeting ends. Rename them on the meeting page.</div>
          </div>
          <button role="switch" aria-checked={s.speakers} aria-label="Tell speakers apart" className="switch" onClick={toggle}><span /></button>
        </div>
        {s.speakers && model && (
          <div className="row" style={{ flexWrap: 'wrap' }}>
            <div className="label"><div>Speaker model</div><div className="small muted">pyannote community-1 · about 33 MB · free</div></div>
            {model.progress ? <div style={{ flex: '1 1 220px' }}><Download m={model} /></div>
              : model.downloaded ? <span className="small" style={{ color: 'var(--ok)', display: 'inline-flex', gap: 6 }}><Icon name="check" />Downloaded</span>
              : <button className="btn" onClick={download} disabled={!s.hfTokenSet}><Icon name="download" />{model.error ? 'Try again' : 'Download'}</button>}
          </div>
        )}
      </div>
      {s.speakers && model?.error && !model.progress && <div className="note bad">Download failed: {model.error}</div>}
      {s.speakers && model && !model.downloaded && (
        <div className="setup">
          <div style={{ fontWeight: 600, marginBottom: 8 }}>One-time setup with a free Hugging Face account</div>
          <ol>
            <li>Sign in and accept the model’s terms: <button className="link" onClick={() => call('openLink', { url: SPEAKER_MODEL_PAGE })}>pyannote/speaker-diarization-community-1</button></li>
            <li>Create a token of type “Read”: <button className="link" onClick={() => call('openLink', { url: TOKENS_PAGE })}>huggingface.co/settings/tokens</button></li>
            <li>Paste the token here. The model then downloads, and after that it works offline.</li>
          </ol>
          <form onSubmit={saveToken}>
            <label htmlFor="hf-token" className="visually-hidden">Hugging Face token</label>
            <div className="field">
              <input id="hf-token" type="password" autoComplete="off" value={token} onChange={(e) => { setToken(e.target.value); setSaved(false) }}
                placeholder={s.hfTokenSet ? 'A token is saved. Paste a new one to replace it' : 'Paste your Hugging Face token (hf_…)'} />
              <button className="btn dark" type="submit" disabled={!token.trim()}>Save token</button>
            </div>
          </form>
          {(saved || s.hfTokenSet) && <div className="note ok" style={{ marginBottom: 0 }}>{saved ? 'Token saved.' : 'A token is saved on this computer.'}</div>}
        </div>
      )}
    </>
  )
}

function Download({ m }: { m: ModelState }) {
  const gpu = m.id === GPU_SUPPORT // installed by uv in one go, so there are no byte counts
  if (m.progress) {
    const { done, total } = m.progress
    const pct = total ? Math.floor((100 * done) / total) : 0
    return (
      <div className="download">
        <div className="small">{total ? `Downloading ${pct}% · ${bytes(done)} of ${bytes(total)}`
          : gpu ? 'Installing GPU support (about 2.6 GB, once). This can take a while…' : 'Getting ready to download…'}</div>
        <div className="bar" role="progressbar" aria-label={`Downloading ${m.id}`} aria-valuenow={pct}><span style={{ width: `${pct}%` }} /></div>
      </div>
    )
  }
  if (m.downloaded) return <div className="download small ok"><Icon name="check" />{gpu ? 'GPU support installed' : 'Downloaded'}</div>
  return (
    <div className={`download small ${m.error ? 'bad' : 'muted'}`}>
      <Icon name="download" />{m.error ? `${gpu ? 'Install' : 'Download'} failed: ${m.error} · click to try again`
        : gpu ? 'GPU support not installed · click to install (about 2.6 GB)' : 'Not downloaded · click to download'}
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
