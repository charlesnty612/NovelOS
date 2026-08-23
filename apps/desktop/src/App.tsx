import { useEffect, useState } from 'react'
import './App.css'

type Health = {
  status: string
  version: string
  tables: number
}

function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((data: Health) => setHealth(data))
      .catch((e: Error) => setError(e.message))
  }, [])

  return (
    <main className="container">
      <h1>NovelOS</h1>
      <p className="subtitle">Sprint 0 — local-first novel writing operating system</p>
      <section className="card">
        <h2>Backend health</h2>
        {error && <p className="err">unreachable: {error}</p>}
        {health && (
          <ul>
            <li>status: <code>{health.status}</code></li>
            <li>version: <code>{health.version}</code></li>
            <li>tables: <code>{health.tables}</code></li>
          </ul>
        )}
        {!health && !error && <p>loading…</p>}
      </section>
    </main>
  )
}

export default App