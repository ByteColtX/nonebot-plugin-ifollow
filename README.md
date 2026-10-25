<div align="center">
    <a href="https://v2.nonebot.dev/store">
    <img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-template/refs/heads/resource/.docs/NoneBotPlugin.svg" width="310" alt="logo"></a>

## ✨ nonebot-plugin-ifollow ✨
[![LICENSE](https://img.shields.io/github/license/ByteColtX/nonebot-plugin-ifollow.svg)](./LICENSE)
[![pypi](https://img.shields.io/pypi/v/nonebot-plugin-ifollow.svg)](https://pypi.org/project/nonebot-plugin-ifollow/)
[![python](https://img.shields.io/badge/python-3.12|3.13|3.14-blue.svg)](https://www.python.org)
[![uv](https://img.shields.io/badge/package%20manager-uv-black?style=flat-square&logo=uv)](https://github.com/astral-sh/uv)
<br/>
[![ruff](https://img.shields.io/badge/code%20style-ruff-black?style=flat-square&logo=ruff)](https://github.com/astral-sh/ruff)
[![pre-commit](https://results.pre-commit.ci/badge/github/ByteColtX/nonebot-plugin-ifollow/main.svg)](https://results.pre-commit.ci/latest/github/ByteColtX/nonebot-plugin-ifollow/main)

</div>

## 📖 介绍

订阅抖音和 B 站作者，有新作品或动态时推到群聊 / 私聊。插件只负责发现更新，媒体交给
[ParseHub](https://github.com/z-mio/ParseHub) 解析、下载并发送。

- **多平台**：抖音（主页 / 短链 / `sec_user_id`）、B 站（主页 / BV / AV / b23.tv）
- **按会话独立**：每个群 / 私聊各自记录推送游标，同一个作者可以订阅到多个会话
- **不补发历史**：首次订阅只记录当前最新一条，之后只推新内容
- **失败可控**：作者级退避、作品级重试 3 次、目标群不可用只跳过该会话
- **下载前控体积**：视频类媒体先探远端体积，超过上限直接不下载
- **零配置可跑**：抖音匿名即可轮询；B 站不配 Cookie 时只推视频（固定 720p）

## 💿 安装

使用 nb-cli 安装：

```bash
nb plugin install nonebot-plugin-ifollow --upgrade
```

使用 uv 安装：

```bash
uv add nonebot-plugin-ifollow
```

安装仓库 main 分支：

```bash
uv add git+https://github.com/ByteColtX/nonebot-plugin-ifollow@main
```

用 uv 安装后还需要在 nonebot2 项目根目录的 `pyproject.toml` 里注册插件（nb-cli 会自动完成这一步）：

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_ifollow"]
```

## ⚙️ 配置

在 nonebot2 项目的 `.env` 文件中添加下表配置，全部可选：

| 配置项 | 必填 | 默认值 | 说明 |
| :--- | :---: | :--- | :--- |
| `IFOLLOW_SUB_INTERVAL` | 否 | `1800` | 每个作者的轮询周期（秒），最小 30 |
| `IFOLLOW_SUB_MAX_ITEMS` | 否 | `5` | 每作者每 feed 每轮最多补发的作品数，范围 1-50 |
| `IFOLLOW_MEDIA_MAX_MB` | 否 | `100` | 单个媒体体积上限（MB），视频类会在下载前先探测 |
| `IFOLLOW_USE_BASE64` | 否 | `false` | 媒体编码成 base64 发送，仅协议端与插件不同机时需要 |
| `IFOLLOW_DOUYIN_COOKIE` | 否 | 无 | 抖音网页 Cookie，匿名即可轮询，通常无需填写 |
| `IFOLLOW_BILIBILI_COOKIE` | 否 | 无 | B 站 Cookie（SESSDATA）；配置后才会轮询动态 |

- **周期**：每个作者按自己的节奏到期，订阅 30 个作者也不会互相拖慢；扫描间隔取周期的四分之一
  （15 秒 ~ 5 分钟），抓取并发 4。默认 30 分钟按「多数作者月更」定，34 个作者约 1600 次请求/天。
- **补发上限**：不是页大小。停机很久后回来最多补发最新 5 条，更早的跳过（日志里有 warning）。
- **媒体传输**：默认把本地路径（`file://`）交给协议端，插件不读字节也不编码；协议端在另一台机器、
  读不到同一路径时才开 `IFOLLOW_USE_BASE64`。单个媒体超过 `IFOLLOW_MEDIA_MAX_MB` 时跳过该媒体，
  并在文案里注明「另有 N 个媒体超过 XMB 未发送」。
- **旧配置**：`IFOLLOW_SUBSCRIBE_INTERVAL` / `IFOLLOW_SUBSCRIBE_COUNT` 已废弃且不再读取，请改成新名字。
- **数据格式**：订阅数据为 v3，不兼容旧版本；升级需清空插件数据目录里的 `subscriptions.json` 并重新订阅。

### 关于 Cookie

抖音接口要求 `a_bogus` 签名 + 自动注册的 `ttwid`，**匿名即可轮询**，`IFOLLOW_DOUYIN_COOKIE` 只是加强项
（其中的 `ttwid` 会被自动注册值覆盖）。

B 站作者资料与视频投稿走 APP 接口，匿名可用；**动态没有稳定的匿名接口**，只有配置了
`IFOLLOW_BILIBILI_COOKIE` 才会轮询动态。获取方式：浏览器登录后按 F12 → Network → 复制请求头里的整段
`Cookie`。Cookie 等同于账号控制权，建议用小号，不要提交到仓库。

## 🎉 使用

### 指令表

| 指令 | 权限 | 需要@ | 范围 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| `/爱播帮助` | 群员 | 否 | 群聊、私聊 | 查看帮助 |
| `/爱播订阅 <作者主页或作品链接>` | 群员 | 否 | 群聊、私聊 | 订阅一个抖音或 B 站作者 |
| `/爱播列表` | 群员 | 否 | 群聊、私聊 | 查看当前会话的订阅 |
| `/爱播退订 <序号、作者 ID、昵称或链接>` | 群员 | 否 | 群聊、私聊 | 退订一个作者 |
| `/爱播提醒 [开\|关]` | 群主 / 管理员 / 超级用户；私聊订阅人 | 否 | 群聊、私聊 | 开关当前会话新作品通知里的 @ 提醒 |
| `/爱播状态` | 超级用户 | 否 | 群聊、私聊 | 查看轮询健康度与各作者状态 |

### 订阅作者

```text
/爱播订阅 https://www.douyin.com/user/MS4wLjABAAAA...
/爱播订阅 https://v.douyin.com/作者主页短链/
/爱播订阅 MS4wLjABAAAA...
/爱播订阅 https://space.bilibili.com/123456
/爱播订阅 https://www.bilibili.com/video/BV1xxxxxxxxx
```

也可以引用含链接的消息后发送 `/爱播订阅`（需要适配器支持 `get_msg`）。

首次订阅只记录当前最新作品或动态，不补发历史；之后发现新内容时，插件把作品链接交给 ParseHub
解析、下载并发送到订阅会话。

### 管理订阅

退订优先在本地匹配：序号、作者 ID、抖音号 / B 站 UID、主页链接里的 ID、唯一昵称都不联网，
作者注销或接口被风控时也能退订；只有短链和作品链接才请求平台。昵称重名请改用序号。

`/爱播提醒 关` 会关闭当前会话所有订阅的 @ 提醒，新作品仍照常发送；之后新订阅的作者也不会 @。
`/爱播提醒 开` 恢复。默认开启。群里需要群主、群管理员或机器人超级用户设置；私聊由订阅人自己设置。

### 失败处理

- 作者不存在或已注销 —— 停用该作者（重启后重新尝试），`/爱播状态` 里标「已停用」；
- 作者接口暂时失败（网络、风控、限流）—— 该作者指数退避，30 秒起、上限 5 分钟；
- 作品已删除、不可见、平台不支持 —— 立即放弃该作品并推进游标；
- 网络、限流、下载失败 —— 下个周期重试，连续 3 次后放弃并推进游标；
- 机器人被踢出群、私聊非好友 —— 首次轮询时按 `get_group_list` / `get_friend_list` 扫描一次，
  跳过不可达目标；重新入群或加好友需重启后恢复；

订阅或退订失败时只回一句人话原因、一条建议和一个错误码（如 `E-DY-RISK`），不会把异常堆栈发到群里；
把错误码贴出来就能在日志里定位到完整堆栈。

## 🛠️ 开发

```bash
uv sync
uv run pytest -q                # 只跑测试
uv run poe test                 # 覆盖率门禁 90%（CI 同款）
uv run ruff check . && uv run ruff format --check .
uv run basedpyright
```

发布流程见 [RELEASING.md](RELEASING.md)：改动走 PR 合入，版本提升单独提 PR，
在 GitHub 创建带自动生成说明的 Release 后由 CI 构建、发布 PyPI 并上传附件。

### 代码结构

```
src/nonebot_plugin_ifollow/
├── __init__.py / app.py / settings.py    # 元数据、装配、唯一读配置的地方
├── commands.py / formatting.py / reply.py / style.py
├── platforms.py / adapters.py / pagination.py / errors.py
├── douyin_platform.py / douyin_abogus.py
├── bilibili_platform.py
├── media.py / douyin_media.py / bilibili_media.py
├── models.py                             # 订阅存储（v3，批量写）
└── poller.py / delivery.py / failures.py / status.py
```

模块设计文档（轮询子系统、存储、媒体、抖音 / B 站拉取与发送）已归档到 `archive/specs`
分支，不再随源码发布；`AGENTS.md` 是给 coding agent 的约定。

## 📄 许可

[MIT](LICENSE)

## 🙏 致谢

- [z-mio/ParseHub](https://github.com/z-mio/ParseHub)：媒体解析与下载能力全部来自这个项目
- [fllesser/nonebot-plugin-template](https://github.com/fllesser/nonebot-plugin-template)：插件标识与 README 结构参考
- [Johnserf-Seed/f2](https://github.com/Johnserf-Seed/f2)：抖音 `a_bogus` 签名算法参考（Apache-2.0）
- [Nemo2011/bilibili-api](https://github.com/Nemo2011/bilibili-api)：B 站网页接口兜底与 `buvid3` 预热
