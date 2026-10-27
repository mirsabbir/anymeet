import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { viteSingleFile } from 'vite-plugin-singlefile'

// One self-contained index.html (scripts, styles and fonts inlined), so the
// native apps can load it from disk in WKWebView / WebView2 without a server.
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  build: { assetsInlineLimit: 100_000_000 },
})
