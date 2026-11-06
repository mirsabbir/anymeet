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
}
const fresh: Meeting = {
  ...road, id: '2026-10-21_1500', folder: '~/Meetings/2026-10-21_1500', startedAt: '2026-10-21T15:00:00',
  durationSec: 8, status: 'none', preview: '', segments: [], model: undefined,
}
// A long meeting where Gemini's daily limit ran out part-way: most parts are missing.
const QUOTA = "Gemini's daily limit for gemini-3.5-transcribe is used up (100 requests a day on this API key); it resets in 14 h 59 min. Try again then, or choose another model in Settings → Transcription."
const cut: Meeting = {
  ...road, id: '2026-11-05_2233', folder: '~/Meetings/2026-11-05_2233', startedAt: '2026-11-05T22:33:00',
  durationSec: 6324, status: 'failed', error: QUOTA, preview: 'You can see my screen, right?',
  segments: [
    { speaker: 'Others', track: 'system', start: 0, end: 3, failed: '', text: 'You can see my screen, right?' },
    { speaker: 'Me', track: 'mic', start: 4, end: 6, failed: '', text: 'I can see your screen. Perfect.' },
    ...Array.from({ length: 9 }, (_, i) => ({
      speaker: i % 2 ? 'Me' : 'Others', track: i % 2 ? 'mic' : 'system', start: 10 + i * 12, end: 20 + i * 12,
      failed: "Gemini's daily limit for gemini-3.5-transcribe is used up", text: '',
    })),
  ],
}
const meetings = [cut, fresh, road]

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
    case 'listMeetings': return meetings.map(({ id, startedAt, durationSec, status, preview }) => ({ id, startedAt, durationSec, status, preview }))
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
    default: return
  }
}
