import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    strictPort: true,
    proxy: {
      // Forwards every `/api/...` request (REST + the SSE chat stream) made
      // from the dev server's own origin (http://localhost:3000) through to
      // the FastAPI backend. Paired with `VITE_API_BASE_URL` being left
      // empty in dev (see `.env.example`), this is what lets
      // `curl -f http://localhost:3000/api/health` resolve.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
