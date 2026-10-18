---
id: bilibili-send
type: module-design
status: draft
title: B 站作品发送契约
depends-on: [media]
---

## Responsibility

`bilibili_media.py` 负责把 B 站视频或动态链接变成可发送的 `UniMessage` 序列：
parsehub 解析 → 下载媒体到临时目录 → 拼成「文案 + 媒体」。解析、下载、图片分批与体积限制
复用 `media.py`；轮询、游标与消息发送不属于本模块。

## Boundary

- 输入：B 站视频链接（`www.bilibili.com/video/BV...`）、动态链接（`t.bilibili.com/{id}` 或
  `www.bilibili.com/opus/{id}`）。
- 输出：`UniMessage` 列表，第一条是文案（作者 + 标题/正文 + 参数行 + 发布时间），其后是媒体。
- 不负责作者与作品列表获取；那部分在 `bilibili_platform.py`。
- 媒体落在临时目录，上下文退出时删除，调用方必须在上下文内发送完毕。

## 消息格式

文案固定为四部分，首行是「平台 · 昵称」，平台差异只体现在时长单位（抖音毫秒、B 站秒）：

```
抖音 · 呆呆丸 发布了新作品
────────────
不烧心 #cos#拉菲#碧蓝航线#拉菲兔兔

00:07 · 720×1280 · 1.0 MB
2026-10-11 18:35
```

- 首行用 ` · ` 连接平台与昵称，与订阅列表的称呼保持一致；
- 参数行按「数量 / 时长 / 分辨率 / 体积」顺序，用 ` · ` 拼接，缺项自动省略；
  多图时数量显示为 `9 张`，含视频时显示为 `N 个`。
- 不发送作品链接：媒体本体就是内容，链接只会多占一行。
- 订阅人 `@` 由 `delivery.py` 在文案末尾追加一条分隔线后再拼，不单独发提醒消息。

## 决策

- **发送统一走 parsehub**：抖音和 B 站共用 `media.py` 的「解析 → 下载 → 拼消息」管线，
  平台模块只传 `MediaPolicy`、Cookie、日志名和临时目录前缀。
- **接受 720p**：parsehub 的 B 站 `playurl` 固定 `qn=64` 且不带 `SESSDATA`，配置 Cookie
  不会提升画质。相比旧 `nonebot-plugin-parser` 的默认 1080p 是画质回退，换来移除 parser 依赖。
- **动态链接用 `t.bilibili.com/{id}`**：parsehub 的匹配规则只接受 `video`、`opus` 或
  直接跟 18–19 位动态 ID 的链接，`www.bilibili.com/dynamic/{id}` 会报 `UnknownPlatform`。
  因此在 `bilibili_platform._parse_dynamic_item` 直接生成 `t.bilibili.com` 链接；
  opus 动态保留接口返回的 `jump_url`。
- **Cookie 只用于动态和兜底**：`IFOLLOW_BILIBILI_COOKIE` 传给 parsehub，用于动态详情和
  受限接口；视频取流不读该 Cookie。
- **`bilibili-api-python` 保留为直接依赖**：动态列表和网页兜底仍用 `Credential` / `User` / `Video`，
  移除 parser 后必须显式声明。
- **不在媒体层吞异常**：解析或下载失败直接抛出，交给现有的发送重试和游标保留逻辑。
- **目标群不可用不重试**（抖音、B 站共用，实现在 `delivery.py` 的 `handle_target_failure`）：
  发送失败信息里出现「被移出该群」「群不存在」等关键字时，判定为目标已不可用，本轮直接跳过
  该目标、不写退避状态，游标也不推进。每个目标只在首次命中时打一条 warning，之后降为 debug，
  避免每轮刷屏。
- **媒体默认走本地路径**：`media.py` 把下载好的文件以 `path=` 交给协议端，插件不读字节也不编码，
  大视频不会拖慢消息处理。只有协议端与插件不在同一台机器、读不到同一路径时，才用
  `IFOLLOW_USE_BASE64=true` 读成字节并编码成 `base64://`；代价是体积放大 1/3 且整个文件塞进一条
  WS 消息，`IFOLLOW_MEDIA_MAX_MB` 要按协议端的承受能力调低。

## 已知限制

- B 站视频固定 720p；需要 1080p 时要另做 bilibili-api + ffmpeg 下载链路。
- 开启 `IFOLLOW_USE_BASE64` 后 base64 会把体积放大 1/3，几十 MB 的视频可能被协议端拒收；
  日志里出现发送失败时优先调低 `IFOLLOW_MEDIA_MAX_MB`，而不是加大重试。
- 动态解析在匿名态容易触发 `-352` 风控，建议配置 `IFOLLOW_BILIBILI_COOKIE`。
- parsehub 是同步库，解析调用在线程中执行，首次导入有数秒延迟。
- 动态解析依赖 `t.bilibili.com` 链接形式；旧格式链接需要先改写。
- 旧 `nonebot-plugin-parser` 的卡片渲染和 1080p 能力不再保留。

## 后续

拉取侧已改为 APP 接口优先，细节见 `bilibili-fetch.SPEC.md`。
