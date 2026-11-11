// The contract between this UI and the native host (macOS now, Windows later).
// Every host implements these methods and events; see bridge.ts for transport.

export interface Segment {
  speaker: string // "Me", "Others", "Speaker 1"… or the name the user gave
  start: number // seconds from the start of the meeting
  end: number
  text: string
  failed: string // non-empty when this part could not be transcribed
  track?: string // the audio file's name without extension (mic, system); absent in older transcripts
}

export type TranscriptStatus = 'none' | 'transcribing' | 'done' | 'failed'

export interface MeetingSummary {
  id: string // folder name, e.g. 2026-10-26_2121
  startedAt: string // ISO 8601
  durationSec: number
  status: TranscriptStatus
  preview: string // start of the transcript, for the list and search
  title?: string // the AI summary's title, when there is one
  local: Record<string, number> // the files directly in the meeting folder, with their sizes in bytes
  cloud?: Record<string, CloudFile> // what the cloud holds, by path in the folder: <meeting>/cloud.json "files"
  job?: MeetingJob // a background job on the meeting, or why the last one failed
}

/** A verified cloud copy of a file (transcriber/cloud.py). */
export interface CloudFile {
  size: number
  etag: string
}

/** <meeting>/job.json (transcriber/job.py): running while updatedAt keeps moving (within 30 s). */
export interface MeetingJob {
  kind: 'backup' | 'restore' | 'free' | 'forget' | 'summary'
  done?: number // bytes so far, of total
  total?: number
  file?: string // the file being sent or fetched
  waiting?: boolean // queued behind another meeting of the same run
  error?: string // the job failed, and why, in words the user can act on
  updatedAt: string
}

/** <meeting>/summary.json, written by Claude from the transcript (transcriber/summary.py). */
export interface Summary {
  title: string
  overview: string
  decisions: string[]
  actionItems: { owner: string; task: string; due: string }[]
  topics: { start: number; title: string; summary: string }[]
  openQuestions: string[]
  model: string
  createdAt: string
}

export interface MeetingFile {
  name: string
  path: string
  kind: 'display' | 'audio'
  info: string // e.g. "2940×1912 · HEVC" or "Mono · 48 kHz"
  url?: string // where the page can play it (served by the host, decoded by the system); absent if it can't
  thumbnail?: string // data: URL of a video frame
}

/** What a running transcription is doing: the transcriber's <meeting>/progress.json. */
export interface TranscribeProgress {
  stage: 'preparing' | 'speakers' | 'transcribing'
  done: number // parts back from the provider, transcribed or not
  total: number // parts to send; 0 while preparing, and it grows with the recording in live mode
  failed: number // of `done`, the parts that could not be transcribed
  waitSec: number // > 0 while every request waits out the provider's per-minute limit
  updatedAt: string
}

export interface Meeting extends MeetingSummary {
  folder: string
  model?: string
  segments: Segment[]
  files: MeetingFile[]
  transcriptText: string // plain text for copying
  error?: string // last line of transcribe.log when transcription failed: the transcriber says why there
  progress?: TranscribeProgress // while status is 'transcribing', once the transcriber has started
  summary?: Summary
}

export interface Transcriber {
  id: string
  name: string
  note: string
  available: boolean
  needsKey: boolean // false for local models
}

/** A local model's files on this computer. */
export interface ModelState {
  // a Transcriber id (e.g. local-whisper-english), 'speakers-pyannote', or on Windows
  // 'gpu-support' (PyTorch for NVIDIA cards, installed instead of downloaded)
  id: string
  downloaded: boolean
  progress?: { done: number; total: number } // bytes, while downloading; total is 0 while getting ready
  error?: string // why the last download failed
}

/** Where local models (Whisper, speaker identification) run. */
export interface Processor {
  id: 'gpu' | 'cpu'
  name: string // e.g. "Apple M3", "NVIDIA GeForce RTX 4060"
  note: string
  available: boolean // false when there is no graphics card the models can use
}

/** live: while recording · after: when recording stops · off: only by hand */
export type TranscribeMode = 'live' | 'after' | 'off'

export interface Settings {
  meetingsFolder: string
  transcribeMode: TranscribeMode
  transcriber: string
  transcribers: Transcriber[]
  apiKeySet: boolean
  speakers: boolean // split "Others" into Speaker 1, Speaker 2…
  hfTokenSet: boolean // a Hugging Face token is saved (needed once, to download the speaker model)
  processor: 'gpu' | 'cpu'
  processors: Processor[]
  glossary: string[]
  displays: string[]
  microphone: string
  cloudUrl: string // the AnyMeet Cloud server; "" when cloud backup isn't set up
  cloudTokenSet: boolean
  autoBackup: boolean // back up each meeting once its transcript is ready (or recording stops, with transcription off)
  anthropicKeySet: boolean
  autoSummary: boolean // summarize each meeting once its transcript is ready, if it has no summary yet
  summaryLanguage: SummaryLanguage
}

/** auto: the language most of the meeting was in */
export type SummaryLanguage = 'auto' | 'en' | 'bn'

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
  transcribe(p: { id: string; onlyFailed?: boolean }): void // onlyFailed: send again only the parts marked not transcribed
  stopTranscribing(p: { id: string }): void // nothing more is sent; parts already sent finish, so it ends within seconds
  deleteMeeting(p: { id: string; cloudToo?: boolean }): void // moves the folder to the Trash / Recycle Bin; cloudToo deletes the cloud copy first
  openPath(p: { path: string }): void
  getSettings(): Settings
  setTranscribeMode(p: { mode: TranscribeMode }): void
  setTranscriber(p: { id: string }): void
  getModels(p: { refresh?: boolean }): ModelState[] // local models; refresh re-checks the disk (about a second)
  downloadModel(p: { id: string }): void // returns at once; progress arrives with 'modelsChanged'
  setApiKey(p: { key: string }): void
  setSpeakers(p: { on: boolean }): void
  setHfToken(p: { token: string }): void
  setProcessor(p: { id: 'gpu' | 'cpu' }): void
  renameSpeaker(p: { id: string; from: string; to: string }): void // in that meeting's transcript; same name = merge
  openLink(p: { url: string }): void // in the default browser; https://huggingface.co only
  setGlossary(p: { terms: string[] }): void
  // Cloud backup and summaries run as `anymeet` jobs and report through MeetingSummary.job.
  backup(p: { ids: string[] }): void // sends what is new or changed, one meeting after another
  restore(p: { id: string; files?: string[] }): void // downloads the files named, or everything missing here
  freeUp(p: { ids: string[] }): void // deletes the recordings here that are safely in the cloud
  stopJob(p: { id: string }): void
  summarize(p: { id: string }): void
  checkCloud(): { meetings: number; bytes: number } // rejects with the reason when the server can't be reached
  setCloud(p: { url: string; token: string }): void // token "" keeps the saved one
  setAutoBackup(p: { on: boolean }): void
  setAnthropicKey(p: { key: string }): void
  setAutoSummary(p: { on: boolean }): void
  setSummaryLanguage(p: { language: SummaryLanguage }): void
}

// Pushed by the host when something changes outside the UI.
export type HostEvent = 'meetingsChanged' | 'settingsChanged' | 'recordingChanged' | 'modelsChanged'
