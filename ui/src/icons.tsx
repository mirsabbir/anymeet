// Stroke icons, sized by font-size and colored by currentColor.
const paths = {
  screen: 'M4 5h16a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM8 20h8M12 16v4',
  mic: 'M12 3a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3zM5 11a7 7 0 0 0 14 0M12 18v3',
  speaker: 'M11 5 6 9H3v6h3l5 4V5zM15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13',
  folder: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  copy: 'M9 9h10v10H9zM5 15V5h10',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7',
  search: 'M11 4a7 7 0 1 1 0 14 7 7 0 0 1 0-14zM21 21l-4.3-4.3',
  warning: 'M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
  play: 'M8 5v14l11-7z',
  check: 'M5 12l5 5L20 7',
  close: 'M6 6l12 12M18 6 6 18',
  record: 'M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18z',
}

export type IconName = keyof typeof paths

export function Icon({ name, label }: { name: IconName; label?: string }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9"
      strokeLinecap="round" strokeLinejoin="round"
      {...(label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': true })}>
      <path d={paths[name]} />
    </svg>
  )
}
