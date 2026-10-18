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
| `IFOLLOW_SUBSCRIBE_INTERVAL` | 否 | `300` | 所有平台共享的轮询间隔，单位为秒，最小 30 秒 |
| `IFOLLOW_SUBSCRIBE_COUNT` | 否 | `20` | 每个平台每次拉取的最新内容数，范围 1-50 |
| `IFOLLOW_MEDIA_MAX_MB` | 否 | `90` | 单个媒体文件的体积上限（抖音、B 站），超出则跳过该媒体 |
| `IFOLLOW_DOUYIN_COOKIE` | 否 | 无 | 抖音网页 Cookie，通常无需填写，详见下方说明 |
| `IFOLLOW_BILIBILI_COOKIE` | 否 | 无 | B 站 Cookie，动态解析和接口受限时填写；视频固定 720p |

示例：

```dotenv
IFOLLOW_SUBSCRIBE_INTERVAL=300
IFOLLOW_SUBSCRIBE_COUNT=20
IFOLLOW_MEDIA_MAX_MB=90
IFOLLOW_DOUYIN_COOKIE="你的抖音网页 Cookie"
IFOLLOW_BILIBILI_COOKIE="你的 B 站 Cookie"
```

插件使用 `parsehub` 解析并下载媒体，B 站视频固定按 720p 取流。订阅数据由
`nonebot-plugin-localstore` 持久化到插件数据目录，不要把 Cookie 或订阅数据提交到代码仓库。

### 关于抖音 Cookie

抖音作品接口要求每次请求都带 `a_bogus` 签名、`ttwid` Cookie 与 `x-tt-argus` 请求头，插件会
自动注册 ttwid 并在被风控时重新注册，因此 **匿名即可正常轮询，无需手工配置 Cookie**。

`IFOLLOW_DOUYIN_COOKIE` 只是加强项：当接口开始校验登录态时可填写浏览器 Cookie。注意其中
的 `ttwid` 会被插件自动注册的值覆盖，其余字段（如 `msToken`、`UIFID`、`sessionid`）会原样透传。

获取方式：桌面 Chrome 登录 <https://www.douyin.com>，按 `F12` 打开开发者工具，切到 **Network**，
过滤 `aweme/v1/web/aweme/post`，点开任意请求，右键 **Copy → Copy as cURL**，把其中
`-H 'Cookie: ...'` 的整段值填入配置即可。`document.cookie` 拿不到 HttpOnly 的 `ttwid`/`UIFID`，
不要用它。

### 关于 B 站 Cookie 与画质

B 站发送由 parsehub 完成，视频接口固定按 720p（`qn=64`）取流，配置 Cookie 不会提升画质。
`IFOLLOW_BILIBILI_COOKIE` 主要用于动态解析和接口受限时的兜底：动态在匿名态容易触发 `-352` 风控。
在浏览器登录 B 站，按 `F12` → **Application → Cookies → https://www.bilibili.com** 复制
`SESSDATA` 即可。Cookie 等同于账号控制权，建议使用小号，不要提交到代码仓库。

动态链接会以 `t.bilibili.com/{id}` 的形式发送，parsehub 只识别这种动态链接。

## 🎉 使用

### 指令表

| 指令 | 权限 | 需要@ | 范围 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| `爱播帮助` | 群员 | 否 | 群聊、私聊 | 查看帮助 |
| `爱播订阅 <作者主页或作品链接>` | 群员 | 否 | 群聊、私聊 | 订阅一个抖音或 B 站作者 |
| `爱播订阅列表` | 群员 | 否 | 群聊、私聊 | 查看当前会话的订阅 |
| `爱播取消订阅 <序号或作者主页/作品链接>` | 群员 | 否 | 群聊、私聊 | 取消一个作者订阅 |

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
爱播取消订阅 https://www.douyin.com/user/MS4wLjABAAAA...
```

订阅状态按平台、作者和目标会话保存；同一个作者可以订阅到多个会话，各会话分别记录推送游标。

### 🎨 效果图

暂无示例图。
