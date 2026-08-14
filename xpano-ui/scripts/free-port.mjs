import { createServer } from 'node:net'
import { execSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import process from 'node:process'

const startPort = parseInt(process.argv[2]) || 1420
const maxPort = startPort + 20

async function portFree(port) {
  return new Promise((resolve) => {
    const server = createServer()
    server.unref()
    server.on('error', () => resolve(false))
    server.listen(port, '127.0.0.1', () => {
      server.close(() => resolve(true))
    })
  })
}

async function findFreePort() {
  if (await portFree(startPort)) return startPort

  // Try to identify and kill only a stale xPano dev server on the preferred port
  console.log(`Port ${startPort} is busy, checking for stale xPano process...`)
  try {
    if (process.platform === 'win32') {
      const out = execSync(`netstat -ano | findstr :${startPort}`, { encoding: 'utf8' })
      const match = out.match(/LISTENING\s+(\d+)/)
      if (match) {
        // Only kill if it looks like a Node.js dev server (check via tasklist)
        try {
          const taskOut = execSync(`tasklist /FI "PID eq ${match[1]}" /FO CSV /NH`, { encoding: 'utf8' })
          if (taskOut.toLowerCase().includes('node')) {
            console.log(`  Killing stale Node.js process PID ${match[1]} on port ${startPort}`)
            execSync(`taskkill /F /PID ${match[1]}`, { stdio: 'ignore' })
            await new Promise((r) => setTimeout(r, 400))
            if (await portFree(startPort)) return startPort
          }
        } catch { /* couldn't identify, skip kill */ }
      }
    } else {
      const out = execSync(`lsof -ti:${startPort}`, { encoding: 'utf8' }).trim()
      if (out) {
        const pids = out.split('\n').map(s => s.trim()).filter(Boolean)
        for (const pid of pids) {
          try {
            const cmd = execSync(`ps -p ${pid} -o comm=`, { encoding: 'utf8' }).trim()
            if (cmd.includes('node') || cmd.includes('vite')) {
              console.log(`  Killing stale Node.js process PID ${pid} on port ${startPort}`)
              execSync(`kill ${pid}`, { stdio: 'ignore' })
            }
          } catch { /* skip */ }
        }
        await new Promise((r) => setTimeout(r, 400))
        if (await portFree(startPort)) return startPort
      }
    }
  } catch { /* fall through to next port */ }

  // Fallback: find next available port
  for (let port = startPort + 1; port <= maxPort; port++) {
    if (await portFree(port)) {
      console.log(`Port ${startPort} unavailable, using port ${port}`)
      return port
    }
  }
  console.error(`No free port in range ${startPort}-${maxPort}`)
  process.exit(1)
}

const port = await findFreePort()
// Write port file so vite.config.ts and Tauri can discover it
writeFileSync('.vite-port', String(port))
// Also set env var for Tauri's ${} interpolation in config
process.env.VITE_DEV_SERVER_PORT = String(port)
process.exit(0)
