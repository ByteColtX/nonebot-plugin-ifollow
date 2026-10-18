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

订阅抖音和 B 站作者的新作品及动态，并将作品分享链接交给
[parsehub](https://github.com/z-mio/ParseHub) 解析、下载和发送。
支持抖音作者主页链接、作者主页短链、`sec_user_id`，以及 B 站作者主页和作品链接，
每个会话独立记录已发送作品。

## 💿 安装

<details open>
<summary>使用 nb-cli 安装</summary>
在 nonebot2 项目的根目录下打开命令行, 输入以下指令即可安装

    nb plugin install nonebot-plugin-ifollow --upgrade
使用 **pypi** 源安装

    nb plugin install nonebot-plugin-ifollow --upgrade -i "https://pypi.org/simple"
使用**清华源**安装

    nb plugin install nonebot-plugin-ifollow --upgrade -i "https://pypi.tuna.tsinghua.edu.cn/simple"


</details>

<details>
<summary>使用包管理器安装</summary>
在 nonebot2 项目的插件目录下, 打开命令行, 根据你使用的包管理器, 输入相应的安装命令

<details open>
<summary>uv</summary>

    uv add nonebot-plugin-ifollow
安装仓库 main 分支

    uv add git+https://github.com/ByteColtX/nonebot-plugin-ifollow@main
</details>

<details>
<summary>pdm</summary>

    pdm add nonebot-plugin-ifollow
安装仓库 main 分支

    pdm add git+https://github.com/ByteColtX/nonebot-plugin-ifollow@main
</details>
<details>
<summary>poetry</summary>

    poetry add nonebot-plugin-ifollow
安装仓库 main 分支

    poetry add git+https://github.com/ByteColtX/nonebot-plugin-ifollow@main
</details>

打开 nonebot2 项目根目录下的 `pyproject.toml` 文件, 在 `[tool.nonebot]` 部分追加写入

    plugins = ["nonebot_plugin_ifollow"]

</details>

<details>
<summary>使用 nbr 安装(使用 uv 管理依赖可用)</summary>

[nbr](https://github.com/fllesser/nbr) 是一个基于 uv 的 nb-cli，可以方便地管理 nonebot2

    nbr plugin install nonebot-plugin-ifollow
使用 **pypi** 源安装

    nbr plugin install nonebot-plugin-ifollow -i "https://pypi.org/simple"
使用**清华源**安装

    nbr plugin install nonebot-plugin-ifollow -i "https://pypi.tuna.tsinghua.edu.cn/simple"

</details>


## ⚙️ 配置

在 nonebot2 项目的`.env`文件中添加下表中的可选配置。平台网页接口可能随时调整，接口受限时可配置有效 Cookie。

| 配置项 | 必填 | 默认值 | 说明 |
| :--- | :---: | :---: | :--- |
| `IFOLLOW_SUB_INTERVAL` | 否 | `1800` | 每个作者的轮询周期，单位为秒，最小 30 秒 |
| `IFOLLOW_SUB_MAX_ITEMS` | 否 | `5` | 每个作者每个 feed 一次轮询最多补发的作品数，范围 1-50 |
| `IFOLLOW_MEDIA_MAX_MB` | 否 | `100` | 单个媒体文件的体积上限（抖音、B 站），超出则跳过该媒体；视频类会在下载前先探测 |
| `IFOLLOW_USE_BASE64` | 否 | `false` | 是否把媒体编码成 base64 发送，仅协议端与插件不在同一台机器时需要开启 |
| `IFOLLOW_DOUYIN_COOKIE` | 否 | 无 | 抖音网页 Cookie，通常无需填写，详见下方说明 |
| `IFOLLOW_BILIBILI_COOKIE` | 否 | 无 | B 站 Cookie（SESSDATA）；配置后才会轮询动态，视频固定 720p |

示例：

```dotenv
IFOLLOW_SUB_INTERVAL=1800
IFOLLOW_SUB_MAX_ITEMS=5
IFOLLOW_MEDIA_MAX_MB=100
IFOLLOW_DOUYIN_COOKIE="你的抖音网页 Cookie"
IFOLLOW_BILIBILI_COOKIE="你的 B 站 Cookie"
```

> 旧版配置项 `IFOLLOW_SUBSCRIBE_INTERVAL` / `IFOLLOW_SUBSCRIBE_COUNT` 已废弃且**不再读取**，
> 请改成 `IFOLLOW_SUB_INTERVAL` / `IFOLLOW_SUB_MAX_ITEMS`；不修改会直接回退到默认值。

`IFOLLOW_SUB_INTERVAL` 是**每个作者**的轮询周期：每个作者按自己的节奏到期，
所以订阅 30 个作者也不会让实际间隔变长。扫描间隔跟着周期走（周期的四分之一，
夹在 15 秒到 5 分钟之间），同时最多抓取 4 个作者。

默认 30 分钟是按「多数作者月更、插件不要求实时」定的：34 个作者约 2000 次请求/天，
新作品最晚晚半小时推送。想更省请求可以调到 `3600` 或更大，正确性不受影响，只是延迟和
失败重试间隔一起变长。

`IFOLLOW_SUB_MAX_ITEMS` 是**补发上限**，不是页大小：一轮轮询最多推送多少条新作品。
插件不指定分页条数，页大小用平台默认值；停机很久后回来时，最多补发最新 5 条，
更早的作品会被跳过（日志里有 warning 说明跳过了几条）。日常一轮通常只有 0~2 条新作品，
默认值不会影响正常推送。

插件使用 `parsehub` 解析并下载媒体，B 站视频固定按 720p 取流。订阅数据由
`nonebot-plugin-localstore` 持久化到插件数据目录，不要把 Cookie 或订阅数据提交到代码仓库。

> 订阅数据格式为 **v3**，不兼容旧版本。从旧版本升级时需要清空插件数据目录里的
> `subscriptions.json` 并重新订阅。

### 关于媒体传输方式

默认把下载好的媒体以本地路径（`file://`）交给协议端，插件不读字节也不编码，大视频不会拖慢
消息处理。**只有协议端与插件不在同一台机器、读不到同一个路径时**（比如协议端跑在另一台
服务器或另一个容器里，报「路径不存在」），才需要开启：

```dotenv
IFOLLOW_USE_BASE64=true
```

开启后媒体会编码成 base64 发送，单个文件的体积上限由 `IFOLLOW_MEDIA_MAX_MB` 控制。

### 关于抖音 Cookie

抖音作品接口要求每次请求都带 `a_bogus` 签名、`ttwid` Cookie 与 `x-tt-argus` 请求头，插件会
自动注册 ttwid 并在被风控时重新注册，因此 **匿名即可正常轮询，无需手工配置 Cookie**。

`IFOLLOW_DOUYIN_COOKIE` 只是加强项：当接口开始校验登录态时可填写浏览器 Cookie。注意其中
的 `ttwid` 会被插件自动注册的值覆盖，其余字段（如 `msToken`、`UIFID`、`sessionid`）会原样透传。

获取方式：桌面 Chrome 登录 <https://www.douyin.com>，按 `F12` 打开开发者工具，切到 **Network**，
过滤 `aweme/v1/web/aweme/post`，点开任意请求，右键 **Copy → Copy as cURL**，把其中
`-H 'Cookie: ...'` 的整段值填入配置即可。`document.cookie` 拿不到 HttpOnly 的 `ttwid`/`UIFID`，
不要用它。

### 关于 B 站接口、Cookie 与画质

B 站作者资料和视频投稿走 APP 端接口（只校验 `appkey + sign`），匿名即可稳定获取，不需要 Cookie；
APP 接口失败时会回退网页接口。动态没有对应的 APP 接口，匿名态容易触发 `-352` 风控，因此
**只有配置了 `IFOLLOW_BILIBILI_COOKIE` 才会轮询动态**；未配置时只推送视频。

视频发送由 parsehub 完成，接口固定按 720p（`qn=64`）取流，配置 Cookie 不会提升画质。
动态链接会以 `t.bilibili.com/{id}` 的形式发送，parsehub 只识别这种动态链接。

`buvid3` / `buvid4` 无需手动填写：配置 Cookie 后插件会在启动时自动获取，网页兜底请求也会自动补充。

需要动态推送时，按下面任一方式获取 Cookie：

1. 匿名指纹（不登录，只提升网页接口成功率）：请求 `https://api.bilibili.com/x/frontend/finger/spi`，
   把返回的 `b_3`、`b_4` 拼成 `buvid3=...; buvid4=...`。
2. 登录 Cookie（动态最稳）：浏览器登录 `https://www.bilibili.com`，按 F12 打开开发者工具，
   在“应用/存储 → Cookie → https://www.bilibili.com”中复制 `SESSDATA`；也可以在“网络”面板
   任意请求的请求头中复制整段 `Cookie`。

```dotenv
IFOLLOW_BILIBILI_COOKIE="SESSDATA=...; buvid3=...; buvid4=..."
```

Cookie 等同于账号控制权，建议使用小号，不要提交到代码仓库；退出登录或修改密码后 Cookie 会失效。

## 🎉 使用

### 指令表

| 指令 | 权限 | 需要@ | 范围 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| `爱播帮助` | 群员 | 否 | 群聊、私聊 | 查看帮助 |
| `爱播订阅 <作者主页或作品链接>` | 群员 | 否 | 群聊、私聊 | 订阅一个抖音或 B 站作者 |
| `爱播订阅列表` | 群员 | 否 | 群聊、私聊 | 查看当前会话的订阅 |
| `爱播取消订阅 <序号、作者 ID、昵称或主页/作品链接>` | 群员 | 否 | 群聊、私聊 | 取消一个作者订阅 |
| `爱播状态` | 超级用户 | 否 | 群聊、私聊 | 查看轮询健康度与各作者状态 |

### 订阅作者

```text
爱播订阅 https://www.douyin.com/user/MS4wLjABAAAA...
爱播订阅 https://v.douyin.com/作者主页短链/
爱播订阅 MS4wLjABAAAA...
爱播订阅 https://space.bilibili.com/123456
爱播订阅 https://www.bilibili.com/video/BV1xxxxxxxxx
```

也可以引用包含抖音或 B 站作品分享链接的消息后发送 `爱播订阅`。插件会读取被引用的原消息，
从作品页解析作者主页并完成订阅；该功能需要当前适配器支持 `get_msg` 接口。

```text
群友A：4.38 复制打开抖音，看看【小葵日记的作品】喵喵喵 https://v.douyin.com/0l0Sigjl5jM/
群友A（引用上一条消息）：爱播订阅
```

首次订阅只记录当前最新作品或动态，不会补发历史内容。之后轮询发现新内容时，插件会将作品链接交给
`parsehub` 解析、下载并发送到订阅会话。

### 管理订阅

```text
爱播订阅列表
爱播取消订阅 1
爱播取消订阅 MS4wLjABAAAA...
爱播取消订阅 呆呆丸
爱播取消订阅 https://space.bilibili.com/123456
```

取消时优先在本地匹配：序号、作者 ID、抖音号 / B 站 UID、主页链接里的 ID、唯一的昵称都不联网，
作者注销或接口被风控时也能取消；只有短链和作品链接（本地无法得知作者）才需要请求平台。
昵称重名时请改用序号。纯数字输入始终按序号解释。

订阅状态按平台、作者和目标会话保存；同一个作者可以订阅到多个会话，各会话分别记录推送游标。

### 轮询与失败处理

插件启动后立即开始首轮轮询，之后每个作者按自己的周期到期。一个作品发送失败时：

- 作品已删除、不可见、平台不支持 —— 立即放弃该作品并推进游标，记一条 warning；
- 网络、限流、下载失败 —— 下个周期重试，连续 3 次仍失败则放弃并推进游标；
- 机器人被踢出群、群不存在 —— 只跳过该会话，不影响同一作品的其他会话。

视频类媒体（视频、动图、实况照片）会在下载前先探测远端体积，超过 `IFOLLOW_MEDIA_MAX_MB`
的直接不下载；探测失败或服务端不返回长度时，退回下载后的体积检查。

放弃意味着这个作品不会重发，后续新作品不受影响。需要看某个作者是不是一直在失败时，
超级用户发送 `爱播状态` 即可看到每个作者的最近成功时间、连续失败次数和下次重试时间。

### 🎨 效果图

暂无示例图。
