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
    { speaker: 'Me', start: 0, end: 21, failed: '', text: 'আজকের মিটিংয়ে আমরা release plan নিয়ে কথা বলবো। Sprint শেষে demo দিতে হবে, তাই testing টা আগেই শেষ করা দরকার।' },
    { speaker: 'Others', start: 22, end: 40, failed: '', text: 'ঠিক আছে, আমি backend এর API গুলো আজকেই deploy করে দিচ্ছি। তারপর QA team কে জানিয়ে দেবো।' },
    { speaker: 'Me', start: 41, end: 58, failed: 'empty transcript', text: '' },
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
const meetings = [fresh, road]

const settings: Settings = {
  meetingsFolder: '~/Meetings',
  transcribeMode: 'live',
  transcriber: 'gemini-3.5-transcribe',
  transcribers: [
    { id: 'gemini-3.5-transcribe', name: 'Gemini 3.5 Transcribe', note: 'Tested on our Bangla audio · ~$0.35/hour', available: true, needsKey: true },
    { id: 'local-whisper-bangla', name: 'Local Whisper (Bangla)', note: 'On this Mac, free, offline · Bangla only: English words come out in Bangla letters · 1.5 GB', available: true, needsKey: false },
    { id: 'local-whisper-english', name: 'Local Whisper (English)', note: 'On this Mac, free, offline · English-only meetings · 1.6 GB', available: true, needsKey: false },
    { id: 'soniox', name: 'Soniox', note: 'Not added yet', available: false, needsKey: true },
  ],
  apiKeySet: true,
  glossary: ['API', 'deploy', 'sprint', 'release plan'],
  displays: ['Built-in Retina Display · 2940×1912'],
  microphone: 'MacBook Air Microphone',
}

const models: ModelState[] = [
  { id: 'local-whisper-bangla', downloaded: true },
  { id: 'local-whisper-english', downloaded: false },
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
    case 'getMeeting': return meetings.find((m) => m.id === p.id)
    case 'deleteMeeting': meetings.splice(meetings.findIndex((m) => m.id === p.id), 1); return
    case 'getSettings': return { ...settings }
    case 'setTranscribeMode': settings.transcribeMode = p.mode as Settings['transcribeMode']; return
    case 'setTranscriber': settings.transcriber = p.id as string; return
    case 'getModels': return models.map((m) => ({ ...m }))
    case 'downloadModel': mockDownload(models.find((m) => m.id === p.id)!); return
    case 'setApiKey': settings.apiKeySet = Boolean(p.key); return
    case 'setGlossary': settings.glossary = p.terms as string[]; return
    default: return
  }
}
