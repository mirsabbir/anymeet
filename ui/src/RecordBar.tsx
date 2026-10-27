import { useCallback, useEffect, useState } from 'react'
import { call, on } from './bridge'
import type { Recording } from './types'

function elapsed(since: string): string {
  const s = Math.max(0, Math.floor((Date.now() - Date.parse(since)) / 1000))
  const pad = (n: number) => String(n).padStart(2, '0')
  const h = Math.floor(s / 3600)
  return (h ? `${h}:` : '') + `${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`
}

/** Start/stop recording from the window: same action as a left click on the tray icon. */
export function RecordBar() {
  const [rec, setRec] = useState<Recording>({ state: 'idle', message: '' })
  const [, tick] = useState(0)
  const [error, setError] = useState('')

  const refresh = useCallback(() => { call('getRecording').then(setRec) }, [])
  useEffect(() => { refresh(); return on('recordingChanged', refresh) }, [refresh])
  useEffect(() => {
    if (rec.state !== 'recording') return
    const timer = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(timer)
  }, [rec.state])

  const run = (method: 'startRecording' | 'stopRecording') => {
    setError('')
    setRec((r) => ({ ...r, state: method === 'startRecording' ? 'starting' : 'stopping' }))
    call(method).catch((e: Error) => setError(e.message)).finally(refresh)
  }

  const problem = error || (rec.state === 'idle' && rec.message.startsWith('⚠️') ? rec.message.replace('⚠️ ', '') : '')

  return (
    <div className="record-bar">
      {rec.state === 'recording' || rec.state === 'stopping' ? (
        <>
          <div className="rec-live" aria-live="polite">
            <span className="rec-dot" aria-hidden="true" />
            <span>Recording</span>
            <span className="rec-time">{rec.startedAt ? elapsed(rec.startedAt) : ''}</span>
          </div>
          <button className="btn rec-stop" onClick={() => run('stopRecording')} disabled={rec.state === 'stopping'}>
            <span className="stop-square" aria-hidden="true" />{rec.state === 'stopping' ? 'Saving…' : 'Stop'}
          </button>
        </>
      ) : (
        <button className="btn rec-start" onClick={() => run('startRecording')} disabled={rec.state === 'starting'}>
          <span className="rec-dot" aria-hidden="true" />{rec.state === 'starting' ? 'Starting…' : 'Start recording'}
        </button>
      )}
      {problem && <p className="rec-problem small" role="alert">{problem}</p>}
    </div>
  )
}
