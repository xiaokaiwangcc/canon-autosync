import { useEffect, useState, useCallback } from 'react'
import { api } from './api'

function fmtSize(bytes) {
  if (bytes == null) return '-'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function fmtPct(bytes, total) {
  if (!bytes || !total) return 0
  return Math.min(100, Math.round(((total - bytes) / total) * 100))
}

function fmtTime(iso) {
  if (!iso) return '—'
  return iso.replace('T', ' ')
}

const BATTERY_META = {
  high: { label: '充足', color: '#16a34a' },
  middle: { label: '中等', color: '#ca8a04' },
  low: { label: '偏低', color: '#ea580c' },
  empty: { label: '耗尽', color: '#dc2626' },
}

const TEMP_META = {
  normal: { label: '正常', color: '#16a34a' },
  high: { label: '偏高', color: '#ea580c' },
  error: { label: '过热', color: '#dc2626' },
}

function StatusBadge({ status }) {
  const { camera_online, sync_mode, event_listening, syncing } = status
  let text = '相机离线'
  let cls = 'offline'
  if (camera_online) {
    if (syncing) {
      text = '同步中…'
      cls = 'syncing'
    } else if (sync_mode === 'event') {
      text = event_listening ? '已连接 · 监听拍摄中' : '已连接'
      cls = 'online'
    } else {
      text = '已连接 · 定时扫描'
      cls = 'online'
    }
  }
  return (
    <span className={`badge ${cls}`}>
      <span className="dot" />
      {text}
    </span>
  )
}

function Icon({ paths, size = 16 }) {
  return (
    <svg
      width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths}
    </svg>
  )
}

const NAV_ITEMS = [
  {
    id: 'camera',
    label: '相机',
    icon: (
      <>
        <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
        <circle cx="12" cy="13" r="4" />
      </>
    ),
  },
  {
    id: 'files',
    label: '文件',
    icon: (
      <>
        <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
        <polyline points="13 2 13 9 20 9" />
      </>
    ),
  },
  {
    id: 'settings',
    label: '设置',
    icon: (
      <>
        <line x1="4" y1="21" x2="4" y2="14" />
        <line x1="4" y1="10" x2="4" y2="3" />
        <line x1="12" y1="21" x2="12" y2="12" />
        <line x1="12" y1="8" x2="12" y2="3" />
        <line x1="20" y1="21" x2="20" y2="16" />
        <line x1="20" y1="12" x2="20" y2="3" />
        <line x1="1" y1="14" x2="7" y2="14" />
        <line x1="9" y1="8" x2="15" y2="8" />
        <line x1="17" y1="16" x2="23" y2="16" />
      </>
    ),
  },
]

const PAGE_TITLES = {
  camera: '相机',
  files: '文件',
  settings: '设置',
}

function Sidebar({ page, onNavigate, status }) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-canon">Canon</span>
        <span className="brand-sub">Autosync</span>
      </div>
      <nav className="nav">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            className={`nav-item ${page === item.id ? 'active' : ''}`}
            onClick={() => onNavigate(item.id)}
          >
            <Icon paths={item.icon} />
            {item.label}
          </button>
        ))}
      </nav>
      <div className="sidebar-footer">
        <div className="foot-item">
          <span className={`foot-dot ${status.auto_sync ? 'on' : ''}`} />
          自动同步 {status.auto_sync ? '开' : '关'}
        </div>
        <div className="foot-item muted">
          {status.sync_mode === 'event' ? '事件驱动模式' : '定时扫描模式'}
        </div>
      </div>
    </aside>
  )
}

function CameraPanel({ status }) {
  const cam = status.camera || {}
  const device = cam.device || {}
  const batt = cam.battery || {}
  const temp = cam.temperature || {}
  const storages = (cam.storage || {}).storagelist || []
  const battMeta = BATTERY_META[batt.level] || { label: '—', color: '#9ca3af' }
  const tempMeta = TEMP_META[temp.status] || { label: '—', color: '#9ca3af' }

  return (
    <div className="card camera-card">
      <div className="row" style={{ marginBottom: 10 }}>
        <h2 style={{ margin: 0 }}>相机</h2>
        {device.productname && (
          <span className="device-name">
            {device.productname}
            {device.firmwareversion && <span className="muted"> · FW {device.firmwareversion}</span>}
          </span>
        )}
      </div>
      <div className="cam-grid">
        <div className="cam-cell">
          <div className="label">电量</div>
          <div className="cam-line">
            <span className="cam-dot" style={{ background: battMeta.color, boxShadow: `0 0 6px ${battMeta.color}` }} />
            <span style={{ color: battMeta.color }}>{battMeta.label}</span>
            <span className="muted">{batt.name || ''}</span>
          </div>
        </div>
        <div className="cam-cell">
          <div className="label">温度</div>
          <div className="cam-line">
            <span className="cam-dot" style={{ background: tempMeta.color, boxShadow: `0 0 6px ${tempMeta.color}` }} />
            <span style={{ color: tempMeta.color }}>{tempMeta.label}</span>
          </div>
        </div>
        {device.serialnumber && (
          <div className="cam-cell">
            <div className="label">序列号</div>
            <div className="cam-line">{device.serialnumber}</div>
          </div>
        )}
        {device.macaddress && (
          <div className="cam-cell">
            <div className="label">MAC</div>
            <div className="cam-line">{device.macaddress}</div>
          </div>
        )}
      </div>
      {storages.map((s) => (
        <div className="storage" key={s.name}>
          <div className="row" style={{ marginBottom: 6 }}>
            <span className="label" style={{ margin: 0 }}>
              存储卡 {s.name}
              {s.accesscapability === 'readonly' && (
                <span className="tag readonly">只读</span>
              )}
            </span>
            <span className="muted">
              {fmtSize(s.spacesize)} 可用 / {fmtSize(s.maxsize)} · {s.contentsnumber} 个文件
            </span>
          </div>
          <div className="capacity-bar">
            <div style={{ width: `${fmtPct(s.spacesize, s.maxsize)}%` }} />
          </div>
        </div>
      ))}
    </div>
  )
}

function Stats({ status }) {
  const items = [
    { label: '已备份', value: status.synced_count },
    { label: '待备份', value: status.pending_count },
    { label: '相机文件总数', value: status.camera_file_count },
    { label: '上次同步', value: fmtTime(status.last_sync) },
  ]
  return (
    <div className="grid">
      {items.map((it) => (
        <div className="stat" key={it.label}>
          <div className="label">{it.label}</div>
          <div className="value" style={it.label === '上次同步' ? { fontSize: 12, paddingTop: 4 } : undefined}>
            {it.value}
          </div>
        </div>
      ))}
    </div>
  )
}

function SyncProgress({ status, onSync, onStop }) {
  const { progress, syncing } = status
  const pct = progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0
  const modeText = status.sync_mode === 'event'
    ? `事件驱动 · 监听中${status.event_listening ? '' : '（重连中）'} · 超过 ${status.poll_interval}s 无事件自动兜底扫描`
    : `定时扫描 · 每 ${status.poll_interval}s 一次`
  // 进度 60s 无变化且仍在同步中 → 判定卡住，提示相机可能已断线并提供停止入口
  const [stalled, setStalled] = useState(false)
  useEffect(() => {
    setStalled(false)
    if (!syncing) return
    const t = setTimeout(() => setStalled(true), 60000)
    return () => clearTimeout(t)
  }, [syncing, progress.done, progress.total, progress.current])
  return (
    <div className="card">
      <div className="row" style={{ marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>同步</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          {syncing && stalled && (
            <button className="ghost" onClick={onStop}>停止同步</button>
          )}
          <button onClick={onSync} disabled={syncing || !status.camera_online}>
            {syncing ? '同步中…' : '立即同步'}
          </button>
        </div>
      </div>
      {syncing && stalled && (
        <div className="warning" style={{ margin: '8px 0' }}>
          同步已 60 秒无进展，相机可能已断线。可点击「停止同步」后重试连接。
        </div>
      )}
      {(syncing || progress.total > 0) && (
        <div className="progress-wrap">
          <div className="progress-bar">
            <div style={{ width: `${pct}%` }} />
          </div>
          <div className="progress-text">
            {progress.done} / {progress.total}
            {progress.current ? ` — ${progress.current}` : ''}
          </div>
        </div>
      )}
      <div className="muted">
        备份目录：{status.nas_path} · 自动同步：{status.auto_sync ? `开（${modeText}）` : '关'}
        {status.delete_after_sync && ' · 同步后清理卡上文件：开'}
      </div>
    </div>
  )
}

function ConfigForm() {
  const [cfg, setCfg] = useState(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    api.config().then(setCfg).catch(() => {})
  }, [])

  if (!cfg) return null
  const set = (k, v) => setCfg({ ...cfg, [k]: v })

  const submit = async (e) => {
    e.preventDefault()
    if (cfg.delete_after_sync && !window.confirm('开启后，每个文件备份完成会立即从存储卡删除，且无法恢复。确认开启？')) {
      return
    }
    await api.saveConfig({ ...cfg, camera_port: Number(cfg.camera_port), poll_interval: Number(cfg.poll_interval) })
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  return (
    <div className="card">
      <h2>设置</h2>
      <form onSubmit={submit}>
        <div className="form-grid">
          <div className="field">
            <label>相机 IP</label>
            <input value={cfg.camera_ip} onChange={(e) => set('camera_ip', e.target.value)} />
          </div>
          <div className="field">
            <label>端口</label>
            <input type="number" value={cfg.camera_port} onChange={(e) => set('camera_port', e.target.value)} />
          </div>
          <div className="field full">
            <label>NAS 备份目录</label>
            <input value={cfg.nas_path} onChange={(e) => set('nas_path', e.target.value)} placeholder="/Volumes/photos/canon-backup" />
          </div>
          <div className="field">
            <label>兜底扫描间隔（秒）</label>
            <input type="number" min="10" value={cfg.poll_interval} onChange={(e) => set('poll_interval', e.target.value)} />
          </div>
          <div className="field check" style={{ alignSelf: 'end' }}>
            <input id="auto" type="checkbox" checked={cfg.auto_sync} onChange={(e) => set('auto_sync', e.target.checked)} />
            <label htmlFor="auto" style={{ margin: 0, color: '#1f2430', fontSize: 14 }}>自动同步</label>
          </div>
          <div className="field check">
            <input id="event" type="checkbox" checked={cfg.sync_on_event} onChange={(e) => set('sync_on_event', e.target.checked)} />
            <label htmlFor="event" style={{ margin: 0, color: '#1f2430', fontSize: 14 }}>
              事件驱动（拍照后秒级同步）
            </label>
          </div>
          <div className="field check">
            <input id="del" type="checkbox" checked={cfg.delete_after_sync} onChange={(e) => set('delete_after_sync', e.target.checked)} />
            <label htmlFor="del" style={{ margin: 0, color: '#1f2430', fontSize: 14 }}>
              同步后清理卡上文件
            </label>
          </div>
          <div className="field check" style={{ alignSelf: 'end' }} />
        </div>
        <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 12 }}>
          <button type="submit">保存</button>
          {saved && <span className="muted">已保存</span>}
        </div>
      </form>
    </div>
  )
}

const IMAGE_EXTS = ['jpg', 'jpeg', 'heif', 'hif', 'png', 'webp', 'gif', 'bmp']
const VIDEO_EXTS = ['mp4', 'mov']

function PreviewModal({ items, index, onNavigate, onClose }) {
  const { src, name } = items[index]
  const ext = (name.split('.').pop() || '').toLowerCase()
  const isImage = IMAGE_EXTS.includes(ext)
  const isVideo = VIDEO_EXTS.includes(ext)
  const [failedSrc, setFailedSrc] = useState(null)
  const failed = failedSrc === src
  const hasPrev = index > 0
  const hasNext = index < items.length - 1

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') return onClose()
      if (e.key === 'ArrowLeft' && hasPrev) return onNavigate(-1)
      if (e.key === 'ArrowRight' && hasNext) return onNavigate(1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, onNavigate, hasPrev, hasNext])

  const nav = (dir) => (e) => {
    e.stopPropagation()
    onNavigate(dir)
  }

  return (
    <div className="modal" onClick={onClose}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-title">{name.split('/').pop()} · {index + 1}/{items.length}</span>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          {isImage && !failed && (
            <img key={src} src={src} alt={name} onError={() => setFailedSrc(src)} />
          )}
          {isVideo && <video key={src} src={src} controls autoPlay />}
          {(failed || (!isImage && !isVideo)) && (
            <div className="muted">该格式暂不支持预览（{ext || '未知'}）</div>
          )}
        </div>
      </div>
      {hasPrev && <button className="modal-nav prev" onClick={nav(-1)}>‹</button>}
      {hasNext && <button className="modal-nav next" onClick={nav(1)}>›</button>}
    </div>
  )
}

const RAW_EXTS = ['cr3', 'cr2', 'raw', 'nef', 'arw', 'dng']

const IconList = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="8" y1="6" x2="21" y2="6" /><line x1="8" y1="12" x2="21" y2="12" /><line x1="8" y1="18" x2="21" y2="18" /><line x1="3" y1="6" x2="3.01" y2="6" /><line x1="3" y1="12" x2="3.01" y2="12" /><line x1="3" y1="18" x2="3.01" y2="18" /></svg>
)
const IconGrid = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></svg>
)

function Thumb({ src, name }) {
  const [attempt, setAttempt] = useState(0)
  const [failed, setFailed] = useState(false)
  // 503 等瞬时错误：失败后延时自动重试（换 key 强制重新加载），最多 2 次；仍失败可点击占位手动重试
  const handleError = () => {
    if (attempt < 2) {
      setTimeout(() => setAttempt((a) => a + 1), 1500)
    } else {
      setFailed(true)
    }
  }
  if (failed) {
    const ext = (name.split('.').pop() || '').toLowerCase()
    const cls = VIDEO_EXTS.includes(ext) ? 'thumb-video'
      : RAW_EXTS.includes(ext) ? 'thumb-raw'
      : IMAGE_EXTS.includes(ext) ? 'thumb-image'
      : 'thumb-file'
    return (
      <div
        className={`thumb thumb-empty ${cls}`}
        title={`${name}\n加载失败，点击重试`}
        onClick={() => { setFailed(false); setAttempt(0) }}
      />
    )
  }
  return <img key={attempt} className="thumb" src={src} alt={name} loading="lazy" onError={handleError} />
}

function FileList() {
  const [tab, setTab] = useState('synced')
  const [view, setView] = useState('list')
  const [files, setFiles] = useState([])
  const [pending, setPending] = useState([])
  const [preview, setPreview] = useState(null)

  const currentList = tab === 'synced' ? files : pending
  const openPreview = (idx) => {
    const list = currentList.map((x) => {
      const camPath = typeof x === 'string' ? x : x.path
      const src = typeof x === 'string'
        ? `/api/thumb?path=${encodeURIComponent(x)}`
        : `/api/preview?path=${encodeURIComponent(x.dest)}`
      return { src, name: camPath.split('/').pop() }
    })
    setPreview({ list, index: idx })
  }

  useEffect(() => {
    const load = () => {
      api.files(0, 200).then((d) => setFiles(d.items)).catch(() => {})
      api.pending().then(setPending).catch(() => {})
    }
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="card">
      <div className="tabs-row">
        <div className="tabs">
          <button className={tab === 'synced' ? 'active' : 'ghost'} onClick={() => setTab('synced')}>
            已备份 ({files.length})
          </button>
          <button className={tab === 'pending' ? 'active' : 'ghost'} onClick={() => setTab('pending')}>
            待备份 ({pending.length})
          </button>
        </div>
        <div className="view-toggle">
          <button className={view === 'list' ? 'active' : 'ghost'} onClick={() => setView('list')} title="列表模式"><IconList /></button>
          <button className={view === 'grid' ? 'active' : 'ghost'} onClick={() => setView('grid')} title="图标模式"><IconGrid /></button>
        </div>
      </div>
      {/* 两个 tab 的列表同时渲染、用 display 切换显隐：切换 tab 不卸载图片，避免重复请求相机缩略图 */}
      {view === 'grid' ? (
        <>
          <div className="grid" style={tab === 'synced' ? undefined : { display: 'none' }}>
            {files.map((x, i) => {
              const name = x.path.split('/').pop()
              return (
                <div className="grid-item" key={x.path} onClick={() => openPreview(i)} title={x.path}>
                  <Thumb src={`/api/preview?path=${encodeURIComponent(x.dest)}&size=320`} name={name} />
                  <div className="grid-name">{name}</div>
                  <div className="grid-meta">{fmtSize(x.size)}</div>
                </div>
              )
            })}
            {files.length === 0 && <div className="grid-empty muted">暂无记录</div>}
          </div>
          <div className="grid" style={tab === 'pending' ? undefined : { display: 'none' }}>
            {pending.map((x, i) => {
              const name = x.split('/').pop()
              return (
                <div className="grid-item" key={x} onClick={() => openPreview(i)} title={x}>
                  <Thumb src={`/api/thumb?path=${encodeURIComponent(x)}`} name={name} />
                  <div className="grid-name">{name}</div>
                  <div className="grid-meta">{x.split('/').slice(-2).join('/')}</div>
                </div>
              )
            })}
            {pending.length === 0 && <div className="grid-empty muted">没有待备份文件</div>}
          </div>
        </>
      ) : (
        <>
          <table style={tab === 'synced' ? undefined : { display: 'none' }}>
            <thead>
              <tr><th>封面</th><th>文件</th><th>大小</th><th>备份位置</th><th>时间</th></tr>
            </thead>
            <tbody>
              {files.map((f, i) => (
                <tr key={f.path} onClick={() => openPreview(i)} title="点击预览">
                  <td><Thumb src={`/api/preview?path=${encodeURIComponent(f.dest)}&size=160`} name={f.path.split('/').pop()} /></td>
                  <td>{f.path.split('/').pop()}</td>
                  <td>{fmtSize(f.size)}</td>
                  <td>{f.dest}</td>
                  <td>{fmtTime(f.synced_at)}</td>
                </tr>
              ))}
              {files.length === 0 && <tr><td colSpan="5" className="muted">暂无记录</td></tr>}
            </tbody>
          </table>
          <table style={tab === 'pending' ? undefined : { display: 'none' }}>
            <thead><tr><th>封面</th><th>相机上的文件</th></tr></thead>
            <tbody>
              {pending.map((p, i) => <tr key={p} onClick={() => openPreview(i)} title="点击预览"><td><Thumb src={`/api/thumb?path=${encodeURIComponent(p)}`} name={p.split('/').pop()} /></td><td>{p}</td></tr>)}
              {pending.length === 0 && <tr><td colSpan="2" className="muted">没有待备份文件</td></tr>}
            </tbody>
          </table>
        </>
      )}
      {preview && (
        <PreviewModal
          items={preview.list}
          index={preview.index}
          onNavigate={(dir) => setPreview((p) => ({ ...p, index: Math.min(Math.max(p.index + dir, 0), p.list.length - 1) }))}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  )
}

export default function App() {
  const [status, setStatus] = useState(null)
  const [page, setPage] = useState('camera')
  const [retrying, setRetrying] = useState(false)

  const refresh = useCallback(() => {
    api.status().then(setStatus).catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 2000)
    return () => clearInterval(t)
  }, [refresh])

  const syncNow = async () => {
    await api.sync().catch(() => {})
    refresh()
  }

  const stopSync = async () => {
    await api.stopSync().catch(() => {})
    refresh()
  }

  const retryConnect = async () => {
    setRetrying(true)
    await api.reconnect().catch(() => {})
    refresh()
    setRetrying(false)
  }

  if (!status) return <div className="app"><h1>Canon → NAS 备份</h1><p className="muted">连接后端中…</p></div>

  return (
    <div className="layout">
      <Sidebar page={page} onNavigate={setPage} status={status} />
      <main className="content">
        <header>
          <h1>{PAGE_TITLES[page]}</h1>
          <div className="header-actions">
            <StatusBadge status={status} />
            <button
              className={status.camera_online ? 'ghost' : ''}
              onClick={retryConnect}
              disabled={retrying || status.syncing}
            >
              {retrying ? '重试中…' : '重试连接'}
            </button>
          </div>
        </header>
        {status.last_error && <div className="error">{status.last_error}</div>}
        {status.camera_warning && !status.last_error && (
          <div className="warning">{status.camera_warning}</div>
        )}
        {page === 'camera' && (
          <>
            <CameraPanel status={status} />
            <Stats status={status} />
            <SyncProgress status={status} onSync={syncNow} onStop={stopSync} />
          </>
        )}
        {page === 'files' && <FileList />}
        {page === 'settings' && <ConfigForm />}
      </main>
    </div>
  )
}
