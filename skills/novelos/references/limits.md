# limits —— 边界与已知限制

> **读法**：本文件回答「这事能不能干 / 会发生什么 / 什么不做」。
> **任何拿不准的场景都读它，读到 EOF**（含末尾「本轮约束」）。
> 数字与限制逐字取自本仓源码与实测，不是印象。

## 1. 部署形态：单用户、本机、**无鉴权**

- NovelOS 是**单用户本机工具**。服务默认监听 `127.0.0.1:18081`。
- **API 没有任何鉴权**：能访问这个端口的人 = 能读写全部项目数据。
  ⇒ **绝对不要**把它绑到 `0.0.0.0`、不要放进容器对外暴露、不要做端口转发。
- 端口可用 `NOVELOS_PORT` / `NOVELOS_API_PORT` 覆盖；监听地址用 `NOVELOS_API_HOST`。
- 前端 UI 与 API 同进程（`apps/web/dist` 存在时挂 SPA，否则纯后端模式）。
  **本 skill 不需要 UI**——一切走 REST。

## 2. 数据：不出本机

- 唯一事实源是**本机 SQLite**（默认 `data/novelos.db`；可用 `NOVELOS_DB_PATH` /
  `NOVELOS_DATA_DIR` 改）。
- 人编辑面（正文、大纲、角色卡）另存**内容仓**（与软件仓分离），**双轨不自动聚合**。
- **本 skill 不上传任何东西**、不连任何外部服务，除了：
  1. 你显式跑 write/review/commit/continue/project-init 时，NovelOS 会按你配置的
     **模型 provider** 发出请求（**数据会离开本机到那个 provider**）；
  2. 导出是**你把文件写到本地磁盘**。
- ⇒ **凡涉及保密内容，先确认 provider 的数据政策。**

## 3. 额度：真实模型调用会烧钱

| 操作 | 是否调真实 LLM |
|---|---|
| `chapter write` / `chapter review` / `chapter commit` | **是**（commit 里的 observer 也调） |
| `project-init` | **是**（4 个 agent：题材定位 / 世界观 / 角色设计 / 卷纲） |
| `POST /api/chapters/{cid}/quality/evaluate` | **否**（纯规则计算） |
| `POST /api/chapters/{cid}/quality/judge` | **否**（只落分，分数由调用方给） |
| `POST /api/model-profiles/{id}/test` | **是**（真发一条） |
| `POST /api/agents/{name}/run` | **是** |
| `POST .../chapters/{cid}/continue` | **是**（1~3 次） |
| `chapter plan` 里的 `director_planner` | **是** |
| `chapter review` 里的 `critic` | **是**（**默认开**；`critic_mode=off` 可关） |
| `chapter review` 里的 `deep_reviewer` | 默认**否**（要显式传 `deep_review=true` 才调） |
| `doctor` / `project list|show` / `chapter list|status` / `state show|diff` / `quality report` / `delivery *` / `export` | **否**（纯读） |

> **两条不同的规则，别混**：
> ① **写小说**是产品正常用途 ⇒ 真实调用正常，由作者决定要不要开；
> ② **为验证软件行为**而发真实模型请求 ⇒ 属"开发时的验证"，不在授权内
> （用只读端点 + 已有数据判断）。
> **本 skill 的所有命令里，只有上表标「是」的那些会烧额度。**

**没有跑通的服务 = 没有可用的章节闭环。** 若 `run status` 的 `error` 提到
provider / api key，先查 `GET /api/model-profiles`（看 `has_api_key`）与
`GET /api/capability-bindings`（看 `creative_writing` 绑没绑）。

## 4. 客户端（`scripts/novelos.py`）的已知边界

- **零业务逻辑**：只做 URL 拼装 + HTTP 调用 + JSON 打印。
  **唯一的客户端变换**是 `--chapter-no N → chapter_id`（查一次章节列表）。
- **未封装的端点**（需要时用 `curl` 或 UI，路径见各 references）：
  题材包 CRUD / 绑定、model-profiles / capability-bindings 的写操作、
  `gate-revise`、`continue`、`cancel`、`deltas` / `commits` / `rollback`、
  `volumes`、`q8-export`。
- **`cancel` 只对 `RUNNING` 有效**：`PAUSED` 的 run 取消不了（走 resume 后的
  reject/approve 决议路径）。这是服务端状态机，**不是客户端缺陷**。
- **`--wait` 停在 PAUSED**（等人工）**与**终态；它**不会**自动替你批准。
- **超时**：`--timeout` 是单次 HTTP 超时（默认 60s）；`--wait-timeout` 是轮询上限
  （默认 900s）。长章节写作可能超过前者——**起跑不要用 `--wait` 之外的大 timeout
  去硬等**，用 `run status --wait`。
- **输出默认紧凑 JSON**（机器可读）；`--pretty` 给人看。
  **错误走 stderr**，退出码见下表。

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | 参数 / 用法错误 |
| 3 | 服务不可达（连接被拒 / 超时） |
| 4 | 服务端 4xx（请求本身的问题） |
| 5 | 服务端 5xx |
| 6 | 本地 I/O 失败 |
| 130 | 被中断（Ctrl-C） |

## 5. 服务启动（`ensure`）的边界

- `ensure` **只在本机已有 NovelOS 仓库时可用**：它执行
  `python -m packages.core.api.main`（cwd = 仓库根），**不下载任何东西**。
- 找不到仓库 ⇒ 报错并提示 `--repo-root` / `NOVELOS_REPO_ROOT`。
- 起服务时会跑**数据库迁移**（新增迁移只加不改，存量兼容）。
- 日志默认写**临时目录** `novelos-skill-server.log`（`--log-file` 可改）。
- **起完不负责关**：`ensure` 返回后服务**继续在后台跑**（这是有意的——
  服务停了后续所有命令都会 3 号退出码）。要停就自己 kill 那个 pid
  （`ensure` 的输出里有 `pid`）。
- **Windows 差异**：子进程用 `CREATE_NEW_PROCESS_GROUP` 起（不随本 CLI 的
  Ctrl-C 一起死）；控制台里 `Ctrl-C` 不会停服务。
- **`ensure` 拉起的服务不带前端构建**（`apps/web/dist` 不在版本库）⇒ 纯后端模式，
  **对本 skill 无影响**。

## 6. 领域层的限制（来自源码注释，不是猜测）

- **快通道是「关闸门不是关判定」**：`observer` 照跑；`quality_gate` 照算、照落库，
  只是 `should_block` 恒假。**快通道 ≠ 自动批准 ≠ 已签字。**
- **章节状态机是硬约束**：commit 要求章节已 `REVIEWED`（快通道下放行 `DRAFTED`）。
  状态不对 ⇒ 409。
- **时序守卫**：最近一次 COMPLETED review 之后又产生了更新草稿 ⇒ commit **409**。
  ⇒ **改完稿必须重新审校再提交**。
- **乐观锁**：`state_version` 不匹配 ⇒ 409 `optimistic_lock`。
  ⇒ **重读再提交，不要强推**。
- **并发守卫**：同 chapter 下有 `RUNNING`/`PENDING` 的活跃 run ⇒ 起跑 409。
  **PAUSED 的 run 不挡新 run**（这是刻意设计，为 resume 留生效空间）。
- **genre pack 绑定失败会降级**（读取失败时按"未绑定"处理，**不崩**）⇒
  "题材约束好像没生效"时，先查绑定状态。

## 7. 本 skill **不**做的事（重申）

- 不替作者做创作决策（题材走向 / 人物结局 / 章节取舍）。
- 不替你在对话里"顺手"写正文冒充产出。
- 不改数据库、不改源码、不跑迁移。
- 不复刻服务端的判定逻辑（字数带 / 门禁 / 状态机 / Story State 推进）。
- 不代作者在 `confirm` 档门禁上签字。
- 不为"验证软件行为"发起真实 LLM 调用。
- 不碰既有测试书籍（续写 / 重生成 / 删改既有内容）。
- 不上传任何数据。
- 不做版本控制操作（不 commit / push / 改分支）。

---

## 本轮约束

1. **服务没跑 ⇒ 先 `doctor` / `ensure`**；失败就把错误原文交给用户，别猜怎么修。
2. **烧额度的命令要事先确认**：write / review / commit / project-init / continue /
   `model-profiles/{id}/test` / `agents/{name}/run`。
3. **只读端点随便用**：`project list|show`、`chapter list|status`、`state show|diff`、
   `quality report`、`delivery *`、`export`。
4. **不要把服务暴露到本机之外**（无鉴权）。
5. **数据不出本机**，除了你显式配置的模型 provider 请求。
6. **PAUSED 不等于失败、取消不了**；走 resume 决议路径。
7. **409 先读 `detail`**（并发 / 未审改动 / 状态机 / 乐观锁各有各的修法），
   **不要盲目重试**。
8. **不要为"让老数据好看"而改既有内容**——测试书籍不是工作对象。
9. **本 skill 不做 git 操作**；要提交由用户自己来。
