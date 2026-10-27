// The contract between this UI and the native host (macOS now, Windows later).
// Every host implements these methods and events; see bridge.ts for transport.

export interface Segment {
  speaker: string
  start: number // seconds from the start of the meeting
  end: number
  text: string
  failed: string // non-empty when this part could not be transcribed
}

export type TranscriptStatus = 'none' | 'transcribing' | 'done' | 'failed'

export interface MeetingSummary {
  id: string // folder name, e.g. 2026-10-26_2121
  startedAt: string // ISO 8601
  durationSec: number
  status: TranscriptStatus
  preview: string // start of the transcript, for the list and search
}

export interface MeetingFile {
  name: string
  path: string
  kind: 'display' | 'audio'
  info: string // e.g. "2940×1912 · HEVC" or "Mono · 48 kHz"
  thumbnail?: string // data: URL of a video frame
}

export interface Meeting extends MeetingSummary {
  folder: string
  model?: string
  segments: Segment[]
  files: MeetingFile[]
  transcriptText: string // plain text for copying
}

export interface Transcriber {
  id: string
  name: string
  note: string
  available: boolean
}

export interface Settings {
  meetingsFolder: string
  autoTranscribe: boolean
  transcriber: string
  transcribers: Transcriber[]
  apiKeySet: boolean
  glossary: string[]
  displays: string[]
  microphone: string
}

export type RecordingState = 'idle' | 'starting' | 'recording' | 'stopping'

export interface Recording {
  state: RecordingState
  startedAt?: string // ISO 8601 with time zone, while recording
  message: string // the tray's last status line; starts with ⚠️ for problems
}

export interface UpdateInfo {
  current: string // this build's version, e.g. "0.3.2"
  latest?: string // newest release on GitHub, when the check worked
  available: boolean
  error?: string // why the check failed, e.g. offline
}

export interface HostApi {
  checkForUpdate(p: { force?: boolean }): UpdateInfo // cached for an hour unless forced
  installUpdate(): void // downloads, replaces the app and relaunches; rejects while recording
  getRecording(): Recording
  startRecording(): void // resolves once recording has started or failed
  stopRecording(): void // resolves once the files are closed
  listMeetings(): MeetingSummary[]
  getMeeting(p: { id: string }): Meeting
  transcribe(p: { id: string }): void
  deleteMeeting(p: { id: string }): void // moves the folder to the Trash / Recycle Bin
  openPath(p: { path: string }): void
  getSettings(): Settings
  setAutoTranscribe(p: { value: boolean }): void
  setTranscriber(p: { id: string }): void
  setApiKey(p: { key: string }): void
  setGlossary(p: { terms: string[] }): void
}

// Pushed by the host when something changes outside the UI.
export type HostEvent = 'meetingsChanged' | 'settingsChanged' | 'recordingChanged'
