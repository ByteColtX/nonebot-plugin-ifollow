# AGENTS.md

本文件为在本仓库中工作的 coding agent 提供最少且必要的项目约定。以
`pyproject.toml`、源码和测试的实际行为为准；不要把其他项目的业务逻辑带入本项目。

## 项目概况

- 本项目是 Python 3.10+ 的 NoneBot 插件，使用 `uv` 管理环境和依赖。
- 包源码位于 `src/nonebot_plugin_douyin_subscribe/`，测试位于 `tests/`；`pyproject.toml`
  是插件、pytest、Ruff 和 CI 配置的来源。
- `plugin.py` 是 NoneBot 入口，负责订阅命令、定时轮询和向目标会话发送消息。
- `douyin.py` 负责抖音作者主页解析和最新作品接口；作品分享链接必须交给
  `nonebot-plugin-parser` 处理，不要在本项目重复实现视频下载。
- `models.py` 使用 `nonebot-plugin-localstore` 持久化订阅作者、目标会话和作品游标。

## 开发与验证

所有 Python 命令均使用 `uv` 执行，不直接使用 `python`、`pip` 或 `pipx`。

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pre-commit run --all-files
```

完整覆盖率测试使用项目已配置的任务：

```bash
uv run poe test
```

新增测试放在 `tests/`，命名为 `test_<module>.py`。抖音 HTTP 请求、parser 下载和消息发送
必须使用 mock，测试不得依赖真实 Cookie、抖音接口、ffmpeg 或外部服务。

## 代码约定

- 遵循 Google Python Style Guide，行宽以 Ruff 配置的 88 个字符为准。
- 新增或修改的公共函数、方法和类必须有 Google 风格 docstring；参数和返回值必须有类型注解。
- 抖音接口返回格式不稳定，修改解析逻辑时必须覆盖成功响应、空列表、无效响应和 HTTP 错误。
- 轮询任务必须保持幂等：成功发送后才能更新游标；发送失败时应保留游标，以便下次重试。
- 不要把抖音 Cookie、NoneBot 配置凭证或本地订阅数据写入源码、测试或提交记录。
- 不要把订阅数据改为仓库内固定文件；持久化应继续使用 `nonebot-plugin-localstore`。

## Git 提交

提交信息使用 Conventional Commits，标题和 body 均使用中文，例如：
`fix(douyin): 修复短链解析失败后的重试`。常用类型为 `feat`、`fix`、`docs`、`test`、
`refactor`、`perf` 和 `chore`。提交前确保测试、Ruff、BasedPyright 和 pre-commit 检查通过；
除非用户明确要求，不自动创建提交。
