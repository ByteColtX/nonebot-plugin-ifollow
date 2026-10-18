---
id: douyin-fetch
type: module-design
status: draft
title: 抖音作品获取契约
depends-on: [media]
---

## Responsibility

`douyin_platform.py` 实现 `PlatformAdapter` 协议：把抖音作者链接解析成作者与首页作品，把作者
作品列表映射成平台无关的 `PlatformWork`；`douyin_abogus.py` 负责生成该接口要求的 `a_bogus`
签名。轮询节奏、失败退避与消息发送不属于本模块。

## Boundary

- 输入：`Settings`、作者主页链接 / 作品链接 / `sec_user_id`，或轮询时的 `sec_user_id`。
- 输出：`ResolvedAuthor`（作者 + 首页作品）或 `list[PlatformWork]`（从新到旧）。
- 错误：只抛 `errors.TransientError` / `PermanentError`，不再自带 `retryable` 标志。
- 不负责翻页策略、历史回填与视频下载；翻页交给 `pagination.paginate`，下载交给 `media.py`。

## 接口契约

抖音 `www.douyin.com/aweme/v1/web/*` 接口同时要求三件事，缺一即失败（2026-10 实测）：

| 要素 | 缺失时的表现 |
| :--- | :--- |
| `a_bogus` 查询参数（SM3 签名，绑定查询串与 UA） | 200 + 空 body |
| `ttwid` Cookie（由 `ttwid.bytedance.com` 注册获得） | 200 + 空 body |
| `x-tt-argus: 1` 请求头 | 403 `Blocked by ArgusSecurityPlugin` |

请求 UA 必须与签名 UA 一致（`USER_AGENT` 单一常量同时供两处使用）；签名与 ttwid 每次请求重新
生成，不做长期缓存。

## 决策

- **空 body 与 403 视为签名/twid 失效**：丢弃缓存的 ttwid、重新注册并重试一次，仍失败才抛
  `TransientError` 交给作者级退避。不把它们当网络错误直接上抛，否则一次 ttwid 过期会让该作者
  长时间停更。
- **ttwid 由插件自动注册**：匿名即可轮询，不要求用户配置 Cookie。配置 Cookie 中的 `ttwid` 会被
  自动注册值覆盖（否则无法自愈），其余字段原样透传。
- **Cookie 在客户端级维护**：配置 Cookie 在创建客户端时注入，ttwid 每次请求前用
  `client.cookies.update()` 刷新；不再用 `client.get(..., cookies=...)`（httpx 0.28 已弃用）。
- **不关 TLS 校验**：签名、ttwid 与 Cookie 都经这几个连接传输，一律校验证书。
- **`status_code == 5` 是永久错误**：表示作者不存在或已注销，抛 `PermanentError`，轮询层直接
  停用该作者。
- **签名算法内嵌而非依赖 f2**：f2 是完整 CLI 应用、依赖链重；内嵌只引入纯 Python 的 `gmssl`。
  代价是抖音改算法时需要人工同步上游 `f2/utils/abogus.py`。
- **分页不传条数**：请求不带 `count`，页大小用平台默认。首次订阅（无游标）只取第一页；
  轮询带游标时翻到盖到游标、或攒够 `IFOLLOW_SUB_MAX_ITEMS` 条（默认 5）就停。
  实测（2026-10，匿名）省略 `count` 时单页返回 5 条，因此默认上限 5 通常一页就够；
  页偏小时只是多翻几页，上限由条数兜底。

## 发送链路

抖音作品的下载与发送由 `douyin_media.py` 完成，依赖 `parsehub`：解析作品链接 → 下载媒体到
临时目录 → 拼成「文案 + 媒体」的 `UniMessage` 序列。解析、下载、图片分批与体积限制都在
`media.py`，`douyin_media.py` 只把 `Settings` 换算成 `MediaPolicy`。B 站发送见
`bilibili-send.SPEC.md`。

## 已知限制

- `a_bogus` 是逆向实现，抖音调整算法后需要同步上游；`tests/test_douyin_abogus.py` 的已知向量
  用于确认「没有意外改动」，不能发现「上游算法已变」。
- ttwid 的有效期未知（未确认），当前只在请求失败时失效重建，没有 TTL。
- 抖音媒体直链带签名且会过期，发送失败重试时必须重新解析，不能复用旧的下载结果。
