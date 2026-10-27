import { createRoot } from 'react-dom/client'
import '@fontsource/inter/latin-400.css'
import '@fontsource/inter/latin-500.css'
import '@fontsource/inter/latin-600.css'
import '@fontsource/inter/latin-700.css'
import '@fontsource/hind-siliguri/bengali-400.css'
import '@fontsource/hind-siliguri/bengali-600.css'
import './styles.css'
import { Meetings } from './Meetings'
import { Settings } from './Settings'

// The host opens one window per view: #/settings, #/meetings or #/meetings/<id>.
// The host may point an open window at another meeting by changing the hash.
window.addEventListener('hashchange', () => location.reload())
const [, view, id] = decodeURIComponent(location.hash).split('/')
document.title = view === 'settings' ? 'Settings' : 'Meetings'

createRoot(document.getElementById('root')!).render(
  view === 'settings' ? <Settings initialTab={id} /> : <Meetings initialId={id} />,
)
