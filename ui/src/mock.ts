import type { HostApi, Meeting, Recording, Settings } from './types'

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
  autoTranscribe: true,
  transcriber: 'gemini-3.5-transcribe',
  transcribers: [
    { id: 'gemini-3.5-transcribe', name: 'Gemini 3.5 Transcribe', note: 'Tested on our Bangla audio · ~$0.35/hour', available: true },
    { id: 'soniox', name: 'Soniox', note: 'Not added yet', available: false },
    { id: 'sarvam', name: 'Sarvam', note: 'Not added yet', available: false },
    { id: 'local-whisper', name: 'Local Whisper', note: 'Not added yet', available: false },
  ],
  apiKeySet: true,
  glossary: ['API', 'deploy', 'sprint', 'release plan'],
  displays: ['Built-in Retina Display · 2940×1912'],
  microphone: 'MacBook Air Microphone',
}

let recording: Recording = { state: 'idle', message: 'Ready' }

export async function mockHost(method: keyof HostApi, params: unknown): Promise<unknown> {
  const p = (params ?? {}) as Record<string, unknown>
  switch (method) {
    case 'getRecording': return { ...recording }
    case 'startRecording': recording = { state: 'recording', startedAt: new Date().toISOString(), message: 'Recording' }; return
    case 'stopRecording': recording = { state: 'idle', message: 'Saved' }; return
    case 'listMeetings': return meetings.map(({ id, startedAt, durationSec, status, preview }) => ({ id, startedAt, durationSec, status, preview }))
    case 'getMeeting': return meetings.find((m) => m.id === p.id)
    case 'getSettings': return { ...settings }
    case 'setAutoTranscribe': settings.autoTranscribe = p.value as boolean; return
    case 'setTranscriber': settings.transcriber = p.id as string; return
    case 'setApiKey': settings.apiKeySet = Boolean(p.key); return
    case 'setGlossary': settings.glossary = p.terms as string[]; return
    default: return
  }
}
