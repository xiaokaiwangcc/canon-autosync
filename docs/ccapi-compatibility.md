# CCAPI 兼容性

canon-autosync 不再把整台相机固定为单一 CCAPI 版本。程序连接 `/ccapi/` 后读取相机公布的版本入口和资源地址，再按功能选择实际 endpoint；目录、事件、设备信息和状态接口可以分别来自不同版本命名空间。

## 当前适配范围

| 能力 | 处理方式 | 不支持时的行为 |
| --- | --- | --- |
| CCAPI 入口 | 递归解析 `/ccapi/` 及其版本入口公布的 URL | 使用内置候选路径探测 |
| 文件目录 | 优先使用相机公布的 contents 地址；否则尝试 `ver130`、`ver140`、`ver120`、`ver110`、`ver100` | 所有候选均不可用时报告连接错误 |
| 目录响应 | 支持普通 JSON 和多个 JSON 对象连续返回的 chunked 格式 | `kind/order` 参数不支持时回退普通列表 |
| 事件监听 | 使用相机公布的 event polling 地址，并保留多版本候选 | 404/405 时降级为定时扫描 |
| 设备、电池、温度、存储 | 使用相机公布的地址，并分别缓存已确认的 endpoint | 单项不可用时不阻断文件同步 |
| 下载、缩略图、删除 | 使用 contents 响应返回的文件 href | 请求失败时沿用现有重试和错误处理 |

`/api/status` 的 `ccapi` 字段会返回本次运行发现的版本、已确认 endpoint、不支持的可选能力和目录列表模式，便于收集不同机型的兼容性信息。已经确认不支持的能力会被缓存，避免每轮状态刷新都重复探测相机。

## 已验证范围

- 自动发现相机公布在其他版本命名空间中的 contents 和 event endpoint。
- 未公布资源目录时，在 `ver100`–`ver140` 候选之间回退。
- `kind=chunked&order=desc` 不受支持时回退到普通目录请求。
- 合并 chunked 响应中的多个连续 JSON 对象。

这些是协议行为的自动化测试，不代表所有佳能机型和固件均已实机验证。新增实测结果时，应记录机型、固件版本、发现到的 CCAPI 版本、contents 模式及不可用能力。

旧版本若只提供带分页字段的普通 contents 响应，目前仍需对应机型的脱敏响应样本才能确认翻页规则；在完成实机验证前，不应把普通列表降级解释为“大目录已完整验证”。

## 实机兼容记录

### Canon EOS R7（固件 1.8.0）

| 机型 | 固件 | Canon AutoSync 版本 | 记录日期 |
| --- | --- | --- | --- |
| Canon EOS R7 | 1.8.0 | v1.0.8 | 2026-08-05（界面显示的上次同步日期） |

- 界面显示相机已通过 CCAPI 连接，相机文件 77 个、已备份 77 个、待备份 0 个。最近传输记录包含 JPG 照片和 MP4 视频。
- 界面显示电量、温度和存储卡容量。电量接口的实测响应为 `level=high`。 [电量接口实测记录](https://github.com/xiaokaiwangcc/canon-autosync/blob/1f87637a2ea5bb0d2bd71dfba32f75f6faedda2d/backend/app/sync.py#L101-L107)
- 界面显示「监听拍摄中」。新拍摄能否自动触发备份及触发延迟待补测。

这条记录依据提交 `1f87637` 保存的历史界面截图和接口实测注释。部署平台、目录响应模式、RAW 文件备份、断线重连，以及 v1.2.2 的实机表现待补测。

## 实机验证

相机连通后查看：

```text
GET /api/status
```

提交实测前，请单独记录机型、固件、应用版本、部署平台和测试日期。从响应中摘录下方示例里的 `ccapi` 字段，并遮盖 IP、序列号、MAC 地址、私有目录和凭据。 [提交机型兼容性报告](https://github.com/xiaokaiwangcc/canon-autosync/issues/new?template=camera-compatibility.yml)

```json
{
  "ccapi": {
    "versions": ["100", "110", "130"],
    "endpoints": {
      "contents": "/ccapi/ver130/contents",
      "event": "/ccapi/ver100/event/polling"
    },
    "unsupported": [],
    "contents_list_mode": "chunked"
  }
}
```

佳能公开页面提供 CCAPI Kit 的版本摘要，但完整 endpoint 定义以取得许可后的官方 Programming Reference 为准。参见[佳能 SDK Camera API Package 获取入口](https://personal.canon.jp/product/camera/software/api-package)。
