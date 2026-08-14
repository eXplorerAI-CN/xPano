import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

function readPortFile(): number {
  try {
    const raw = readFileSync('.vite-port', 'utf-8').trim()
    const port = parseInt(raw, 10)
    if (port > 0 && port < 65536) return port
  } catch { /* file not found, use default */ }
  // Fallback: env var from free-port.mjs (set via cross-env or similar)
  const envPort = parseInt(process.env.VITE_DEV_SERVER_PORT || '', 10)
  if (envPort > 0) return envPort
  return 1420
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],

  // Tauri API uses dynamic imports for its IPC bridge — Vite can't pre-bundle it
  optimizeDeps: {
    exclude: [
      '@tauri-apps/api',
      '@tauri-apps/plugin-dialog',
      '@tauri-apps/plugin-log',
    ],
  },

  server: {
    host: '127.0.0.1',
    port: readPortFile(),
    strictPort: true,
  },

  build: {
    // Three.js is ~600KB minified, raise the default 500KB threshold
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        // Split heavy vendor deps into stable chunks for better browser caching
        manualChunks: (id) => {
          if (id.includes('node_modules/three')) return 'vendor-three'
          if (id.includes('node_modules/@react-three')) return 'vendor-r3f'
          if (id.includes('node_modules/react-dom') || id.includes('node_modules/react/')) return 'vendor-react'
          if (id.includes('node_modules/gsap')) return 'vendor-gsap'
        },
      },
    },
  },
})
