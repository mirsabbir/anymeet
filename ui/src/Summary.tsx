import { useState } from 'react'
import { clock } from './format'
import { Icon } from './icons'
import type { Meeting, Summary } from './types'

/** The AI summary above the transcript; or a button to write one, once there is a transcript. */
export function SummaryCard({ meeting, keySet, writing, onSummarize, onPlay }: {
  meeting: Meeting; keySet: boolean; writing: boolean; onSummarize: () => void; onPlay: (at: number) => void
}) {
  const [copied, setCopied] = useState(false)
  const s = meeting.summary
  const hasWords = meeting.segments.some((seg) => seg.text) && meeting.status !== 'transcribing'
  if (writing) {
    return <div className="summary-bar"><span className="spinner small-spinner" />Claude is writing the summary… a long meeting takes a minute or two.</div>
  }
  if (!s) {
    if (!hasWords) return null
    return (
      <div className="summary-bar">
        <Icon name="sparkle" />
        <span style={{ flex: 1 }}>{keySet ? 'Summary: decisions, action items and topics, written by Claude.' : 'Add an Anthropic API key in Settings → Cloud & AI to summarize meetings.'}</span>
        {keySet ? <button className="btn" onClick={onSummarize}>Summarize</button> : <a className="btn" href="#/settings/cloud">Open Settings</a>}
      </div>
    )
  }
  const copy = async () => {
    await navigator.clipboard.writeText(summaryText(s))
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <details className="summary" open>
      <summary><Icon name="sparkle" /><span className="summary-title">{s.title}</span></summary>
      <p>{s.overview}</p>
      <List title="Decisions" items={s.decisions} />
      {s.actionItems.length > 0 && (
        <section><h3>Action items</h3><ul>{s.actionItems.map((a, i) => (
          <li key={i}><b>{a.owner}</b>: {a.task}{a.due && <span className="muted"> · {a.due}</span>}</li>
        ))}</ul></section>
      )}
      {s.topics.length > 0 && (
        <section><h3>Topics</h3><ul className="topics">{s.topics.map((t, i) => (
          <li key={i}>
            <button className="at" onClick={() => onPlay(t.start)} aria-label={`Play from ${clock(t.start)}`}><Icon name="play" />{clock(t.start)}</button>
            <span><b>{t.title}</b> {t.summary}</span>
          </li>
        ))}</ul></section>
      )}
      <List title="Open questions" items={s.openQuestions} />
      <div className="summary-foot small muted">
        <span style={{ flex: 1 }}>Written by Claude ({s.model}) from the transcript; check anything important against it.</span>
        <button className="btn" onClick={copy}><Icon name={copied ? 'check' : 'copy'} />{copied ? 'Copied' : 'Copy summary'}</button>
        {keySet && <button className="btn" onClick={onSummarize} title="Write it again from the current transcript"><Icon name="refresh" />Summarize again</button>}
      </div>
    </details>
  )
}

function List({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null
  return <section><h3>{title}</h3><ul>{items.map((item, i) => <li key={i}>{item}</li>)}</ul></section>
}

/** Plain text for pasting into a chat or an email. */
export function summaryText(s: Summary): string {
  const list = (title: string, items: string[]) => (items.length ? [`${title}:`, ...items.map((i) => `- ${i}`), ''] : [])
  return [
    s.title, '', s.overview, '',
    ...list('Decisions', s.decisions),
    ...list('Action items', s.actionItems.map((a) => `${a.owner}: ${a.task}${a.due ? ` (${a.due})` : ''}`)),
    ...list('Topics', s.topics.map((t) => `${clock(t.start)} ${t.title}: ${t.summary}`)),
    ...list('Open questions', s.openQuestions),
  ].join('\n').trim()
}
