# canon-autosync

自动把佳能相机（CCAPI）里的照片备份到 NAS 指定目录。Python(FastAPI) 后端 + React(Vite) 前端。参考 [laszewsk/canon-r7-ccapi](https://github.com/laszewsk/canon-r7-ccapi)。

## 原理

相机开启 WiFi + CCAPI 后，后端每 30s（可配置）轮询一次：
相机在线 → 递归列出 SD 卡文件 → 与本地同步记录对比 → 增量下载新文件到 NAS 目录（保持相机目录结构）。

## 界面功能

- **双标签**：已备份 / 待备份，每 5s 自动刷新
- **缩略图封面**：列表每行显示照片缩略图；RAW（CR3 等）、视频等无法预览的格式显示类型图标（RAW 徽章/播放图标）。缩略图请求经串行化 + 内存缓存保护，避免相机 CCAPI 被并发请求打挂（503）
- **列表 / 图标模式**：右上角 ☰ / ▦ 切换表格视图或相册式网格浏览
- **预览弹窗**：点击封面/文件打开大图，支持左右箭头按钮或键盘 ← → 切换（同一标签内），Esc 关闭，视频可直接播放

## 相机准备

1. 相机固件升级到支持 CCAPI 的版本（R7/R6/R5/R3/R10/R50/M50 II 等）
2. **激活 CCAPI**（只需一次）：在佳能开发者社区（developercommunity.usa.canon.com，免费注册）下载 CCAPI 开发包，用其中的 Activation Tool 通过 USB 连接相机激活。激活后相机 Wi-Fi/蓝牙连接菜单会出现 CCAPI 选项
3. 相机菜单：Wi-Fi/蓝牙连接 → CCAPI → 连接路由器，停在显示 IP/端口的界面
4. 确认 `http://<相机IP>:8080/ccapi/` 返回 JSON

注意：
- "连接至智能手机"和"遥控(EOS Utility)"模式**不会**启动 CCAPI HTTP 服务，必须用激活后出现的 CCAPI 连接选项
- R7 的文件浏览接口是 `/ccapi/ver130/contents`（返回完整 href），本项目的客户端已按此实现

## 启动

```bash
# 后端
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000

# 前端
cd frontend
npm install
npm run dev
```

打开 http://localhost:5173 ，在"设置"里填相机 IP 和 NAS 挂载目录（如 `/Volumes/photos/canon-backup`）。

也可以用环境变量覆盖默认值：

```bash
export CANON_IP=192.168.1.50
export NAS_PATH=/Volumes/photos/canon-backup
```

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/status | 相机在线状态、同步进度、统计 |
| GET/POST | /api/config | 读取/保存配置 |
| POST | /api/sync | 手动触发同步 |
| GET | /api/files | 已备份文件列表 |
| GET | /api/pending | 相机上待备份文件 |
| GET | /api/thumb?path=... | 相机文件缩略图（串行请求 + 内存缓存） |

配置和同步状态存于 `backend/data/`。

## 打包飞牛 fnOS 应用（.fpk）

```bash
./fpk/build.sh        # 构建前端 + 组装 + fnpack build
```

产物：`fpk/canon-autosync/canon-autosync.fpk`，在飞牛应用中心 → 手动安装 上传即可。

打包机制：
- 前端构建为静态文件，由 FastAPI 在 **8315 端口**直接托管（单端口，桌面 iframe 打开）
- 依赖飞牛官方 Python 运行时（`install_dep_apps=python312`），首次启动时自动创建 venv 并 pip install（需 NAS 能访问 PyPI）
- 照片默认备份到安装向导中选择的目录；安装/配置向导会询问相机 IP 和备份目录，提交后写入应用配置并重启生效。使用自定义目录时需在 应用设置 → 授权目录 中授权该目录
- 配置和同步记录存于应用数据目录（`TRIM_PKGVAR`），升级不丢失
- 应用中心可启动/停止；日志在应用数据目录 `app.log`
