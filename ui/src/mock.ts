import type { HostApi, Meeting, ModelState, Recording, Settings } from './types'

// Made-up sample data for `npm run dev` in a plain browser.
const road: Meeting = {
  id: '2026-10-20_1030',
  folder: '~/Meetings/2026-10-20_1030',
  startedAt: '2026-10-20T10:30:00',
  durationSec: 62,
  status: 'done',
  model: 'gemini-3.5-transcribe',
  preview: 'আজকের মিটিংয়ে আমরা release plan নিয়ে কথা বলবো।',
  segments: [
    { speaker: 'Me', track: 'mic', start: 0, end: 21, failed: '', text: 'আজকের মিটিংয়ে আমরা release plan নিয়ে কথা বলবো। Sprint শেষে demo দিতে হবে, তাই testing টা আগেই শেষ করা দরকার।' },
    { speaker: 'Speaker 1', track: 'system', start: 22, end: 31, failed: '', text: 'ঠিক আছে, আমি backend এর API গুলো আজকেই deploy করে দিচ্ছি।' },
    { speaker: 'Speaker 2', track: 'system', start: 31, end: 40, failed: '', text: 'তারপর QA team কে জানিয়ে দেবো, ওরা কালকে test করবে।' },
    { speaker: 'Me', track: 'mic', start: 41, end: 58, failed: 'empty transcript', text: '' },
  ],
  files: [
    { name: 'display-1.mp4', path: '', kind: 'display', info: '2940×1912' },
    { name: 'mic.m4a', path: '', kind: 'audio', info: 'Me · Mono · 48 kHz' },
    { name: 'system.m4a', path: '', kind: 'audio', info: 'Others · Stereo · 48 kHz' },
  ],
  transcriptText: '',
  local: { 'display-1.mp4': 254_000_000, 'mic.m4a': 7_200_000, 'system.m4a': 11_500_000, 'transcript.json': 23_000 },
  cloud: {
    'display-1.mp4': { size: 254_000_000, etag: 'a-16' }, 'mic.m4a': { size: 7_200_000, etag: 'b-1' },
    'system.m4a': { size: 11_500_000, etag: 'c-1' }, 'transcript.json': { size: 23_000, etag: 'd-1' },
  },
  title: 'Release plan and QA hand-off',
  summary: {
    title: 'Release plan and QA hand-off',
    overview: 'The team agreed to finish testing before the sprint demo. The backend APIs go out today and QA tests them tomorrow.',
    decisions: ['Testing is finished before the demo, not after'],
    actionItems: [{ owner: 'Speaker 1', task: 'Deploy the backend APIs', due: 'today' }, { owner: 'Speaker 2', task: 'Tell the QA team', due: '' }],
    topics: [{ start: 0, title: 'Release plan', summary: 'Demo at the end of the sprint.' }, { start: 22, title: 'Backend deploy', summary: 'APIs go out today; QA tests tomorrow.' }],
    openQuestions: [],
    model: 'claude-opus-5-5',
    createdAt: '2026-10-20T11:00:00Z',
  },
}
const fresh: Meeting = {
  ...road, id: '2026-10-21_1500', folder: '~/Meetings/2026-10-21_1500', startedAt: '2026-10-21T15:00:00',
  durationSec: 8, status: 'none', preview: '', segments: [], model: undefined, cloud: undefined, title: undefined, summary: undefined,
  local: { 'display-1.mp4': 1_065_000, 'mic.m4a': 28_000, 'system.m4a': 2_400 },
}
// A long meeting where Gemini's daily limit ran out part-way: most parts are missing.
const QUOTA = "Gemini's daily limit for gemini-3.5-transcribe is used up (100 requests a day on this API key); it resets in 14 h 59 min. Try again then, or choose another model in Settings → Transcription."
const cut: Meeting = {
  ...road, id: '2026-11-05_2233', folder: '~/Meetings/2026-11-05_2233', startedAt: '2026-11-05T22:33:00',
  durationSec: 6324, status: 'failed', error: QUOTA, preview: 'You can see my screen, right?', title: undefined, summary: undefined, cloud: undefined,
  segments: [
    { speaker: 'Others', track: 'system', start: 0, end: 3, failed: '', text: 'You can see my screen, right?' },
    { speaker: 'Me', track: 'mic', start: 4, end: 6, failed: '', text: 'I can see your screen. Perfect.' },
    ...Array.from({ length: 9 }, (_, i) => ({
      speaker: i % 2 ? 'Me' : 'Others', track: i % 2 ? 'mic' : 'system', start: 10 + i * 12, end: 20 + i * 12,
      failed: "Gemini's daily limit for gemini-3.5-transcribe is used up", text: '',
    })),
  ],
}
// Backed up and freed: the recordings are only in the cloud, the transcript is still here.
const freed: Meeting = {
  ...cut, id: '2026-11-04_2307', folder: '~/Meetings/2026-11-04_2307', startedAt: '2026-11-04T23:07:00', status: 'done', error: undefined,
  files: [], local: { 'transcript.json': 60_000, 'transcript.md': 40_000 }, segments: cut.segments.slice(0, 2), durationSec: 1620,
  cloud: { 'display-1.mp4': { size: 506_895_548, etag: 'e-31' }, 'mic.m4a': { size: 13_716_678, etag: 'f-1' }, 'transcript.json': { size: 60_000, etag: 'g-1' } },
}
// Deleted here earlier (or recorded on another computer): only in the cloud.
const away: Meeting = {
  ...fresh, id: '2026-09-30_1100', folder: '~/Meetings/2026-09-30_1100', startedAt: '2026-09-30T11:00:00', durationSec: 0, files: [], local: {},
  cloud: { 'display-1.mp4': { size: 1_851_113_576, etag: 'h-111' }, 'mic.m4a': { size: 5_113_267, etag: 'i-1' }, 'transcript.json': { size: 22_841, etag: 'j-1' } },
}
const meetings = [cut, freed, fresh, road, away]
cut.local = { 'display-1.mp4': 6_367_871_608, 'mic.m4a': 52_476_457, 'system.m4a': 49_211_218, 'transcript.json': 162_987 }

const emit = (e: string) => (window as unknown as { __hostEvent: (e: string) => void }).__hostEvent(e)

// A pretend run: only the failed parts when onlyFailed, one part every 700 ms, Stop ends it.
let run: ReturnType<typeof setInterval> | undefined
function mockTranscribe(m: Meeting, onlyFailed: boolean) {
  if (m.status === 'transcribing') throw new Error('already transcribing')
  const todo = m.segments.filter((s) => s.failed || !onlyFailed)
  m.status = 'transcribing'
  m.error = undefined
  m.progress = { stage: 'preparing', done: 0, total: 0, failed: 0, waitSec: 0, updatedAt: '' }
  let tick = 0
  run = setInterval(() => {
    const p = m.progress!
    if (tick++ < 2) { p.stage = 'preparing' } else {
      p.stage = 'transcribing'
      p.total = todo.length
      p.waitSec = tick === 5 ? 20 : 0 // once, the per-minute limit
      const s = todo[p.done]
      if (s) { s.text = s.text || `(transcribed again) part at ${s.start} s`; s.failed = ''; p.done++ }
    }
    if (p.stage === 'transcribing' && p.done >= p.total) finish(m)
    emit('meetingsChanged')
  }, 700)
}
function finish(m: Meeting) {
  clearInterval(run)
  m.progress = undefined
  m.status = m.segments.some((s) => s.failed) ? 'failed' : 'done'
  if (m.status === 'failed') m.error = 'Stopped. Some parts are not transcribed yet; Try again sends only those.'
}

const settings: Settings = {
  meetingsFolder: '~/Meetings',
  transcribeMode: 'live',
  transcriber: 'gemini-3.5-transcribe',
  transcribers: [
    { id: 'gemini-3.5-transcribe', name: 'Gemini 3.5 Transcribe', note: 'Tested on our Bangla audio · ~$0.35/hour · a new API key allows only 100 parts a day, less than one meeting', available: true, needsKey: true },
    { id: 'gemini-3.8-flash', name: 'Gemini 3.8 Flash', note: 'Same Gemini key · far higher daily limit · matched Transcribe in a spot check, not benchmarked yet', available: true, needsKey: true },
    { id: 'local-whisper-bangla', name: 'Local Whisper (Bangla)', note: 'On this Mac, free, offline · Bangla only: English words come out in Bangla letters · 1.5 GB', available: true, needsKey: false },
    { id: 'local-whisper-english', name: 'Local Whisper (English)', note: 'On this Mac, free, offline · English-only meetings · 1.6 GB', available: true, needsKey: false },
    { id: 'soniox', name: 'Soniox', note: 'Not added yet', available: false, needsKey: true },
  ],
  apiKeySet: true,
  speakers: true,
  hfTokenSet: false,
  processor: 'gpu',
  processors: [
    { id: 'gpu', name: 'Apple M3', note: 'Faster, and leaves the processor free for your other work', available: true },
    { id: 'cpu', name: 'Apple M3 · 8 cores', note: 'Slower, and keeps several processor cores busy', available: true },
  ],
  glossary: ['API', 'deploy', 'sprint', 'release plan'],
  displays: ['Built-in Retina Display · 2940×1912'],
  microphone: 'MacBook Air Microphone',
  cloudUrl: 'https://example.lambda-url.ap-southeast-1.on.aws/',
  cloudTokenSet: true,
  autoBackup: true,
  anthropicKeySet: true,
  autoSummary: false,
  summaryLanguage: 'auto',
}

// A pretend cloud job: a backup or download moves 5% every 300 ms; freeing drops the recordings.
function mockJob(m: Meeting, kind: 'backup' | 'restore' | 'free' | 'summary') {
  const total = Object.values(m.local).reduce((a, b) => a + b, 0) || 500_000_000
  m.job = { kind, done: 0, total, file: 'display-1.mp4', updatedAt: new Date().toISOString() }
  const tick = setInterval(() => {
    const j = m.job!
    j.done = Math.min(total, (j.done ?? 0) + total / 20)
    j.updatedAt = new Date().toISOString()
    if (j.done >= total) {
      clearInterval(tick)
      m.job = undefined
      if (kind === 'backup') m.cloud = Object.fromEntries(Object.entries(m.local).map(([n, size]) => [n, { size, etag: 'x-1' }]))
      if (kind === 'free') { for (const n of Object.keys(m.local)) if (!n.startsWith('transcript')) delete m.local[n]; m.files = [] }
      if (kind === 'restore') { m.local = { ...m.local, ...Object.fromEntries(Object.entries(m.cloud ?? {}).map(([n, f]) => [n, f.size])) }; m.files = road.files }
      if (kind === 'summary') { m.summary = road.summary; m.title = road.title }
    }
    emit('meetingsChanged')
  }, 300)
}

const models: ModelState[] = [
  { id: 'local-whisper-bangla', downloaded: true },
  { id: 'local-whisper-english', downloaded: false },
  { id: 'speakers-pyannote', downloaded: false },
]

// A pretend 1.6 GB download that finishes in about ten seconds.
function mockDownload(m: ModelState) {
  const total = 1_617_824_864
  m.progress = { done: 0, total: 0 }
  const tick = setInterval(() => {
    const done = Math.min(total, (m.progress?.done ?? 0) + total / 40)
    m.progress = done < total ? { done, total } : undefined
    if (!m.progress) { m.downloaded = true; clearInterval(tick) }
    ;(window as unknown as { __hostEvent: (e: string) => void }).__hostEvent('modelsChanged')
  }, 250)
}

let recording: Recording = { state: 'idle', message: 'Ready' }

export async function mockHost(method: keyof HostApi, params: unknown): Promise<unknown> {
  const p = (params ?? {}) as Record<string, unknown>
  switch (method) {
    case 'checkForUpdate': return { current: '0.3.2', latest: '0.3.3', available: true }
    case 'installUpdate': throw new Error('Updates only work in the app')
    case 'getRecording': return { ...recording }
    case 'startRecording': recording = { state: 'recording', startedAt: new Date().toISOString(), message: 'Recording' }; return
    case 'stopRecording': recording = { state: 'idle', message: 'Saved' }; return
    case 'listMeetings': return meetings.map(({ id, startedAt, durationSec, status, preview, title, local, cloud, job }) =>
      structuredClone({ id, startedAt, durationSec, status, preview, title, local, cloud, job }))
    case 'getMeeting': return structuredClone(meetings.find((m) => m.id === p.id)) // a fresh copy, like the real bridge
    case 'transcribe': mockTranscribe(meetings.find((m) => m.id === p.id)!, Boolean(p.onlyFailed)); return
    case 'stopTranscribing': setTimeout(() => { finish(meetings.find((m) => m.id === p.id)!); emit('meetingsChanged') }, 1500); return
    case 'deleteMeeting': meetings.splice(meetings.findIndex((m) => m.id === p.id), 1); return
    case 'getSettings': return { ...settings }
    case 'setTranscribeMode': settings.transcribeMode = p.mode as Settings['transcribeMode']; return
    case 'setTranscriber': settings.transcriber = p.id as string; return
    case 'getModels': return models.map((m) => ({ ...m }))
    case 'downloadModel': mockDownload(models.find((m) => m.id === p.id)!); return
    case 'setApiKey': settings.apiKeySet = Boolean(p.key); return
    case 'setSpeakers': settings.speakers = Boolean(p.on); return
    case 'setHfToken': settings.hfTokenSet = Boolean(p.token); return
    case 'setProcessor': settings.processor = p.id as Settings['processor']; return
    case 'renameSpeaker': {
      const m = meetings.find((m) => m.id === p.id)!
      m.segments = m.segments.map((s) => (s.speaker === p.from ? { ...s, speaker: p.to as string } : s))
      return
    }
    case 'openLink': window.open(p.url as string); return
    case 'setGlossary': settings.glossary = p.terms as string[]; return
    case 'backup': for (const id of p.ids as string[]) mockJob(meetings.find((m) => m.id === id)!, 'backup'); return
    case 'freeUp': for (const id of p.ids as string[]) mockJob(meetings.find((m) => m.id === id)!, 'free'); return
    case 'restore': mockJob(meetings.find((m) => m.id === p.id)!, 'restore'); return
    case 'summarize': mockJob(meetings.find((m) => m.id === p.id)!, 'summary'); return
    case 'checkCloud': return { meetings: 3, bytes: 793_000_000 }
    case 'forgetCloud':
      for (const id of p.ids as string[]) {
        const m = meetings.find((m) => m.id === id)!
        m.cloud = undefined
        if (Object.keys(m.local).length === 0) meetings.splice(meetings.indexOf(m), 1)
      }
      setTimeout(() => emit('meetingsChanged'), 300)
      return
    case 'syncCloud': setTimeout(() => emit('meetingsChanged'), 300); return
    case 'setCloud': settings.cloudUrl = p.url as string; settings.cloudTokenSet ||= Boolean(p.token); return
    case 'setAutoBackup': settings.autoBackup = Boolean(p.on); return
    case 'setAnthropicKey': settings.anthropicKeySet = Boolean(p.key); return
    case 'setAutoSummary': settings.autoSummary = Boolean(p.on); return
    case 'setSummaryLanguage': settings.summaryLanguage = p.language as Settings['summaryLanguage']; return
    default: return
  }
}
