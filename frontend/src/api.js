async function req(path, options) {
  const resp = await fetch(path, options)
  if (!resp.ok) {
    // 优先透传后端 detail（如 NAS 掉线的具体提示），否则退化为状态码
    let message = `${resp.status} ${resp.statusText}`
    try {
      const body = await resp.json()
      if (body.detail) message = body.detail
    } catch { /* 非 JSON 响应，保留状态码 */ }
    throw new Error(message)
  }
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
  deleteFile: (path) => req(`/api/files?path=${encodeURIComponent(path)}`, { method: 'DELETE' }),
  cleanupMissing: () => req('/api/files/cleanup-missing', { method: 'POST' }),
  clearIgnored: () => req('/api/files/clear-ignored', { method: 'POST' }),
  restoreFile: (path) => req(`/api/files/restore?path=${encodeURIComponent(path)}`, { method: 'POST' }),
  pending: () => req('/api/pending'),
}
