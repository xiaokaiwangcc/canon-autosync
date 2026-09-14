# 版本变更与 Bug 修复记录

这里集中记录各版本面向用户的功能变化、构建调整和 Bug 修复。内容按 Git 标签和提交历史整理；安装包与发布资产请到 [GitHub Releases](https://github.com/xiaokaiwangcc/canon-autosync/releases) 下载。

## Bug 修复索引

| 版本 | 问题 | 修复结果 |
| --- | --- | --- |
| [v1.1.1](#2026-09-06v1012--v111) | CCAPI 的 `kind=chunked` 响应包含连续 JSON 对象，按单个 JSON 解析时会报 `Extra data` | 逐段解析并合并所有 `path`，完整读取相机目录 |
| [v1.1.0](#2026-09-06v1012--v111) | FPK manifest 的版本字段缺失、重复或格式错误时，构建仍可能继续 | 构建前校验字段数量和 `x.y.z` 格式，并验证写入结果 |
| [v1.0.12](#2026-09-06v1012--v111) | 普通 contents 请求最多返回 100 条，旧文件可能无法进入同步队列 | 改用分段、倒序目录请求；多段响应兼容在 v1.1.1 完成 |
| [v1.0.12](#2026-09-06v1012--v111) | 单个文件下载失败会中断整批同步 | 跳过失败文件继续同步，其失败状态保留到下一轮重试 |
| [v1.0.11](#2026-08-07v109--v1011) | PyInstaller hook 会把完整版 ffmpeg 再次打入 Windows 包 | 排除 `imageio_ffmpeg`，避免重复打包和包体积回涨 |
| [v1.0.10](#2026-08-07v109--v1011) | FPK Release 已存在或并发构建时可能发布失败 | 已有 Release 改为补传资产；版本自增前先同步远端 |
| [v1.0.4](#2026-08-05v102--v108) | Windows 构建环境无法正确输出图标脚本中的中文 | 修正输出编码，恢复 Windows 图标构建 |

## 版本历史

### Unreleased

- 暂无未发布变更。

### 2026-09-06（v1.0.12 – v1.1.1）

#### 修复

- **v1.1.1**：正确解析 CCAPI 分段目录响应中的多个连续 JSON 对象，合并全部文件路径，避免 `Extra data` 导致目录读取失败；同时增加回归测试（[`bd7f859`](https://github.com/xiaokaiwangcc/canon-autosync/commit/bd7f85902515e3c9fc76e06887a6975e83e304dd)）。
- **v1.1.0**：FPK 构建前检查 manifest 中必须且只能存在一个 `version` 字段，且版本号必须符合 `x.y.z`；写入后再次校验结果（[`6da9929`](https://github.com/xiaokaiwangcc/canon-autosync/commit/6da9929eeed441784e2aa1fbea6d06487331ef5f)）。
- **v1.0.12**：CCAPI 目录列表改用 `kind=chunked` 和倒序请求，为读取超过 100 条的完整目录提供支持；多段响应解析随后在 v1.1.1 完成。
- **v1.0.12**：单个文件发生相机断连、HTTP 或本地 I/O 错误时继续处理后续文件；失败文件留到下一轮重试，同步结果分别报告成功和失败数量（[`412b5ab`](https://github.com/xiaokaiwangcc/canon-autosync/commit/412b5ab43b81e9367a09057d3c916e6a6dd6e349)）。

### 2026-08-07（v1.0.9 – v1.0.11）

#### 新增与改进

- **v1.0.10**：Windows 桌面包改用裁剪编译的最小化 ffmpeg，体积减少约 30 MB；FPK manifest 版本与发布版本对齐。
- **v1.0.9**：支持批量清理已删除的备份记录。
- **v1.0.9**：完善 RAW 图片和视频预览，无法预览的文件显示类型占位图。
- **v1.0.9**：优化文件列表、缩略图和预览接口的路径校验及缓存行为。

#### 修复

- **v1.0.11**：排除 `imageio_ffmpeg` 的 PyInstaller hook，防止完整版 ffmpeg 与裁剪版一起进入桌面安装包（[`eb121c9`](https://github.com/xiaokaiwangcc/canon-autosync/commit/eb121c9)）。
- **v1.0.10**：GitHub Release 已存在时改为补传 FPK 资产；版本号自增前先同步远端，避免发布冲突。

### 2026-08-05（v1.0.2 – v1.0.8）

#### 新增

- **v1.0.7**：增加相机事件轮询、缩略图保护、备份文件删除与恢复，以及状态面板和文件浏览功能。
- **v1.0.3**：增加 Docker 部署，以及基于 pywebview 和 PyInstaller 的 macOS/Windows 桌面应用。

#### 改进

- **v1.0.8**：优化 FPK 构建时的版本号写入流程和相关说明。
- **v1.0.7**：重构响应式界面与设置项布局，并补充 macOS 未签名应用首次打开说明。
- **v1.0.6**：Windows 桌面应用改为单文件 EXE，并直接作为 Release 资产发布。
- **v1.0.5**：GitHub Release 自动附带 macOS DMG 和 Windows ZIP 桌面安装包。
- **v1.0.2**：更新 fnOS manifest 的应用描述、维护者和设备支持信息。

#### 修复

- **v1.0.4**：修复 Windows 构建时图标生成脚本的中文输出编码错误（[`c2db353`](https://github.com/xiaokaiwangcc/canon-autosync/commit/c2db353)）。

### 2026-08-04（v1.0.1）

#### 首次发布

- 提供佳能 CCAPI 照片/视频增量备份服务和 fnOS 打包脚本。
- 支持手动停止同步，并加强下载超时、停滞检测和状态保存。
- 增加 GitHub Actions FPK 构建流程。

## 维护约定

- 新变更先写入 `Unreleased`，发布时按发布日期归档；同一天的多个标签合并为一个版本段。
- 使用“新增”“改进”“修复”分类；Bug 修复同时更新顶部索引，写清问题表现和修复结果。
- 版本和日期以 Git 标签为准，具体实现链接到对应提交，避免在 README、Release 说明和多个修复文档中重复维护同一份历史。
