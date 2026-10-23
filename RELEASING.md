# 发布流程

一句话：改动走 PR 合入 `main`，版本提升单独提一个 PR，然后在 GitHub 创建带自动生成说明的 Release；
CI 从 Release 事件构建、发布 PyPI，并把产物挂回该 Release。

## 唯一事实来源

| 内容 | 位置 |
|---|---|
| 版本号 | `pyproject.toml` 的 `project.version`（`uv version` 会同步 `uv.lock`） |
| 标签 | GitHub Release 创建的 `v<version>` |
| 发布说明 | GitHub 根据已合并 PR 自动生成，不再维护 `CHANGELOG.md` |

`Release` workflow 会在发布到 PyPI **之前**校验 `project.version` 与 Release 标签一致；不一致直接失败。

## 版本号规则

| 变更 | 提升 |
|---|---|
| 破坏性变更 | major |
| 新功能 | minor |
| 修复、文档、依赖、重构 | patch |

## 步骤

### 1. 准备

```bash
git switch main
git pull --ff-only
uv sync
uv run poe test
```

要求 CI 通过、工作区干净。

### 2. 版本提升 PR

```bash
git switch -c chore/bump-2.3.0
uv version --bump minor      # patch / minor / major；会同时更新 uv.lock
git add pyproject.toml uv.lock
git commit -m "chore(version): 升级版本号至 2.3.0"
git push -u origin chore/bump-2.3.0
```

用 `gh pr create` 或在 GitHub 上开 PR，等 CI 通过后合并到 `main`。版本提升必须单独一个 PR，
不要和功能改动混在一起。

### 3. 创建 GitHub Release

在 GitHub 的 **Releases → Draft a new release**：

1. Tag 填 `v2.3.0`，Target 选 `main`；
2. 标题填 `v2.3.0`；
3. 点 **Generate release notes**，检查生成的内容；
4. 发布。

或者用 CLI：

```bash
gh release create v2.3.0 --target main --title v2.3.0 --generate-notes
```

发布说明来自**已合并的 PR**，直接 push 到 `main` 的 commit 不会出现在里面。所有用户可感知的改动
都必须走 PR，PR 标题按 Conventional Commits 写。

> 需要签名 tag 时，先本地 `git tag -s v2.3.0` 并推送，再用
> `gh release create v2.3.0 --verify-tag --generate-notes` 创建 Release；其余步骤不变。

### 4. CI 自动发布

Release 发布后，`Release` workflow（`.github/workflows/release.yml`）会：

1. checkout Release 对应的标签；
2. 校验 `project.version` 与标签版本一致；
3. 跑一遍 `uv run poe test`；
4. `uv build`，用 PyPI Trusted Publishing 发布（`--check-url`，重跑不会因文件已存在而失败）；
5. 把 `dist/*.whl` 与 `dist/*.tar.gz` 上传到该 Release（`--clobber`，可重复执行）。

如果仓库配置了 `release` environment 的保护规则，需要在 Actions 页面批准一次。

## 故障处理

| 现象 | 处理 |
|---|---|
| 版本号与标签不一致 | workflow 在发布前失败；删除该 Release 与标签，修正版本 PR 后重新创建 |
| PyPI 发布前失败（测试、版本） | 修正后重跑 workflow；标签不要移动 |
| PyPI 发布后失败（附件上传） | 直接重跑 workflow；`--check-url` 与 `--clobber` 保证幂等 |
| 需要撤回已发布版本 | PyPI 只能 yank，不能删除后复用版本号；发新的 patch 版本 |
| 发布说明有误 | 直接编辑 GitHub Release；没有文件需要同步 |
| 发布说明缺少某次改动 | 确认改动是通过 PR 合并的；直接 commit 不会进入自动生成说明 |
