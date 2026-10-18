---
id: media
type: module-design
status: draft
title: 媒体解析、下载与消息拼装契约
---

## Responsibility

`media.py` 是抖音与 B 站共用的媒体管线：parsehub 解析作品 → 按策略过滤体积 → 下载到临时目录 →
拼成「文案 + 媒体」的 `UniMessage` 序列。平台差异只有 Cookie、日志名、临时目录前缀和时长单位。

## Boundary

- 输入：作品链接、`MediaPolicy`（体积上限、数量上限、图片批次、传输方式）、可选 Cookie、`WorkMeta`。
- 输出：`UniMessage` 列表，第一条是文案，其后是媒体。
- 不发送消息、不判断新旧作品、不推进游标；那些属于轮询子系统。
- 媒体落在临时目录，上下文退出时删除，调用方必须在 `media_messages_for` 上下文内发送完毕。

## 策略对象

`MediaPolicy.from_settings()` 把 `IFOLLOW_MEDIA_MAX_MB` / `IFOLLOW_USE_BASE64` 换算一次，
之后体积判断只读策略对象，不再各处重复换算配置。平台模块（`douyin_media.py` /
`bilibili_media.py`）只负责构造策略与传入 Cookie。

## parsehub 的约束

- parsehub 是同步库，首次导入还要加载 numpy/opencv（数秒）：导入与解析都放进线程执行，
  实例全局复用。
- 不在媒体层吞异常：解析或下载失败直接抛出，交给投递层的重试与游标保留逻辑。

## 文案

由 `caption()` 拼装，格式（作者、分隔线、标题/正文、参数行、发布时间）记在
`bilibili-send.SPEC.md`，这里不重复。

## 数量与体积

- 单个作品最多发送 20 个媒体。
- 图片按每批 9 张合并成一条消息，视频单独一条。
- 单个媒体超过 `IFOLLOW_MEDIA_MAX_MB` 或文件不存在时跳过并告警，不中断整条作品。
- **下载前先探测视频类体积**：视频、动图、实况照片在 `download()` 之前先 HEAD 远端长度
  （拿不到时用 `Range: bytes=0-0` 读 `Content-Range`），超限的直接不下载；探测失败或服务端
  不返回长度时保留媒体，退回下载后的体积检查。图片不探测，避免图集多打请求。
  超限日志只记扩展名与体积，不打印带签名的直链。探测请求不带自定义 UA / Referer，
  与 parsehub 下载器一致；各 CDN 是否接受 HEAD 未确认（未确认），不接受时退回下载后检查。

## 传输方式

- 默认把本地路径交给协议端，插件不读字节也不编码。
- 只有协议端读不到同一路径时才开启 base64，代价与取舍见 `bilibili-send.SPEC.md`。
