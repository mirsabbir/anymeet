import type { HostApi, HostEvent } from './types'
import { mockHost } from './mock'

// Transport to the native host:
// - macOS WKWebView: postMessage returns a promise (WKScriptMessageHandlerWithReply).
// - Windows WebView2: messages carry an id; the host answers with the same id.
// - A plain browser (npm run dev): sample data, so the UI can be designed without a host.
type Method = keyof HostApi
type Params<M extends Method> = Parameters<HostApi[M]>[0]
type Result<M extends Method> = ReturnType<HostApi[M]>

interface Webkit { messageHandlers: { bridge: { postMessage(m: unknown): Promise<unknown> } } }
interface WebView2 {
  postMessage(m: unknown): void
  addEventListener(type: 'message', cb: (e: { data: unknown }) => void): void
}
const w = window as unknown as { webkit?: Webkit; chrome?: { webview?: WebView2 } }

const pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>()
let nextId = 1
w.chrome?.webview?.addEventListener('message', ({ data }) => {
  const msg = data as { id?: number; result?: unknown; error?: string; event?: HostEvent }
  if (msg.event) return emit(msg.event)
  const p = msg.id !== undefined ? pending.get(msg.id) : undefined
  if (!p) return
  pending.delete(msg.id!)
  if (msg.error) p.reject(new Error(msg.error))
  else p.resolve(msg.result)
})

export function call<M extends Method>(method: M, params?: Params<M>): Promise<Result<M>> {
  if (w.webkit?.messageHandlers?.bridge) {
    return w.webkit.messageHandlers.bridge.postMessage({ method, params: params ?? {} }) as Promise<Result<M>>
  }
  const webview = w.chrome?.webview
  if (webview) {
    const id = nextId++
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve: resolve as (v: unknown) => void, reject })
      webview.postMessage({ id, method, params: params ?? {} })
    })
  }
  return mockHost(method, params) as Promise<Result<M>>
}

const listeners = new Map<HostEvent, Set<() => void>>()

function emit(event: HostEvent) {
  listeners.get(event)?.forEach((cb) => cb())
}

export function on(event: HostEvent, cb: () => void): () => void {
  if (!listeners.has(event)) listeners.set(event, new Set())
  listeners.get(event)!.add(cb)
  return () => listeners.get(event)!.delete(cb)
}

// macOS pushes events by evaluating window.__hostEvent("meetingsChanged").
;(window as unknown as { __hostEvent: (e: HostEvent) => void }).__hostEvent = emit
