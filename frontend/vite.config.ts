import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// BACKEND_PORT: where the dev server proxies /api (default 8000) — set it
// when another app already holds :8000, e.g. BACKEND_PORT=8001 npm run dev
const backendPort = process.env.BACKEND_PORT || '8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': `http://127.0.0.1:${backendPort}`,
    },
  },
})
