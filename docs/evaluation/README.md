# 评测与评审报告：**本目录只放机制文档，不放运行痕迹**

> **依据**：AGENTS.md 硬规则 5「软件 / 内容 / **痕迹**三仓分离」——
> 运行 payload 存 DB；**操作痕迹在 `NovelOS-Artifacts`**；本仓（软件仓）只承载机制与规则。

## 这里该有什么

| 位置 | 内容 | 说明 |
|---|---|---|
| `quality-scoring-v0.md` | 评分口径与公式的**机制**文档 | 留在软件仓（它是规则，不是痕迹） |
| `baseline/.gitkeep`、`runs/.gitkeep` | 占位 | 保持目录存在 |

## 这里**不该**有什么（2026-10-23 已迁走）

**运行报告、逐章评分、评审 verdict、真实 id（`prj_*` / `ch_*`）一律不留在本仓**——
它们是**痕迹**，且含**真实书籍正文片段**，一旦提交就会进 GitHub 历史（**不可撤回**）。

2026-10-23 全面审查实测到：软件仓 `docs/evaluation/` 下的 `m1-data.md`（含真实 project_id 与逐章 token/评分）、
`m2-judge/judge_results.json`（含 5 个真实 chapter_id 与逐章 LLM 评审 verdict）等 8 个文件
**已随 `origin/master` 推送到 GitHub**。那部分**无法撤回**（git 历史已含），
本目录现在只做**防复发**：文件已迁至痕迹仓，且 `.gitignore` 已加规则挡住回流。

## 痕迹现在在哪

`D:/zcodeproject/NovelOS-Artifacts/docs/evaluation/`
（`runs/` 下已有 60+ 个 `run_*.json`；本仓历史上那 8 个文件也已迁入同结构）

该仓**没有配 remote**（本地仓 ⇒ 天然不会外泄）。

## 新报告写到哪

`scripts/eval_regression.py --write-docs` 与 `scripts/m1_report.py --write-docs`
现在默认写到**痕迹仓**的 `docs/evaluation/`（可用 `NOVELOS_ARTIFACTS_DIR` 改指向）。
不带 `--write-docs` 时一律写系统临时目录，**不脏工作树**。

### ⚠️ 痕迹仓找不到 ⇒ 明确失败（2026-10-23 批次三十 / 对抗审查 B1·B2 + 台账 D-19）

痕迹仓根的解析顺序是 `--artifacts-dir` > `NOVELOS_ARTIFACTS_DIR` > 软件仓**同级目录**
`<repo父目录>/NovelOS-Artifacts`；**最后一档不再是软件仓自身**——找不到就**退出码 3 +
stderr 说明原因**，**不写软件仓**（新克隆 / CI / 换机拿不到痕迹仓 ⇒ 报告写回软件仓
= 进 GitHub 历史，不可撤回；`.gitignore` 挡得住 `git add`，挡不住落盘）。

- **逃生口（默认关）**：`--artifacts-dir <path>` / `NOVELOS_ARTIFACTS_DIR` /
  `--allow-software-repo-out`（明确承担把痕迹写回软件仓的风险）。
- **基线随痕迹仓走**：`eval_regression.py` 的默认 `--baseline` 是
  `<痕迹仓>/docs/evaluation/baseline/last_passing_run.json`。
  旧默认指向软件仓，而那里只有 `.gitkeep`（基线文件从未被跟踪）⇒ **每次运行都退化成
  「无基线 → 建基线 → PASS」**，结构漂移闸门变成「与上一次自产基线比对」，
  **首次出现的漂移被静默吸收**。痕迹仓缺失时**拒绝建基线**（不建到软件仓）。
- **守卫** `scripts/guard_repo_write_path.py`：待写路径落在**软件仓根下的非 `data/`、
  非 `tmp/`** 位置 ⇒ 拒绝（退出码 2）。仓内允许子树只有 `data/`、`tmp/`
  （两者都已被 `.gitignore` 覆盖）。

