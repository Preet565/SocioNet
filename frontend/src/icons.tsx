import type { ReactNode, SVGProps } from 'react'

export type IconName =
  | 'activity'
  | 'arrow'
  | 'beaker'
  | 'book'
  | 'brain'
  | 'chevron'
  | 'close'
  | 'command'
  | 'eye'
  | 'flask'
  | 'globe'
  | 'layers'
  | 'link'
  | 'menu'
  | 'network'
  | 'pause'
  | 'play'
  | 'refresh'
  | 'reset'
  | 'search'
  | 'send'
  | 'settings'
  | 'spark'
  | 'target'
  | 'users'
  | 'x'

const paths: Record<IconName, ReactNode> = {
  activity: <><path d="M3 12h4l2.2-7 4.1 14L16 9l2 3h3" /></>,
  arrow: <><path d="M5 12h13" /><path d="m13 6 6 6-6 6" /></>,
  beaker: <><path d="M9 3h6" /><path d="M10 3v6l-5.8 9.1A1.2 1.2 0 0 0 5.2 20h13.6a1.2 1.2 0 0 0 1-1.9L14 9V3" /><path d="M8 15h8" /></>,
  book: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21z" /><path d="M4 5.5v15" /><path d="M8 7h8" /></>,
  brain: <><path d="M9.5 4.5a3 3 0 0 0-5 2.2A3.2 3.2 0 0 0 5 13a3 3 0 0 0 4.5 3v3" /><path d="M14.5 4.5a3 3 0 0 1 5 2.2A3.2 3.2 0 0 1 19 13a3 3 0 0 1-4.5 3v3" /><path d="M9.5 8a2 2 0 0 1 5 0v7" /><path d="M6 9h3.5M14.5 11H18" /></>,
  chevron: <><path d="m7 10 5 5 5-5" /></>,
  close: <><path d="m6 6 12 12M18 6 6 18" /></>,
  command: <><path d="M18 8a3 3 0 1 0-3-3v14a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V5a3 3 0 1 0-3 3z" /></>,
  eye: <><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" /><circle cx="12" cy="12" r="2.5" /></>,
  flask: <><path d="M9 3h6M10 3v5l-6 9.5A1 1 0 0 0 4.9 19h14.2a1 1 0 0 0 .9-1.5L14 8V3" /><path d="M7.5 14h9" /></>,
  globe: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a13.7 13.7 0 0 1 0 18M12 3a13.7 13.7 0 0 0 0 18" /></>,
  layers: <><path d="m12 3 9 5-9 5-9-5z" /><path d="m3 12 9 5 9-5M3 16l9 5 9-5" /></>,
  link: <><path d="M10 13a5 5 0 0 0 7.1.1l1.4-1.4a5 5 0 0 0-7.1-7.1L10.6 5.4" /><path d="M14 11a5 5 0 0 0-7.1-.1l-1.4 1.4a5 5 0 0 0 7.1 7.1l.8-.8" /></>,
  menu: <><path d="M4 6h16M4 12h16M4 18h16" /></>,
  network: <><circle cx="5" cy="7" r="2" /><circle cx="19" cy="5" r="2" /><circle cx="16" cy="18" r="2" /><circle cx="7" cy="17" r="2" /><path d="m7 8 8.5 8M7 16 17 6.5M6.5 7h10.5M8.5 17H14" /></>,
  pause: <><path d="M8 5v14M16 5v14" /></>,
  play: <><path d="m8 5 11 7-11 7z" /></>,
  refresh: <><path d="M20 11a8 8 0 0 0-14.7-4L3 10" /><path d="M3 5v5h5M4 13a8 8 0 0 0 14.7 4L21 14" /><path d="M21 19v-5h-5" /></>,
  reset: <><path d="M3 12a9 9 0 1 0 3-6.7" /><path d="M3 4v6h6" /></>,
  search: <><circle cx="10.8" cy="10.8" r="6.8" /><path d="m16 16 5 5" /></>,
  send: <><path d="m21 3-7.5 18-3.2-7.3L3 10.5z" /><path d="M10.3 13.7 21 3" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-1.8 1.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5v.2h-2.6v-.2a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1-1.8-1.8.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H4.3v-2.6h.2a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1 1.8-1.8.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.5V4.3H13v.2a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1 1.8 1.8-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.5 1h.2V13h-.2a1.7 1.7 0 0 0-1.5 1Z" /></>,
  spark: <><path d="m12 2 1.4 6.6L20 10l-6.6 1.4L12 18l-1.4-6.6L4 10l6.6-1.4z" /><path d="m19 16 .6 2.4L22 19l-2.4.6L19 22l-.6-2.4L16 19l2.4-.6z" /></>,
  target: <><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="3" /><path d="M12 2v2M12 20v2M2 12h2M20 12h2" /></>,
  users: <><path d="M16 20v-1.5a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4V20" /><circle cx="9.5" cy="7" r="3.5" /><path d="M16 4.5a3.5 3.5 0 0 1 0 6.8M21 20v-1.5a4 4 0 0 0-3-3.8" /></>,
  x: <><path d="M5 5 19 19M19 5 5 19" /></>,
}

export function Icon({ name, size = 18, strokeWidth = 1.7, ...props }: { name: IconName; size?: number; strokeWidth?: number } & Omit<SVGProps<SVGSVGElement>, 'name'>) {
  return <svg aria-hidden="true" focusable="false" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" {...props}>{paths[name]}</svg>
}
