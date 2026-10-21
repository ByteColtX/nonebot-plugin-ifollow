# 发布流程

一句话：先在 `CHANGELOG.md` 写好版本段落并提交，再用 bump-my-version 提升版本并打标签，推送标签后由 CI 发布。

## 唯一事实来源

| 内容 | 位置 |
|---|---|
| 版本号 | `pyproject.toml` 的 `project.version` 与 `[tool.bumpversion] current_version` |
| 变更日志 | `CHANGELOG.md` 中 `## <version> - <YYYY-MM-DD>` 段落 |
| 标签 | `v<version>`，签名 annotated tag |
| 发布说明 | `CHANGELOG.md` 对应版本段落，由 `scripts/release_notes.py` 提取 |

三者必须一致。`Release` workflow 会在发布到 PyPI **之前**校验版本与标签一致、并确认 CHANGELOG 有对应段落；
任一不满足都会失败，不会产生半成品发布。

## 版本号规则

| 变更 | 提升 |
|---|---|
| 破坏性变更 | major |
| 新功能 | minor |
| 修复、文档、依赖、重构 | patch |

判断依据是 `git log <last-tag>..HEAD` 里的 conventional commit 类型：`feat` → minor，
`!` 或 `BREAKING CHANGE:` → major，其余 → patch。

## 步骤

### 1. 准备

```bash
git switch main
git pull --ff-only
uv sync
uv run poe test
```

要求工作区干净：bump-my-version 默认拒绝在脏工作区执行，这正好保证变更日志先提交。

### 2. 写变更日志

用 `/release-skills`（读取 `.releaserc.yml`，按提交自动分类）或手动在 `CHANGELOG.md` 顶部新增：

```markdown
## 2.1.0 - 2026-10-22

### 新功能

- 一句话描述用户能感知的变化
```

- 标题固定为 `## <version> - <YYYY-MM-DD>`，不加 `v`、不加方括号。
- 只记录用户能感知的变化；`chore` 不记录。
- 破坏性变更放在最前面的「破坏性变更」小节。
- 提交：`git commit -m "docs(changelog): 记录 2.1.0 变更"`

### 3. 提升版本

```bash
uv run poe bump minor   # patch / minor / major
```

bump-my-version 会：

- 同步更新 `pyproject.toml` 与 `uv.lock`；
- 提交 `chore(release): 将版本从 A 更新至 B`；
- 创建签名标签 `vB`。

先看候选版本：`uv run poe show-bump`。

### 4. 推送

```bash
git push origin main --follow-tags
```

### 5. CI 自动发布

`Release` workflow（`.github/workflows/release.yml`）在标签推送后：

1. 校验 `project.version` 与标签版本一致；
2. 从 `CHANGELOG.md` 提取发布说明，缺失则失败；
3. `uv build`，用 PyPI Trusted Publishing 发布（`--check-url`，重跑不会因文件已存在而失败）；
4. 创建或更新 GitHub Release（说明来自 CHANGELOG），上传 `dist/*.whl` 与 `dist/*.tar.gz`。

## 预览发布说明

```bash
uv run --no-project scripts/release_notes.py --version 2.0.7
```

## 回填历史 GitHub Release

已有标签缺少 Release 时：

```bash
/release-skills --backfill-releases
```

按 `CHANGELOG.md` 的段落创建 Release，不修改已有标签。

## 故障处理

| 现象 | 处理 |
|---|---|
| 标签推送后 CI 失败 | 修正后重跑 `Release` workflow；不要删除或移动已推送的标签 |
| 版本号与标签不一致 | 在发布前就会失败；删除本地标签，修正后重新 bump |
| 需要撤回已发布版本 | PyPI 只能 yank，不能删除后复用版本号；发新的 patch 版本 |
| 发布说明有误 | 改 `CHANGELOG.md` 对应段落，并编辑 GitHub Release；标签保持不动 |
