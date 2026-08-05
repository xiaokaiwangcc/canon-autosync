import { useEffect, useState, useCallback, useRef } from 'react'
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
  ac: { label: '外接电源', color: '#16a34a' },
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

const PAGE_SUBTITLES = {
  camera: '设备状态与同步进度',
  files: '存储卡内容浏览与备份管理',
  settings: '连接与备份参数',
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
        <div className="conn-card">
          <div className="foot-item conn-status">
            <span className={`foot-dot ${status.camera_online ? 'on' : ''}`} />
            <b>{status.camera_online ? '已连接' : '未连接'}</b>
            <span className="muted" style={{ marginLeft: 'auto' }}>
              {status.poll_interval}s 轮询
            </span>
          </div>
          {status.camera?.device?.productname && (
            <div className="conn-name">{status.camera.device.productname}</div>
          )}
          <div className="conn-addr">
            {status.camera_ip} · CCAPI
          </div>
        </div>
        <div className="foot-item muted" style={{ marginTop: 8 }}>
          自动同步{status.auto_sync ? '已开启' : '已关闭'} · {status.sync_mode === 'event' ? '秒级同步' : '定时扫描模式'}
        </div>
        {status.version && <div className="app-version">v{status.version}</div>}
      </div>
    </aside>
  )
}

const ICON_BATTERY = (
  <>
    <rect x="1" y="6" width="18" height="12" rx="2" ry="2" />
    <line x1="23" y1="13" x2="23" y2="11" />
  </>
)
const ICON_THERMOMETER = (
  <path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z" />
)
const ICON_FINGERPRINT = (
  <>
    <path d="M12 11a3 3 0 0 0-3 3c0 2 1 4 1 6" />
    <path d="M15 11.5c.2.5.3 1 .3 1.5 0 2.5-1 5-2 8" />
    <path d="M12 2a9 9 0 0 0-9 9c0 1.5.2 3 .5 4.5" />
    <path d="M12 2a9 9 0 0 1 9 9c0 1-.1 2-.3 3" />
    <path d="M12 7a4 4 0 0 1 4 4c0 .6-.05 1.2-.16 1.8" />
    <path d="M8.5 9A4 4 0 0 0 8 11c0 3 1 6 2 9" />
  </>
)
const ICON_WIFI = (
  <>
    <path d="M5 12.55a11 11 0 0 1 14.08 0" />
    <path d="M1.42 9a16 16 0 0 1 21.16 0" />
    <path d="M8.53 16.11a6 6 0 0 1 6.95 0" />
    <line x1="12" y1="20" x2="12.01" y2="20" />
  </>
)
const ICON_SD = (
  <>
    <rect x="2" y="7" width="20" height="14" rx="2" ry="2" />
    <path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" />
  </>
)

function CameraPanel({ status }) {
  const cam = status.camera || {}
  const device = cam.device || {}
  const storages = (cam.storage || {}).storagelist || []

  return (
    <div className="card camera-hero">
      <div className="hero-main">
        <div className="hero-icon">
          <Icon paths={NAV_ITEMS[0].icon} size={26} />
        </div>
        <div className="hero-info">
          <div className="hero-name-row">
            <span className="hero-name">{device.productname || '未识别相机'}</span>
            {device.firmwareversion && <span className="chip">FW {device.firmwareversion}</span>}
          </div>
          <div className="muted">
            {status.camera_online
              ? `CCAPI 已连接 · ${status.sync_mode === 'event' ? '事件驱动' : '无线传输'}`
              : '相机未连接'}
          </div>
        </div>
        {status.camera_online && (
          <span className="badge online hero-badge"><span className="dot" />在线</span>
        )}
      </div>
      {storages.map((s) => (
        <div className="hero-storage" key={s.name}>
          <div className="row" style={{ marginBottom: 8 }}>
            <span className="label" style={{ margin: 0, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <Icon paths={ICON_SD} size={14} />
              存储卡 {s.name}
              {s.accesscapability === 'readonly' && <span className="tag readonly">只读</span>}
            </span>
            <span className="muted">
              {fmtSize(s.spacesize)} 可用 / {fmtSize(s.maxsize)} · {s.contentsnumber} 个文件
            </span>
          </div>
          <div className="capacity-bar red">
            <div style={{ width: `${fmtPct(s.spacesize, s.maxsize)}%` }} />
          </div>
        </div>
      ))}
    </div>
  )
}

function InfoCards({ status }) {
  const cam = status.camera || {}
  const device = cam.device || {}
  const batt = cam.battery || {}
  const temp = cam.temperature || {}
  const battMeta = BATTERY_META[batt.level] || { label: '—', color: '#9ca3af' }
  const tempMeta = TEMP_META[temp.status] || { label: '—', color: '#9ca3af' }
  const battPct = typeof batt.percentage === 'number' ? batt.percentage : null

  const cards = [
    {
      icon: ICON_BATTERY,
      label: '电量',
      body: (
        <>
          <div className="info-value">
            {battPct != null ? `${battPct}%` : battMeta.label}
            {batt.name && <span className="info-sub">{batt.name}</span>}
          </div>
          <div className="capacity-bar" style={{ background: '#eef0f3' }}>
            <div style={{ width: battPct != null ? `${battPct}%` : '0%', background: battMeta.color }} />
          </div>
        </>
      ),
    },
    {
      icon: ICON_THERMOMETER,
      label: '机身温度',
      body: (
        <>
          <div className="info-value" style={{ color: tempMeta.color }}>
            <span className="cam-dot" style={{ background: tempMeta.color, boxShadow: `0 0 6px ${tempMeta.color}` }} />
            {tempMeta.label}
          </div>
          <div className="muted">持续传输温度监控</div>
        </>
      ),
    },
    {
      icon: ICON_FINGERPRINT,
      label: '序列号',
      body: (
        <>
          <div className="info-value mono">{device.serialnumber || '—'}</div>
          <div className="muted">机身唯一标识</div>
        </>
      ),
    },
    {
      icon: ICON_WIFI,
      label: 'MAC 地址',
      body: (
        <>
          <div className="info-value mono">{device.macaddress || '—'}</div>
          <div className="muted">{status.camera_ip || ''}</div>
        </>
      ),
    },
  ]

  return (
    <div className="info-grid">
      {cards.map((c) => (
        <div className="info-card" key={c.label}>
          <div className="label info-label">
            <Icon paths={c.icon} size={13} />
            {c.label}
          </div>
          {c.body}
        </div>
      ))}
    </div>
  )
}

const ICON_FILE_CHECK = (
  <>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <polyline points="14 2 14 8 20 8" />
    <polyline points="9 15 11 17 15 13" />
  </>
)
const ICON_HOURGLASS = (
  <>
    <path d="M5 22h14" />
    <path d="M5 2h14" />
    <path d="M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22" />
    <path d="M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2" />
  </>
)
const ICON_FILES = (
  <>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <polyline points="14 2 14 8 20 8" />
    <path d="M16 13H8" />
    <path d="M16 17H8" />
  </>
)
const ICON_CLOCK = (
  <>
    <circle cx="12" cy="12" r="10" />
    <polyline points="12 6 12 12 16 14" />
  </>
)

function Stats({ status }) {
  const items = [
    { icon: ICON_FILE_CHECK, label: '已备份', value: status.synced_count, cls: 'green' },
    { icon: ICON_HOURGLASS, label: '待备份', value: status.pending_count, cls: 'amber' },
    { icon: ICON_FILES, label: '相机文件总数', value: status.camera_file_count, cls: 'blue' },
    { icon: ICON_CLOCK, label: '上次同步', value: fmtTime(status.last_sync), cls: 'gray', small: true },
  ]
  return (
    <div className="stat-grid">
      {items.map((it) => (
        <div className="stat" key={it.label}>
          <div className={`stat-icon ${it.cls}`}>
            <Icon paths={it.icon} size={16} />
          </div>
          <div className="label">{it.label}</div>
          <div className={`value ${it.cls === 'green' ? 'val-green' : it.cls === 'amber' ? 'val-amber' : ''}`}
            style={it.small ? { fontSize: 14, paddingTop: 2 } : undefined}>
            {it.value}
          </div>
        </div>
      ))}
    </div>
  )
}

const ICON_BOLT = <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />

function SyncProgress({ status, onSync, onStop }) {
  const { progress, syncing } = status
  const pct = progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0
  const modeText = status.sync_mode === 'event'
    ? `事件驱动 · 监听中${status.event_listening ? '' : '（重连中）'} · 超过 ${status.poll_interval}s 无事件自动兜底扫描`
    : `自动同步 · 每 ${status.poll_interval}s 扫描`
  // 进度 60s 无变化且仍在同步中 → 判定卡住，提示相机可能已断线并提供停止入口
  const [stalled, setStalled] = useState(false)
  useEffect(() => {
    setStalled(false)
    if (!syncing) return
    const t = setTimeout(() => setStalled(true), 60000)
    return () => clearTimeout(t)
  }, [syncing, progress.done, progress.total, progress.current])
  return (
    <div className="card sync-card">
      <div className="row" style={{ marginBottom: 10 }}>
        <h2 style={{ margin: 0, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span className="sync-icon"><Icon paths={ICON_BOLT} size={14} /></span>
          {syncing ? '正在同步' : '同步'}
        </h2>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {progress.total > 0 && (
            <span className="progress-count">{progress.done} / {progress.total}</span>
          )}
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
          <div className="progress-bar big">
            <div style={{ width: `${pct}%` }} />
          </div>
        </div>
      )}
      {progress.current && (
        <div className="current-path">{progress.current}</div>
      )}
      <div className="muted sync-footer">
        备份目录 <span className="mono">{status.nas_path}</span>
        {status.auto_sync && <><span className="foot-dot on" style={{ marginLeft: 10 }} />{modeText}</>}
        {!status.auto_sync && ' · 自动同步：关'}
        {status.delete_after_sync && ' · 同步后清理卡上文件：开'}
      </div>
    </div>
  )
}

const ICON_CHECK_CIRCLE = (
  <>
    <circle cx="12" cy="12" r="10" />
    <polyline points="9 12 11 14 15 10" />
  </>
)

function RecentTransfers() {
  const [items, setItems] = useState([])
  useEffect(() => {
    const load = () => api.files(0, 6).then((f) => setItems(f.items)).catch(() => {})
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])
  return (
    <div className="card recent-card">
      <h2>最近传输</h2>
      <div className="recent-list">
        {items.map((f) => (
          <div className="recent-item" key={f.path}>
            <span className="recent-check">
              <Icon paths={ICON_CHECK_CIRCLE} size={15} />
            </span>
            <span className="recent-name">{f.path.split('/').pop()}</span>
            <span className="recent-meta">{fmtSize(f.size)}</span>
            <span className="recent-meta">{(f.synced_at || '').replace('T', ' ').slice(11)}</span>
          </div>
        ))}
        {items.length === 0 && <div className="muted" style={{ padding: '14px 0' }}>暂无传输记录</div>}
      </div>
    </div>
  )
}

const ICON_REFRESH = (
  <>
    <polyline points="23 4 23 10 17 10" />
    <polyline points="1 20 1 14 7 14" />
    <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
  </>
)
const ICON_SHIELD = (
  <>
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    <polyline points="9 12 11 14 15 10" />
  </>
)
const ICON_INFO = (
  <>
    <circle cx="12" cy="12" r="10" />
    <line x1="12" y1="16" x2="12" y2="12" />
    <line x1="12" y1="8" x2="12.01" y2="8" />
  </>
)
const ICON_GITHUB = (
  <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22" />
)

const DEFAULT_CFG = {
  camera_ip: '192.168.5.53',
  camera_port: 8080,
  nas_path: '/Volumes/photos/canon-backup',
  auto_sync: true,
  sync_on_event: true,
  delete_after_sync: false,
  poll_interval: 60,
}

function SectionCard({ icon, title, subtitle, children }) {
  return (
    <div className="card settings-card">
      <div className="settings-head">
        <span className="settings-icon"><Icon paths={icon} size={17} /></span>
        <div>
          <div className="settings-title">{title}</div>
          <div className="muted">{subtitle}</div>
        </div>
      </div>
      {children}
    </div>
  )
}

function Toggle({ checked, onChange, label, desc }) {
  return (
    <div className="toggle-row">
      <div className="toggle-text">
        <div className="toggle-label">{label}</div>
        <div className="muted">{desc}</div>
      </div>
      <button
        type="button"
        className={`switch${checked ? ' on' : ''}`}
        onClick={() => onChange(!checked)}
        aria-pressed={checked}
      >
        <span className="knob" />
      </button>
    </div>
  )
}

function ConfigForm({ version }) {
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

  const resetDefaults = () => {
    if (!window.confirm('恢复为默认设置？当前修改将丢失。')) return
    setCfg({ ...cfg, ...DEFAULT_CFG })
  }

  return (
    <form className="settings-form" onSubmit={submit}>
      <div className="settings-grid">
        <div className="settings-col">
          <SectionCard icon={ICON_WIFI} title="相机连接" subtitle="通过 CCAPI 与相机建立无线连接">
            <div className="form-grid">
              <div className="field" style={{ gridColumn: 'span 1', flex: 3 }}>
                <label>相机 IP</label>
                <input className="mono" value={cfg.camera_ip} onChange={(e) => set('camera_ip', e.target.value)} />
              </div>
              <div className="field" style={{ maxWidth: 120 }}>
                <label>端口</label>
                <input className="mono" type="number" value={cfg.camera_port} onChange={(e) => set('camera_port', e.target.value)} />
              </div>
            </div>
          </SectionCard>

          <SectionCard icon={ICON_SHIELD} title="高级" subtitle="高风险操作，请确认后开启">
            <div className="toggle-group" style={{ marginTop: 0 }}>
              <Toggle
                checked={cfg.delete_after_sync}
                onChange={(v) => set('delete_after_sync', v)}
                label="同步后清理卡上文件"
                desc="备份校验通过后自动删除存储卡上的原文件"
              />
            </div>
          </SectionCard>

          <SectionCard icon={ICON_INFO} title="关于" subtitle="版本与项目信息">
            <div className="about-row">
              <span className="muted">当前版本</span>
              <span className="chip mono">{version ? `v${version}` : '—'}</span>
            </div>
            <div className="about-row">
              <span className="muted">项目地址</span>
              <a
                className="about-link"
                href="https://github.com/xiaokaiwangcc/canon-autosync"
                target="_blank"
                rel="noreferrer"
              >
                <Icon paths={ICON_GITHUB} size={13} />
                xiaokaiwangcc/canon-autosync
              </a>
            </div>
          </SectionCard>
        </div>

        <div className="settings-col">
          <SectionCard icon={ICON_REFRESH} title="同步策略" subtitle="控制备份目录与自动同步行为">
            <div className="field">
              <label>NAS 备份目录</label>
              <input className="mono" value={cfg.nas_path} onChange={(e) => set('nas_path', e.target.value)} placeholder="/Volumes/photos/canon-backup" />
              <div className="field-hint">支持本地挂载路径或 SMB/NFS 挂载点</div>
            </div>
            <div className="field" style={{ marginTop: 12 }}>
              <label>兜底扫描间隔（秒）</label>
              <input className="mono" type="number" min="10" value={cfg.poll_interval} onChange={(e) => set('poll_interval', e.target.value)} />
              <div className="field-hint">事件驱动失效时按此间隔轮询相机文件列表</div>
            </div>
            <div className="toggle-group">
              <Toggle
                checked={cfg.auto_sync}
                onChange={(v) => set('auto_sync', v)}
                label="自动同步"
                desc="检测到新文件后自动开始备份"
              />
              <Toggle
                checked={cfg.sync_on_event}
                onChange={(v) => set('sync_on_event', v)}
                label="事件驱动（拍照后秒级同步）"
                desc="监听相机事件，按下快门后立即拉取文件"
              />
            </div>
          </SectionCard>
        </div>
      </div>

      <div className="settings-actions">
        <button type="button" className="ghost" onClick={resetDefaults}>恢复默认</button>
        <button type="submit" className="btn-danger">保存设置</button>
        {saved && <span className="muted" style={{ alignSelf: 'center' }}>已保存</span>}
      </div>
    </form>
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
          {hasPrev && <button className="modal-nav prev" onClick={nav(-1)}>‹</button>}
          {hasNext && <button className="modal-nav next" onClick={nav(1)}>›</button>}
        </div>
      </div>
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
const IconTrash = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /><line x1="10" y1="11" x2="10" y2="17" /><line x1="14" y1="11" x2="14" y2="17" /></svg>
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
  const [view, setView] = useState('grid')
  const [query, setQuery] = useState('')
  const [files, setFiles] = useState([])
  const [pending, setPending] = useState([])
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState(null)
  const [loaded, setLoaded] = useState(false)
  // 缩略图分批渲染：首屏 50 张，滚到底部哨兵后再加载下一批，避免一次性请求 200 张
  const [visible, setVisible] = useState(50)
  const sentinelRef = useRef(null)

  const rawList = tab === 'synced' ? files : pending
  const q = query.trim().toLowerCase()
  const currentList = q
    ? rawList.filter((x) => x.path.split('/').pop().toLowerCase().includes(q))
    : rawList
  const shown = currentList.slice(0, visible)

  const openPreview = (idx) => {
    // 用全量列表而非已渲染批次：模态框内可翻页到未加载的项（图片 src 仅在展示时才请求）
    const isP = tab === 'pending'
    const list = currentList.map((x) => {
      const src = isP
        ? `/api/thumb?path=${encodeURIComponent(x.path)}`
        : `/api/preview?path=${encodeURIComponent(x.dest)}`
      return { src, name: x.path.split('/').pop() }
    })
    setPreview({ list, index: idx })
  }

  useEffect(() => {
    const load = () => {
      Promise.all([api.files(0, 200), api.pending()])
        .then(([f, p]) => {
          setFiles(f.items)
          setPending(p)
          setError(null)
        })
        .catch(() => setError('列表加载失败，5 秒后自动重试'))
        .finally(() => setLoaded(true))
    }
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  const deleteFile = async (e, camPath) => {
    e.stopPropagation()
    const name = camPath.split('/').pop()
    if (!window.confirm(`删除备份「${name}」？NAS 上的文件将被一并删除，且无法恢复。`)) return
    try {
      await api.deleteFile(camPath)
      setFiles((fs) => fs.filter((f) => f.path !== camPath))
    } catch {
      setError('删除失败，请稍后重试')
    }
  }

  const restoreFile = async (e, camPath) => {
    e.stopPropagation()
    try {
      await api.restoreFile(camPath)
      setPending((ps) => ps.map((p) => (p.path === camPath ? { ...p, ignored: false } : p)))
    } catch {
      setError('恢复失败，请稍后重试')
    }
  }

  // 切换 tab/视图时重置分批加载进度
  useEffect(() => setVisible(50), [tab, view])

  // 列表底部哨兵进入视口时加载下一批缩略图
  useEffect(() => {
    if (!sentinelRef.current) return
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) setVisible((v) => v + 50)
      },
      { rootMargin: '300px' }
    )
    io.observe(sentinelRef.current)
    return () => io.disconnect()
  }, [tab, view, shown.length])

  return (
    <div>
      <div className="tabs-row">
        <div className="seg-tabs">
          <button className={tab === 'synced' ? 'active' : ''} onClick={() => setTab('synced')}>
            <Icon paths={ICON_CHECK_CIRCLE} size={13} />
            已备份 <span className="seg-count">{files.length}</span>
          </button>
          <button className={tab === 'pending' ? 'active' : ''} onClick={() => setTab('pending')}>
            <Icon paths={ICON_HOURGLASS} size={13} />
            待备份 <span className="seg-count">{pending.filter((p) => !p.ignored).length}</span>
          </button>
        </div>
        <div className="files-tools">
          <div className="search-box">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索文件名…"
            />
          </div>
          <div className="view-toggle">
            <button className={view === 'grid' ? 'active' : 'ghost'} onClick={() => setView('grid')} title="图标模式"><IconGrid /></button>
            <button className={view === 'list' ? 'active' : 'ghost'} onClick={() => setView('list')} title="列表模式"><IconList /></button>
          </div>
        </div>
      </div>
      {/* 只渲染当前 tab 的列表：display:none 不会阻止图片加载，双列表同时渲染会重复请求缩略图 */}
      {error && <div className="error" style={{ margin: '0 0 10px' }}>{error}</div>}
      {!loaded ? (
        <div className="muted" style={{ padding: '18px 0', textAlign: 'center' }}>加载中…</div>
      ) : view === 'grid' ? (
        <div className="photo-grid">
          {shown.map((x, i) => {
            const isPending = tab === 'pending'
            const name = x.path.split('/').pop()
            const ignored = isPending && x.ignored
            return (
              <div
                className={`photo-card${ignored ? ' ignored' : ''}`}
                key={x.path}
                onClick={() => openPreview(i)}
                title={ignored ? `${x.path}\n已忽略，不会自动备份` : x.path}
              >
                {!isPending && (
                  <button
                    className="photo-del"
                    title="删除备份"
                    onClick={(e) => deleteFile(e, x.path)}
                  >
                    <IconTrash />
                  </button>
                )}
                {ignored && <span className="ignored-tag">已忽略</span>}
                <Thumb
                  src={isPending
                    ? `/api/thumb?path=${encodeURIComponent(x.path)}`
                    : `/api/preview?path=${encodeURIComponent(x.dest)}&size=480`}
                  name={name}
                />
                <div className="photo-foot">
                  <span className="photo-name">{name}</span>
                  {ignored ? (
                    <button className="restore-btn" onClick={(e) => restoreFile(e, x.path)}>恢复备份</button>
                  ) : (
                    <span className="photo-size">{isPending ? x.path.split('/').slice(-2)[0] : fmtSize(x.size)}</span>
                  )}
                </div>
              </div>
            )
          })}
          {shown.length === 0 && (
            <div className="grid-empty muted">{q ? '没有匹配的文件' : tab === 'synced' ? '暂无记录' : '没有待备份文件'}</div>
          )}
        </div>
      ) : tab === 'synced' ? (
        <div className="card">
        <table>
          <thead>
            <tr><th>封面</th><th>文件</th><th>大小</th><th>备份位置</th><th>时间</th><th></th></tr>
          </thead>
          <tbody>
            {shown.map((f, i) => (
              <tr key={f.path} onClick={() => openPreview(i)} title="点击预览">
                <td><Thumb src={`/api/preview?path=${encodeURIComponent(f.dest)}&size=160`} name={f.path.split('/').pop()} /></td>
                <td>{f.path.split('/').pop()}</td>
                <td>{fmtSize(f.size)}</td>
                <td>{f.dest}</td>
                <td>{fmtTime(f.synced_at)}</td>
                <td>
                  <button className="row-del" title="删除备份" onClick={(e) => deleteFile(e, f.path)}>
                    <IconTrash />
                  </button>
                </td>
              </tr>
            ))}
            {shown.length === 0 && <tr><td colSpan="6" className="muted">暂无记录</td></tr>}
          </tbody>
        </table>
        </div>
      ) : (
        <div className="card">
        <table>
          <thead><tr><th>封面</th><th>相机上的文件</th><th></th></tr></thead>
          <tbody>
            {shown.map((p, i) => (
              <tr key={p.path} onClick={() => openPreview(i)} title={p.ignored ? '已忽略，不会自动备份' : '点击预览'}>
                <td><Thumb src={`/api/thumb?path=${encodeURIComponent(p.path)}`} name={p.path.split('/').pop()} /></td>
                <td>
                  {p.path}
                  {p.ignored && <span className="tag" style={{ marginLeft: 8 }}>已忽略</span>}
                </td>
                <td>
                  {p.ignored && (
                    <button className="restore-btn" onClick={(e) => restoreFile(e, p.path)}>恢复备份</button>
                  )}
                </td>
              </tr>
            ))}
            {shown.length === 0 && <tr><td colSpan="3" className="muted">没有待备份文件</td></tr>}
          </tbody>
        </table>
        </div>
      )}
      {shown.length < currentList.length && (
        <div ref={sentinelRef} className="muted" style={{ padding: '10px 0', textAlign: 'center' }}>
          加载更多…
        </div>
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
          <div>
            <h1>{PAGE_TITLES[page]}</h1>
            <div className="page-subtitle">{PAGE_SUBTITLES[page]}</div>
          </div>
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
            <InfoCards status={status} />
            <Stats status={status} />
            <div className="bottom-grid">
              <SyncProgress status={status} onSync={syncNow} onStop={stopSync} />
              <RecentTransfers />
            </div>
          </>
        )}
        {page === 'files' && <FileList />}
        {page === 'settings' && <ConfigForm version={status.version} />}
      </main>
    </div>
  )
}
