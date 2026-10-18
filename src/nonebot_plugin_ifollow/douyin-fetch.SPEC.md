---
id: douyin-fetch
type: module-design
status: draft
title: 抖音作品获取契约
---

## Responsibility

`douyin_platform.py` 负责把抖音作者 ID 解析成"最新作品列表"，并把抖音返回的作品字段映射成
平台无关的 `PlatformWork`；`douyin_abogus.py` 负责生成该接口要求的 `a_bogus` 签名。
轮询节奏、失败退避与消息发送不属于本模块。

## Boundary

- 输入：作者主页链接、作品分享链接、`sec_user_id`，或轮询时的 `sec_user_id`。
- 输出：`PlatformWork` 列表（按 `create_time` 降序，最新在前），或 `DouyinRequestError`。
- 不负责翻页、历史回填与视频下载；下载交给 `nonebot-plugin-parser`。

## 接口契约

抖音 `www.douyin.com/aweme/v1/web/*` 接口同时要求三件事，缺一即失败（2026-10 实测）：

| 要素 | 缺失时的表现 |
| :--- | :--- |
| `a_bogus` 查询参数（SM3 签名，绑定查询串与 UA） | 200 + 空 body |
| `ttwid` Cookie（由 `ttwid.bytedance.com` 注册获得） | 200 + 空 body |
| `x-tt-argus: 1` 请求头 | 403 `Blocked by ArgusSecurityPlugin` |

因此：请求 UA 必须与签名 UA 一致（`USER_AGENT` 单一常量同时供两处使用）；签名与 ttwid 每次
请求重新生成，不做长期缓存。

## 决策

- **空 body 与 403 视为签名/twid 失效**：丢弃缓存的 ttwid、重新注册并重试一次，仍失败才抛可重试
  异常交给作者级退避。不把它们当成网络错误直接上抛，否则一次 ttwid 过期会让该作者长时间停更。
- **ttwid 由插件自动注册**：匿名即可轮询，不要求用户配置 Cookie。配置 Cookie 中的 `ttwid` 会被
  自动注册值覆盖（否则无法自愈），其余字段原样透传。
- **签名算法内嵌而非依赖 f2**：f2 是完整 CLI 应用、依赖链重；内嵌只引入纯 Python 的 `gmssl`。
  代价是抖音改算法时需要人工同步上游 `f2/utils/abogus.py`。
- **只取最新一页**：匿名态用 `max_cursor` 翻页恒返回空列表（加 `msToken`、`locate_item_id` 均无效），
  而轮询只需要最新 N 条，与"首次订阅只记录当前最新作品"的不变量一致。
- **`status_code == 5` 不可重试**：表示作者不存在或已注销，重试只会浪费配额。

## 发送链路

抖音作品的下载与发送由 `douyin_media.py` 完成，依赖 `parsehub`（`https://github.com/z-mio/ParseHub`）：
解析作品链接 → 下载媒体到临时目录 → 拼成「文案 + 媒体」的 `UniMessage` 序列发给目标会话。

- **为什么不用 nonebot-plugin-parser**：部署主机上只有 parsehub，且 parsehub 已支持抖音详情与下载；
  parser 的渲染卡片并非订阅推送必需，去掉它可少一层依赖。B 站发送仍走 parser，待那边迁移后一并移除。
- **不渲染卡片**：推送只要文本 + 媒体，图集按每批 9 张合并，单作品最多 20 个媒体，
  超过 `IFOLLOW_MEDIA_MAX_MB` 的媒体直接跳过。
- **临时目录在上下文退出时删除**，所以必须在 `douyin_media_messages` 上下文内把消息发完。
- **代价**：`parsehub` 要求 Python >= 3.12，本项目的最低版本随之抬高；
  且不再单独发送抖音音乐轨（旧 parser 会额外发一条音频）。

## 已知限制

- `a_bogus` 是逆向实现，抖音调整算法后需要同步上游；`tests/test_douyin_abogus.py` 的已知向量
  用于确认"没有意外改动"，不能发现"上游算法已变"。
- ttwid 的有效期未知（未确认），当前只在请求失败时失效重建，没有 TTL。
- 抖音媒体直链带签名且会过期，发送失败重试时必须重新解析，不能复用旧的下载结果。
