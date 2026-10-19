# NovelOS Agent Skill 包

把 **NovelOS**（本地优先的小说写作 OS）接到你的 coding agent 上，让它替你驱动
**plan → write → review → commit** 的章节生产闭环。

- **Skill**：`skills/novelos/SKILL.md`（Agent Skills 规范，frontmatter 只用规范 6 字段）
- **薄客户端**：`skills/novelos/scripts/novelos.py`（stdlib-only，零业务逻辑）
- **分层参考**：`skills/novelos/references/*.md`（按时刻读，每份一个阶段）
- **契约自检**：`skills/novelos/scripts/check_skill_package.py`
- **三平台清单**：`.claude-plugin/`（Claude Code）、`.codex-plugin/` + `.agents/plugins/`
  （Codex）、`.cursor-plugin/`（Cursor）

---

## ⚠️ 先读这一段：上手门槛不低

**这个 skill 不是零安装的说明书。** 它假定**本机已经有一个能跑的 NovelOS 服务**。

| 前置 | 说明 |
|---|---|
| NovelOS 仓库 | 需要 clone 到本机（skill 与它同仓；单独拿走 skill 没有用） |
| Python 依赖 | `uv sync --extra dev`（或 `pip install -e .`）——`fastapi` / `uvicorn` / `pydantic` 等 |
| 模型配置 | 在 UI 或 API 里配好模型档案 + 绑定 `creative_writing` 环节（**不配就跑不了 AI 环节**） |
| 端口 | 默认 `127.0.0.1:18081`（`NOVELOS_PORT` 可改） |

**前端 UI 不是必需的**（`npm run build` 可以不做）——skill 走 REST，纯后端模式即可。
但**要真正用起来**（手动改稿、看报告、题材包编辑），UI 会舒服很多。

> **换句话说**：装这个 skill 省掉的是「查端点 / 拼请求 / 记状态机」，
> **不省**「把 NovelOS 跑起来」。

### 让 NovelOS 跑起来

**方式 A（推荐）：先手工起一次，确认能跑**

```bash
cd <NovelOS 仓库根>
uv sync --extra dev          # 装依赖（一次性）
python -m packages.core.api.main
# → 服务起在 http://127.0.0.1:18081
```

**方式 B：让 skill 帮你拉起**

```bash
python skills/novelos/scripts/novelos.py doctor   # 探测
python skills/novelos/scripts/novelos.py ensure   # 不在跑就拉起
```

`ensure` 做的事就一件事：在**仓库根**执行 `python -m packages.core.api.main`
（Windows 上用 `CREATE_NEW_PROCESS_GROUP` 起，不随 CLI 的 Ctrl-C 一起死），
然后轮询 `/api/health` 直到就绪。**它不下载任何东西**——仓库不在本机就先 clone。
服务起后**继续在后台跑**（输出里带 `pid`，要停自己 kill）。

**方式 C：UI** —— `npm run build && npm run dev`（前端 dev server 走 `/api` 代理）。

---

## 安装

### Claude Code

```bash
# 从本地仓库加市场（市场根 = 仓库根，含 .claude-plugin/marketplace.json）
claude plugin marketplace add /path/to/NovelOS
claude plugin install novelos@novelos
```

或者用 `--plugin-dir` 直接挂：

```bash
claude --plugin-dir /path/to/NovelOS
```

### Codex

```bash
# 仓库根的 .agents/plugins/marketplace.json 即市场清单
```

`source` 是 `{"source":"local","path":"./"}`（Codex 的形状）。

### Cursor

把仓库根作为插件目录挂进 Cursor 即可（`.cursor-plugin/plugin.json` + `marketplace.json`）。

### 三平台的差异（**故意不共用一份**）

| | 清单位置 | `source` 形状 | 特别之处 |
|---|---|---|---|
| Claude Code | `.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` | 相对路径**字符串**，必须以 `./` 开头 | 字段最多（`metadata` / `hooks` / `mcpServers` / `lspServers` / `outputStyles` / `workflows` …） |
| Codex | `.codex-plugin/plugin.json` + `.agents/plugins/marketplace.json` | **对象** `{"source":"local","path":…}` | 有 `interface` / `defaultPrompt` / `brandColor` 等 Claude 没有的字段 |
| Cursor | `.cursor-plugin/plugin.json` + `.cursor-plugin/marketplace.json` | **裸字符串**（不带 `./`） | `additionalProperties:false` ⇒ **未知键被拒**；**没有 `metadata`**；`author` 只允许 name/email |

---

## 用法

### 作为 agent

直接说人话，agent 会按 `SKILL.md` 的路由表走：

> 「写第 3 章，3000 字」／「开新书，玄幻，300 万字」／「审校一下第 3 章」
> ／「快通道把这章写了直接提交」／「伏笔现在什么状态」／「导出投稿包」

### 直接用薄客户端

```bash
NOVELOS="skills/novelos/scripts/novelos.py"

python "$NOVELOS" doctor
python "$NOVELOS" project list
python "$NOVELOS" project create --name "新书" --genre 玄幻 --writing-bible "第一人称"
python "$NOVELOS" chapter write --project prj_x --chapter-no 3 --target-word-count 3000 --wait
python "$NOVELOS" chapter review --project prj_x --chapter-no 3 --wait
python "$NOVELOS" run approve <run_id>
python "$NOVELOS" chapter commit --project prj_x --chapter-no 3
python "$NOVELOS" state show --project prj_x
python "$NOVELOS" quality report --chapter ch_x
python "$NOVELOS" delivery verdict --project prj_x
python "$NOVELOS" export --kind submission --project prj_x
```

默认输出**紧凑 JSON**（机器可读）；`--pretty` 给人看。
错误走 stderr，带**可操作**的提示（不是裸 traceback）。

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | 参数 / 用法错误 |
| 3 | 服务不可达 |
| 4 | 服务端 4xx |
| 5 | 服务端 5xx |
| 6 | 本地 I/O 失败 |

---

## 边界（先知道再用）

- **单用户本机工具，API 无鉴权**。⇒ **别把服务暴露到本机之外**。
- **数据不出本机**——除了你显式配置的模型 provider 请求（write / review / commit /
  project-init / continue 会真的调模型，**烧额度**）。
- **只读端点随便用**：`doctor` / `project list|show` / `chapter list|status` /
  `state show|diff` / `quality report` / `delivery *` / `export`。
- **既有测试书籍不是工作对象**——本 skill 只用于**新建项目**。
- 完整边界见 `skills/novelos/references/limits.md`。

---

## 维护

```bash
# 契约自检（46 项）：清单字段集 / frontmatter 6 字段 / 版本一致 / 引用完整 / 离线 --help
python skills/novelos/scripts/check_skill_package.py

# 变异验证：造坏副本并断言自检脚本会报红（6 个变异全部被捕获）
python skills/novelos/scripts/check_skill_package.py --selftest

# lint
python -m ruff check skills
```

改 `SKILL.md` 时记得：`metadata.version` 必须与三个 `plugin.json` 的 `version`
**逐字一致**（自检会抓）；新增 `references/*.md` 必须在 `SKILL.md` 里被引用
（自检会抓孤儿文件）。

---

## 授权

本 skill 包与 NovelOS 同仓，随仓库的 **AGPL-3.0** 授权。
完整许可证全文见**仓库根的 `LICENSE`**（本目录**不复制**该文件）。
