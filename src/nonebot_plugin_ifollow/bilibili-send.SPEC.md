---
id: bilibili-send
type: module-design
status: draft
title: B 站作品发送契约
---

## Responsibility

`bilibili_media.py` 负责把 B 站视频或动态链接变成可发送的 `UniMessage` 序列：
parsehub 解析 → 下载媒体到临时目录 → 拼成「文案 + 媒体」。解析、下载、图片分批与体积限制
复用 `media.py`；轮询、游标与消息发送不属于本模块。

## Boundary

- 输入：B 站视频链接（`www.bilibili.com/video/BV...`）、动态链接（`t.bilibili.com/{id}` 或
  `www.bilibili.com/opus/{id}`）。
- 输出：`UniMessage` 列表，第一条是文案（标题/正文/链接去重），其后是媒体。
- 不负责作者与作品列表获取；那部分在 `bilibili_platform.py`。
- 媒体落在临时目录，上下文退出时删除，调用方必须在上下文内发送完毕。

## 决策

- **发送统一走 parsehub**：抖音和 B 站共用 `media.py` 的「解析 → 下载 → 拼消息」管线，
  平台模块只传 Cookie、日志名和临时目录前缀。
- **接受 720p**：parsehub 的 B 站 `playurl` 固定 `qn=64` 且不带 `SESSDATA`，配置 Cookie
  不会提升画质。相比旧 `nonebot-plugin-parser` 的默认 1080p 是画质回退，换来移除 parser 依赖。
- **动态链接用 `t.bilibili.com/{id}`**：parsehub 的匹配规则只接受 `video`、`opus` 或
  直接跟 18–19 位动态 ID 的链接，`www.bilibili.com/dynamic/{id}` 会报 `UnknownPlatform`。
  因此在 `bilibili_platform._parse_dynamic_item` 直接生成 `t.bilibili.com` 链接；
  opus 动态保留接口返回的 `jump_url`。
- **Cookie 只用于动态和兜底**：`IFOLLOW_BILIBILI_COOKIE` 传给 parsehub，用于动态详情和
  受限接口；视频取流不读该 Cookie。
- **`bilibili-api-python` 保留为直接依赖**：`bilibili_platform.py` 仍用
  `Credential` / `User` / `Video` 拉取作者、投稿和动态，移除 parser 后必须显式声明。
- **不在媒体层吞异常**：解析或下载失败直接抛出，交给现有的发送重试和游标保留逻辑。

## 已知限制

- B 站视频固定 720p；需要 1080p 时要另做 bilibili-api + ffmpeg 下载链路。
- 动态解析在匿名态容易触发 `-352` 风控，建议配置 `IFOLLOW_BILIBILI_COOKIE`。
- parsehub 是同步库，解析调用在线程中执行，首次导入有数秒延迟。
- 动态解析依赖 `t.bilibili.com` 链接形式；旧格式链接需要先改写。
- 旧 `nonebot-plugin-parser` 的卡片渲染和 1080p 能力不再保留。

## 后续

`research-bilibili-stable-fetch` 分支上的 APP 接口拉取改进尚未合入本分支；合入后
`bilibili_platform.py` 的视频与作者资料将不再依赖网页接口。
