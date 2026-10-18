# AGENTS.md

本文件是本仓库的 coding agent 指南；以 `pyproject.toml`、源码和测试为准，不带入其他项目逻辑。
修改前检查工作区，保留已有用户修改，不覆盖或回退无关文件。
## 项目概况

- 项目是 Python 3.12+ 的 NoneBot 插件，使用 `uv`；源码在 `src/`，测试在 `tests/`；`models.py` 使用
  `nonebot-plugin-localstore` 持久化订阅、目标、订阅人和作品游标。
- 入口与装配：`__init__.py` 只放插件元数据并调用 `app.setup()`；`app.py` 负责装配
  （`Settings` → `SubscriptionStore` → `Poller` → 命令）与启动/关闭钩子；`settings.py` 是唯一
  读取 nonebot 配置的地方。
- 轮询子系统：`poller.py`（调度与退避）、`delivery.py`（投递与游标）、`failures.py`（失败分类）、
  `status.py`（运行时状态与状态文案）。
- 平台层：`platforms.py` 定义 `PlatformAdapter` 协议与内容模型，`adapters.py` 是注册表；
  `douyin_platform.py` / `bilibili_platform.py` 实现协议，`pagination.py` 是唯一的分页实现，
  `errors.py` 是统一错误模型。
- 媒体层：`media.py` 使用 ParseHub 解析并下载媒体，`douyin_media.py` / `bilibili_media.py`
  把 `Settings` 换算成 `MediaPolicy` 并传入平台 Cookie。
- 命令层：`commands.py` 注册命令，`formatting.py` 出文案，`reply.py` 解析被引用消息。
## Dev environment tips
所有 Python 命令使用 `uv` 或 `uvx`，不直接使用 `python`、`pip` 或 `pipx`。

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uvx pre-commit run --all-files
```

完整覆盖率测试使用 `uv run poe test`；提交前运行上述完整检查。
## Testing instructions

- 新增测试放在 `tests/test_<module>.py`；异步场景使用 `pytest-asyncio`；抖音解析修改覆盖成功、空列表、
  无效响应和 HTTP 错误。
- HTTP、parsehub 与消息发送必须 mock：HTTP 走 `tests/http_stub.py`（`httpx.MockTransport`），
  parsehub 与 alconna 发送用替身；禁止依赖真实 Cookie、抖音接口、ffmpeg 或外部服务。
- 依赖隔离优先用 `monkeypatch` 与 `tests/http_stub.py` 的 `Stub`；需要替身异步方法时才用 `AsyncMock`。
## Code style

- 遵循 Google Python Style Guide，行宽 88；公共 API 需要 Google docstring、参数和返回值类型注解；
  优先复用现有模型、辅助函数和异常类型，异步 I/O 不得阻塞事件循环。
- 配置修改同步更新 `README` 和测试；Cookie、凭证、个人信息和本地订阅数据不得进入源码或提交记录。

## Project-specific invariants

- 首次订阅只记录当前最新作品，不补发历史作品；作品成功发送后才能更新游标；发送失败必须保留游标，
  以便重试；同一时刻只跑一个轮询任务、抓取并发有上限，作者异常不得中断其他作者。
- 使用作者级退避和有界请求，不使用无界并发；发送重试与作者轮询状态只保存在运行时。

## Change workflow

- 修改轮询、解析或持久化逻辑时，先补充失败路径和边界条件测试；完成后运行受影响测试、Ruff；不得为
  通过测试放宽断言或跳过外部交互场景。

## Git Commit Messages

Follow the Conventional Commits specification.
**All commit messages (subject + body) must be written in Chinese.**

Format:
```
<type>(<scope>): <short summary>

[optional body]

[optional footer]
```

Rules:
- Use one of the standard types: feat, fix, refactor, perf, docs, test, chore, ci, build, style, revert
- Scope is optional but recommended when the change is limited to a module/component
- Subject (summary) must be in imperative mood, lowercase, no period, ≤ 72 characters
- Body should explain **why** the change was made (motivation), not just what changed
- Use the body for detailed reasoning; keep the subject concise
- Reference issues in the footer when applicable (e.g. `Refs: #123` or `Closes: #123`)
- For breaking changes, either add `!` after the type/scope or use a `BREAKING CHANGE:` footer

Output only the commit message. Do not add extra explanations or markdown fences.
