import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Non-VITE_-prefixed env var, read from the Node process (docker-compose sets
// it for the frontend container) -- never bundled into the browser build.
// Proxies /api/* on the frontend's dev-server port so the project
// health-check (`curl -f http://localhost:3000/api/health`) resolves to the
// FastAPI backend without the frontend needing any real app code yet
// (real shell + routing land at G-6).
const apiProxyTarget = process.env.API_PROXY_TARGET || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 3000,
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
})
