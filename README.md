<div align="center">
    <a href="https://v2.nonebot.dev/store">
    <img src="https://raw.githubusercontent.com/nonebot/nonebot2/master/.docs/NoneBotPlugin.svg" width="310" alt="logo"></a>

## ✨ nonebot-plugin-douyin-subscribe ✨
[![LICENSE](https://img.shields.io/github/license/ByteColtX/nonebot-plugin-douyin-subscribe.svg)](./LICENSE)
[![pypi](https://img.shields.io/pypi/v/nonebot-plugin-douyin-subscribe.svg)](https://pypi.python.org/pypi/nonebot-plugin-douyin-subscribe)
[![python](https://img.shields.io/badge/python-3.10|3.11|3.12|3.13|3.14-blue.svg)](https://www.python.org)
[![uv](https://img.shields.io/badge/package%20manager-uv-black?style=flat-square&logo=uv)](https://github.com/astral-sh/uv)
<br/>
[![ruff](https://img.shields.io/badge/code%20style-ruff-black?style=flat-square&logo=ruff)](https://github.com/astral-sh/ruff)
[![pre-commit](https://results.pre-commit.ci/badge/github/ByteColtX/nonebot-plugin-douyin-subscribe/master.svg)](https://results.pre-commit.ci/latest/github/ByteColtX/nonebot-plugin-douyin-subscribe/master)

</div>

## 📖 介绍

订阅抖音作者的新作品，并将作品分享链接交给
[nonebot-plugin-parser](https://github.com/fllesser/nonebot-plugin-parser) 下载和发送。
支持抖音作者主页链接、作者主页短链和 `sec_user_id`，每个会话独立记录已发送作品。

## 💿 安装

<details open>
<summary>使用 nb-cli 安装</summary>
在 nonebot2 项目的根目录下打开命令行, 输入以下指令即可安装

    nb plugin install nonebot-plugin-douyin-subscribe --upgrade
使用 **pypi** 源安装

    nb plugin install nonebot-plugin-douyin-subscribe --upgrade -i "https://pypi.org/simple"
使用**清华源**安装

    nb plugin install nonebot-plugin-douyin-subscribe --upgrade -i "https://pypi.tuna.tsinghua.edu.cn/simple"


</details>

<details>
<summary>使用包管理器安装</summary>
在 nonebot2 项目的插件目录下, 打开命令行, 根据你使用的包管理器, 输入相应的安装命令

<details open>
<summary>uv</summary>

    uv add nonebot-plugin-douyin-subscribe
安装仓库 master 分支

    uv add git+https://github.com/ByteColtX/nonebot-plugin-douyin-subscribe@master
</details>

<details>
<summary>pdm</summary>

    pdm add nonebot-plugin-douyin-subscribe
安装仓库 master 分支

    pdm add git+https://github.com/ByteColtX/nonebot-plugin-douyin-subscribe@master
</details>
<details>
<summary>poetry</summary>

    poetry add nonebot-plugin-douyin-subscribe
安装仓库 master 分支

    poetry add git+https://github.com/ByteColtX/nonebot-plugin-douyin-subscribe@master
</details>

打开 nonebot2 项目根目录下的 `pyproject.toml` 文件, 在 `[tool.nonebot]` 部分追加写入

    plugins = ["nonebot_plugin_douyin_subscribe"]

</details>

<details>
<summary>使用 nbr 安装(使用 uv 管理依赖可用)</summary>

[nbr](https://github.com/fllesser/nbr) 是一个基于 uv 的 nb-cli，可以方便地管理 nonebot2

    nbr plugin install nonebot-plugin-douyin-subscribe
使用 **pypi** 源安装

    nbr plugin install nonebot-plugin-douyin-subscribe -i "https://pypi.org/simple"
使用**清华源**安装

    nbr plugin install nonebot-plugin-douyin-subscribe -i "https://pypi.tuna.tsinghua.edu.cn/simple"

</details>


## ⚙️ 配置

在 nonebot2 项目的`.env`文件中添加下表中的可选配置。抖音网页接口可能随时调整，接口受限时可配置有效 Cookie。

| 配置项 | 必填 | 默认值 | 说明 |
| :--- | :---: | :---: | :--- |
| `DOUYIN_SUBSCRIBE_INTERVAL` | 否 | `300` | 轮询间隔，单位为秒，最小 30 秒 |
| `DOUYIN_SUBSCRIBE_COUNT` | 否 | `20` | 每次拉取的最新作品数，范围 1-50 |
| `DOUYIN_COOKIE` | 否 | 无 | 抖音网页 Cookie，接口返回空列表或受限时填写 |

示例：

```dotenv
DOUYIN_SUBSCRIBE_INTERVAL=300
DOUYIN_SUBSCRIBE_COUNT=20
DOUYIN_COOKIE="你的抖音网页 Cookie"
```

插件依赖 `nonebot-plugin-parser` 发送视频，parser 的视频解析还需要安装 ffmpeg。订阅数据由
`nonebot-plugin-localstore` 持久化到插件数据目录，不要把 Cookie 或订阅数据提交到代码仓库。

## 🎉 使用

### 指令表

| 指令 | 权限 | 需要@ | 范围 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| `抖音订阅 <AUTHOR>` | 群员 | 否 | 群聊、私聊 | 订阅一个抖音作者 |
| `抖音订阅列表` | 群员 | 否 | 群聊、私聊 | 查看当前会话的订阅 |
| `抖音取消订阅 <AUTHOR>` | 群员 | 否 | 群聊、私聊 | 取消一个抖音作者订阅 |

### 订阅作者

```text
抖音订阅 https://www.douyin.com/user/MS4wLjABAAAA...
抖音订阅 https://v.douyin.com/作者主页短链/
抖音订阅 MS4wLjABAAAA...
```

也可以引用包含抖音作品分享链接的消息后发送 `抖音订阅`。插件会读取被引用的原消息，
从作品页解析作者主页并完成订阅；该功能需要当前适配器支持 `get_msg` 接口。

```text
群友A：4.38 复制打开抖音，看看【小葵日记的作品】喵喵喵 https://v.douyin.com/0l0Sigjl5jM/
群友A（引用上一条消息）：抖音订阅
```

首次订阅只记录当前最新作品，不会补发历史作品。之后轮询发现新作品时，插件会将作品链接交给
`nonebot-plugin-parser` 下载并发送到订阅会话。

### 管理订阅

```text
抖音订阅列表
抖音取消订阅 https://www.douyin.com/user/MS4wLjABAAAA...
```

订阅状态按作者和目标会话保存；同一个作者可以订阅到多个群组，各群组分别记录推送游标。

### 🎨 效果图

暂无示例图。
