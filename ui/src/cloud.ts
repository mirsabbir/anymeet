import type { MeetingJob, MeetingSummary } from './types'

const AUDIO = /\.(m4a|mp3|wav|flac|aac)$/i
const KEPT = ['transcript.json', 'summary.json'] // small files that stay on this computer

/** The big files "Free up" removes: the videos and audio tracks the app records (transcriber/cloud.py). */
export const isRecording = (name: string) => !name.includes('/') && (name.startsWith('display-') || AUDIO.test(name))

/** A job runs while job.py keeps its file fresh; an old file without an error is left over from a killed run. */
export function activeJob(job: MeetingJob | undefined, now = Date.now()): MeetingJob | undefined {
  return job && !job.error && now - Date.parse(job.updatedAt) < 30_000 ? job : undefined
}

export interface CloudView {
  /** none: nothing in the cloud · partial: some · saved: all of what is here · cloudOnly: nothing of it here */
  state: 'none' | 'partial' | 'saved' | 'cloudOnly'
  freeable: number // bytes of recordings here that the cloud holds too
  missing: string[] // recordings only in the cloud
  missingBytes: number
  transcriptInCloud: boolean // the transcript is in the cloud and not here
  job?: MeetingJob // running now
  failed?: MeetingJob // the last job, which failed
}

/** Where a meeting's files are. Sizes are compared here; `anymeet free` checks with the server before deleting. */
export function cloudView(m: MeetingSummary): CloudView {
  const cloud = m.cloud ?? {}
  const inCloud = (name: string) => cloud[name] !== undefined && cloud[name].size === m.local[name]
  const here = Object.keys(m.local).filter((name) => isRecording(name) || KEPT.includes(name))
  const missing = Object.keys(cloud).filter((name) => isRecording(name) && m.local[name] === undefined)
  const state = Object.keys(cloud).length === 0 ? 'none' : here.length === 0 ? 'cloudOnly' : here.every(inCloud) ? 'saved' : 'partial'
  return {
    state,
    freeable: here.filter((name) => isRecording(name) && inCloud(name)).reduce((sum, name) => sum + m.local[name], 0),
    missing,
    missingBytes: missing.reduce((sum, name) => sum + cloud[name].size, 0),
    transcriptInCloud: cloud['transcript.json'] !== undefined && m.local['transcript.json'] === undefined,
    job: activeJob(m.job),
    failed: m.job?.error ? m.job : undefined,
  }
}

/** Meetings with something here that isn't in the cloud yet. */
export const needsBackup = (m: MeetingSummary) => {
  const v = cloudView(m)
  return !v.job && (v.state === 'none' || v.state === 'partial') && Object.keys(m.local).some((n) => isRecording(n) || KEPT.includes(n))
}

export const JOB_TEXT: Record<MeetingJob['kind'], [running: string, failed: string]> = {
  backup: ['Backing up to the cloud', 'The backup didn’t finish'],
  restore: ['Downloading from the cloud', 'The download didn’t finish'],
  free: ['Freeing up space', 'Freeing up space didn’t finish'],
  forget: ['Deleting the cloud copy', 'Deleting the cloud copy didn’t finish'],
  summary: ['Writing the summary with Claude…', 'No summary'],
}
