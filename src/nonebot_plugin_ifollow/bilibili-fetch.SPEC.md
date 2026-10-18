---
id: bilibili-fetch
type: module-design
status: draft
title: B 站作品获取契约
---

## Responsibility

`bilibili_platform.py` 负责把 B 站作者链接解析成作者资料，并把作者的最新视频投稿/动态映射成
平台无关的 `PlatformWork`。轮询节奏、失败退避与消息发送不属于本模块。

## Boundary

- 输入：作者主页链接、BV/AV 视频链接、b23.tv 短链，或轮询时的 `mid`。
- 输出：`AuthorInfo` / `PlatformWork` 列表（按 `create_time` 降序），或 `BilibiliRequestError`。
- 不负责下载与发送；发送见 `bilibili-send.SPEC.md`。

## 接口契约

网页接口（`api.bilibili.com`）对匿名请求要求 WBI 签名、`dm_img` 参数和 `buvid3` 指纹，
作者资料与投稿列表会随机返回 `-352` / `412` / `-799`。因此：

| 用途 | 主接口 | 兜底 |
| --- | --- | --- |
| 作者资料 | `app.bilibili.com/x/v2/space?vmid=` | `User.get_user_info()` |
| 视频投稿 | `app.bilibili.com/x/v2/space/archive/cursor` | `User.get_media_list()` |
| 动态列表 | 无 APP 接口 | `User.get_dynamics_new()`（需 Cookie） |

APP 请求只校验 `appkey + sign`：参数按 key 排序拼接后追加 `appsec` 取 md5，UA 使用
`BiliDroid/8.13.0 ... os/android`。`appkey` 保留两组，单次失败轮换。

## 决策

- **APP 优先、网页兜底**：APP 抛错才回退网页；APP 成功返回空列表视为“没有作品”，不回退。
- **动态按 Cookie 开关**：动态没有稳定匿名接口，未配置 `IFOLLOW_BILIBILI_COOKIE` 时不轮询动态，
  避免反复触发 `-352`；配置后才拉取。
- **buvid3 自动预热**：订阅含 B 站作者且配置 Cookie 时，启动阶段调用
  `bilibili_api.utils.network.get_buvid()`，失败只告警。
- **联合投稿昵称清洗**：APP 返回的 `author` 可能是“xxx 等联合创作”，去掉后缀再写入作者元数据。
- **分页有界**：首次订阅只取第一页；带游标时最多追 `MAX_CATCHUP_PAGES` 页，按 `aid` 翻页。
- **视频链接解析仍用 `Video.get_info()`**：订阅入口的 BV/AV 链接解析不在本次改动范围内。

## 已知限制

- APP 接口依赖公开的 `appkey`，B 站轮换后需要更新常量。
- 动态列表仍走网页接口，匿名不可用，必须配置 Cookie。
- 网页兜底仍继承 WBI/dm 风控，只作为最后手段。
