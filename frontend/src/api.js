async function req(path, options) {
  const resp = await fetch(path, options)
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`)
  return resp.json()
}

export const api = {
  status: () => req('/api/status'),
  config: () => req('/api/config'),
  saveConfig: (cfg) =>
    req('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    }),
  sync: () => req('/api/sync', { method: 'POST' }),
  stopSync: () => req('/api/sync/stop', { method: 'POST' }),
  reconnect: () => req('/api/reconnect', { method: 'POST' }),
  files: (offset = 0, limit = 100) => req(`/api/files?offset=${offset}&limit=${limit}`),
  pending: () => req('/api/pending'),
}
