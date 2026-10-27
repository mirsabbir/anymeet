import { useCallback, useEffect, useState } from 'react'
import { call } from './bridge'
import { Icon } from './icons'
import type { UpdateInfo } from './types'

const SIX_HOURS = 6 * 60 * 60 * 1000

/** Checks GitHub on open and every six hours; the host caches checks for an hour. */
export function useUpdate() {
  const [info, setInfo] = useState<UpdateInfo>()
  const [checking, setChecking] = useState(false)
  const check = useCallback((force = false) => {
    setChecking(true)
    return call('checkForUpdate', { force }).then(setInfo).finally(() => setChecking(false))
  }, [])
  useEffect(() => {
    check()
    const timer = setInterval(() => check(), SIX_HOURS)
    return () => clearInterval(timer)
  }, [check])
  return { info, checking, check }
}

/** "Update & restart" button with its own progress and error state. */
export function InstallButton({ version }: { version: string }) {
  const [state, setState] = useState<'idle' | 'working' | 'error'>('idle')
  const [error, setError] = useState('')
  const install = () => {
    setState('working')
    call('installUpdate').catch((e: Error) => { setError(e.message); setState('error') })
  }
  return (
    <>
      <button className="btn primary" onClick={install} disabled={state === 'working'}>
        <Icon name="refresh" />{state === 'working' ? `Downloading ${version}…` : 'Update & restart'}
      </button>
      {state === 'error' && <span className="small" style={{ flexBasis: '100%', color: 'var(--warn)' }}>{error}</span>}
    </>
  )
}

export function UpdateBanner() {
  const { info } = useUpdate()
  if (!info?.available || !info.latest) return null
  return (
    <div className="banner" role="status">
      <Icon name="refresh" />
      <span style={{ flex: 1 }}><strong>AnyMeet {info.latest} is available.</strong> You have {info.current}.</span>
      <InstallButton version={info.latest} />
    </div>
  )
}
