# core.content_sync（内容仓同步器）

> 职责：软件仓（NovelOS，机制）与内容仓（NovelOS-Content，内容资产）分离下的
> **搬运层**——把运行库 SQLite 里的书稿产出与题材包 payload 按固定目录结构同步到
> 内容仓，并支持把内容仓里的题材包 `pack.json` 反向导入运行库。
> 状态：书同步 V1.0（2026-09-09）；题材包 `genre-push` / `genre-pull` P3a（2026-09-13）。

## 职责与边界

**做**

- `push`：整书产物（正文 txt / canon JSON / story_state 快照 / backup JSON /
  manifest）→ 内容仓；
- `genre-push`：题材包 payload + 元信息 → `<content-dir>/genres/<slug>/pack.json`，
  并刷新内容仓根 `manifest.json` 的 `genres` 段；
- `genre-pull`：内容仓 `pack.json` → schema 校验 → 运行库 `genre_packs` 行
  （无则创建 / 有则更新）；
- `list` / `init`：内容仓书目录枚举与骨架预建；
- `genre-doctor`（别名 `check-drift`）：**只读**巡检题材包版本漂移
  （内容仓 `pack.json` ↔ 运行库 `genre_packs`），见下「漂移巡检」。

**不做**

- 不调 LLM、不启动服务、不改 schema / 不写 migration；
- 不做「10 维文件 → pack.json」的自动聚合（见下「双轨」）；
- 不做 git 提交（内容仓与软件仓各自 git 历史由人管理）；
- 不做书 → 题材包 / 题材包 → 书的交叉引用。

**读写边界**

| 命令 | 运行库 | 内容仓 |
|---|---|---|
| `list` / `init` / `push` / `genre-push` | 只读 | 只新增 / 覆盖既有产物 |
| `genre-pull` | **写**（`genre_packs` 行） | **只读**（不写任何文件） |
| `genre-doctor` | 只读 | 只读（**两侧都不写**） |

`genre-pull` 是唯一会写运行库的命令——它的语义就是把内容仓的 `pack.json` 导入运行库。
其余命令全程只 SELECT。

## 目录约定

```text
<content-dir=/D:/zcodeproject/NovelOS-Content>/
    manifest.json                     # 内容仓根清单：genres 段（同步时间 + pack 清单）
    genres/                           # 题材包命名空间
        <pack-slug>/
            pack.json                 # 运行面：payload + 元信息（genre-push 写出）
        男主快穿/                      # 编辑面：10 维题材资产（人维护，见下）
            profile.json
            structure/arcs.json …
    <book-slug>/
        novel/
            全书-<slug>.txt            # 整书正文（复用 build_txt kind=book）
            第<N>卷-<title>.txt        # 按卷合并（若该书有 volumes）
            第<N>章-<title>.txt        # 每章正文（复用 build_txt kind=chapter）
        canon/
            characters.json  world_rules.json  plot_events.json
            timeline_events.json  volumes.json
        story_state/<state_version>.json
        backup/<slug>-backup-<YYYYmmdd-HHMMSS>.json
        manifest.json
```

## 双轨：10 维文件（编辑面）vs `pack.json`（运行面）

`genres/` 下同一个题材可能有**两套互不覆盖**的载体：

| 载体 | 位置 | 谁维护 | 软件层是否消费 |
|---|---|---|---|
| 10 维题材资产 | `genres/<题材>/`（如 `genres/男主快穿/`，`profile.json` / `payoffs/library.json` / `structure/*.md` …） | **人**（策展 + 拆书回填，含审核禁忌清单） | 否——只在内容仓存在，软件仓 git diff 不含题材正文 |
| 运行面 payload | `genres/<pack-slug>/pack.json` | `genre-push` 写出，`genre-pull` 读入 | 是——`packages/core/genre` 消费的结构化子集 |

关系与约束：

1. **不同步、不互相覆盖**：`genre-push` 不会写入 / 修改 10 维文件所在目录，也不会
   根据 10 维文件重算 payload；`genre-pull` 只读 `pack.json`。
2. **payload 是 10 维的可消费子集**：schema（`docs/state-model/schemas/genre-pack.schema.json`）
   顶层键白名单只含机制能消费的段（`payoff_types` / `structure_templates` / `pacing` /
   `ratio_declarations` / `style_constraints` / `opening_rules` / `critic_rubric`）；
   其余维度（人设库 / 世界观 / 桥段 / 平台规则）留在 10 维文件里。
3. **聚合方向**：理论上 `pack.json` 可由 10 维文件聚合生成，但 v1 **不做自动聚合**——
   只提供 `genre-pull` 把 `pack.json` 导入运行库。人工策展 → payload 的固化仍由
   内容仓侧完成（后续版本再考虑聚合器）。
4. **slug 不绑定中文目录名**：`pack.json` 落在按 slug 派生的目录（题名可归一化时用
   题名 slug；纯中文题名走 `pack-<pack_id 末 8>`），与 10 维目录名（如 `男主快穿`）
   相互独立。

## CLI

```bash
# 书：列举 / 骨架 / 全量同步
python scripts/content_sync.py --content-dir D:/zcodeproject/NovelOS-Content list
python scripts/content_sync.py --db data/novelos.db --content-dir D:/zcodeproject/NovelOS-Content \
    init  --project prj_xxxxxxxx
python scripts/content_sync.py --db data/novelos.db --content-dir D:/zcodeproject/NovelOS-Content \
    push  --project prj_xxxxxxxx

# 题材包：DB → 内容仓（幂等；刷新根 manifest 的 genres 段）
python scripts/content_sync.py --db data/novelos.db --content-dir D:/zcodeproject/NovelOS-Content \
    genre-push genre-male-quicktrans-v1        # 位置参数；亦可用 --pack <pack_id>

# 题材包：内容仓 → DB（无则创建；payload 变更 → version 自增）
python scripts/content_sync.py --db data/novelos.db --content-dir D:/zcodeproject/NovelOS-Content \
    genre-pull pack-trans-v1                   # slug（相对 <content-dir>/genres/）
python scripts/content_sync.py ... genre-pull D:/zcodeproject/NovelOS-Content/genres/男主快穿
python scripts/content_sync.py ... genre-pull <...>/genres/pack-trans-v1/pack.json

# 题材包版本漂移巡检（只读；两侧都不写）
python scripts/content_sync.py --db data/novelos.db --content-dir D:/zcodeproject/NovelOS-Content \
    genre-doctor                               # 人读逐行输出
python scripts/content_sync.py ... genre-doctor --json          # 机器可读
python scripts/content_sync.py ... check-drift --json           # 等价别名
```

`--db` 默认 `data/novelos.db`；`--content-dir` 默认 `D:/zcodeproject/NovelOS-Content`。

### 退出码

| 码 | 含义（通用子命令） | 含义（`genre-doctor`） |
|---|---|---|
| `0` | 成功 | **无版本漂移**（且无巡检错误） |
| `1` | 参数错误 / project 或题材包不存在 / 数据库读不开 / `pack.json` 缺失或不合 schema | **检出版本漂移** |
| `2` | 部分产物写入失败（产物计数 > 0 但 `errors` 非空） | **巡检自身出错**（运行库读不开 / `pack.json` 不可解析等）；**巡检错误优先于漂移** |

## 漂移巡检（`genre-doctor`）

### 为什么需要它

题材包是「双轨」资产：运行面在运行库 `genre_packs` 表，运行面副本在内容仓
`genres/<slug>/pack.json`。两侧靠 `genre-push` / `genre-pull` 单向搬运，但
**没有任何机制检测两侧已经分叉**。实测（2026-10-19 复核，dev 库 + 内容仓）：

```text
DB   genre_packs['genre-male-quicktrans-v1'].version = 10   (updated_at 2026-09-17)
文件  genres/male-quicktransmigration/pack.json    .version =  6   (synced_at 2026-09-14)
```

DB 比文件新 **4 个版本**——DB 侧的编辑结果没回写内容仓——而全链路**零告警**：
`genre-push` 不检查、`genre-pull` 不检查（且 pull 一次旧文件就能把 DB 的新版本
静默覆盖回旧内容）。`genre-doctor` 把这件事从「静默」变成「可查」。

### 判定口径

对内容仓每个 `genres/<slug>/pack.json`，按 `pack_id` 与运行库 `genre_packs`
的同行比对 `version`，报出四类状态：

| `direction` | 含义 | 处置方向（**本巡检不自动处置**） |
|---|---|---|
| （无） | 两侧 `version` 一致 | 静默通过，不报 |
| `content_newer` | 内容仓 version 更高 | 正常：待 `genre-pull` 拉进运行库 |
| `db_newer` | 运行库 version 更高 | **反向漂移**：DB 的更新没回写内容仓 ⇒ 需 `genre-push` |
| `content_only` | 内容仓有 `pack.json`、运行库无该 `pack_id` | 运行库缺包 ⇒ 需 `genre-pull` |
| `db_only` | 运行库有该 `pack_id`、内容仓无 `pack.json` | 内容仓缺包 ⇒ 需 `genre-push` |

判定用的 `version` 口径与 `genre-push` / `genre-pull` **逐字一致**，不另起一套解析：

- 内容仓侧走 `GenrePackDoc.from_dict`（`version` 缺失 / 非法 → `0`；`pack_id`
  缺失 → 回退目录 slug——与 `pull_genre_pack` 同款）；
- 运行库侧走 `GenrePackService` 的行投影（`genre_packs.version` 列）。

两侧比较共用单点 `drift.compare_versions()`（`packages/core/content_sync/drift.py`）。

### 用法与输出

```bash
python scripts/content_sync.py --db data/novelos.db \
    --content-dir D:/zcodeproject/NovelOS-Content genre-doctor
```

人读形态（逐行 + 汇总）：

```text
题材包版本漂移巡检（只读）
  内容仓: D:\zcodeproject\NovelOS-Content
  运行库: D:\zcodeproject\NovelOS\data\novelos.db
  检查题材包数: 1

  [db_newer] genre-male-quicktrans-v1  DB v10 > 内容仓 v6  ← ...\genres\male-quicktransmigration\pack.json

漂移 1 处 / 检查 1 个 / 巡检错误 0 处
```

机器可读形态（`--json`）：

```json
{
  "drift": [
    {
      "pack_id": "genre-male-quicktrans-v1",
      "direction": "db_newer",
      "content_version": 6,
      "db_version": 10,
      "content_path": "D:\\...\\genres\\male-quicktransmigration\\pack.json",
      "slug": "male-quicktransmigration",
      "content_synced_at": "2026-09-14T14:30:28+08:00",
      "db_updated_at": "2026-09-17T13:43:22.742316+00:00",
      "db_source_path": "genres/male-quicktransmigration"
    }
  ],
  "checked": 1,
  "ok": false,
  "errors": []
}
```

- `checked` = 两侧 `pack_id` **并集**的规模（一致项 + 漂移项 + 单边项都计入）；
- `ok` = 无漂移**且**无巡检错误；
- `errors` = 巡检过程中的问题（`pack.json` 不可解析、`genre_packs` 表缺失等），
  **不计入 `drift`**——它们是「巡检没能完成比对」的信号 ⇒ 退出码 `2`；
- `db_source_path` = 运行库行记录的内容仓来源路径（`genre_packs.source_path`），
  在「DB 有、内容仓无」时它就是「文件该放在哪」的指针。

### 已知边界

1. **只比对 `version` 字段，不比对 payload 内容**——同版本号但内容不同的情况
   查不出来（版本号相同即视为一致）。
2. **内容仓的 `synced_at` 与运行库的 `updated_at` 语义不同、不可直接比**：
   前者是「落盘时刻」，后者是「行更新时刻」，且运行库那侧会被**任何**字段的
   更新改写（含只改元信息）。两者在 JSON 里仅作**人工排查参考，不参与判定**。
3. **只覆盖题材包（`genres/` 命名空间）**：书目录（`novel/` / `canon/` …）的
   同步漂移不在本巡检范围内。
4. **只读**：两侧都不写。发现漂移后**不会**自动 push / pull——处置是人的决定。
5. **`pack_id` 缺失的文件按目录 slug 归键**（与 `genre-pull` 同款回退）；若内容仓
   出现重复 `pack_id`，取排序后的首个并记一条巡检错误。

### `genre-pull` 的反向漂移告警

`genre-pull` 一个 pack 时，若**运行库 version 高于内容仓文件 version**
（反向漂移），会：

- 往返回结构的 `warnings` 列表追加一条告警（CLI 同时打到 **stderr**，
  stdout 仍只留机器可读 JSON）；
- **不改变 pull 的既有行为**——pull 的方向仍是「以内容仓为准」（创建 / 更新 /
  no-op / version 自增全部照旧）。告警只负责「响亮地说出来」，怎么处置由人决定。

```text
$ python scripts/content_sync.py ... genre-pull pack-trans-v1
WARNING: 反向漂移：运行库 genre_packs['genre-male-quicktrans-v1'].version=10 高于内容仓
        pack.json version=6（...）——DB 侧的更新没有回写内容仓；本次 pull 仍以内容仓为准
        （pull 语义不变），若要保留 DB 版本请先 genre-push。
```

## slug 规则

**book-slug**

1. `projects.name` NFKD 归一化 → lowercase → 仅保留 `[a-z0-9-]`（其它字符折叠为 `-`）；
2. 连续 `-` 折叠、首尾 `-` 删除；
3. 结果为空（纯中文 / 纯特殊字符）→ `book-<project_id 末 8 字符>`；
4. 命中 Windows 设备保留名（CON / NUL / COM1…）→ 加 `book-` 前缀；
5. 与内容仓现有书目录冲突 → 追加 `-2` / `-3`（确定性）。

**pack-slug**（题材包）

1. `genre_packs.name` 走**同一条归一化管线**；
2. 结果为空（纯中文如「男主快穿」/ name 为空）→ `pack-<pack_id 末 8 字符>`
   （如 `genre-male-quicktrans-v1` → `pack-trans-v1`）；
3. 命中 Windows 保留名 → 加 `pack-` 前缀；
4. 与 `genres/` 下现有目录冲突 → 追加 `-2` / `-3`；
5. **同一 `pack_id` 再次 `genre-push` 时复用既有目录**（按既有 `pack.json` 里的
   `pack_id` 反查）——slug 只在首次 push 时确定一次，后续改名也不会搬家、不会产生
   `-2` 兄弟目录。

实现单点：`packages/core/content_sync/slug.py`（`derive_slug` / `derive_genre_slug` /
`unique_slug`）。

## 幂等语义

| 产物 | 重复执行的行为 |
|---|---|
| `novel/*.txt` | 按文件名覆盖（正文可能变 → 覆盖合理） |
| `canon/*.json` | 覆盖式 |
| `story_state/<version>.json` | 已存在则跳过（按 `state_version` 天然去重） |
| `backup/*.json` | 每次 push 新建（带时间戳；同秒冲突追加 `-1` / `-2`，不覆盖历史快照） |
| 书 `manifest.json` | 覆盖式 |
| `genres/<slug>/pack.json` | 覆盖式；同一 `pack_id` 恒定路径（不产生新目录） |
| 内容仓根 `manifest.json` 的 `genres` 段 | 整体重建：`packs` = 现场扫描 `genres/*/pack.json`（内容仓真实状态为准）；其它顶层键原样保留 |
| `genre-pull` 写 DB | 内容一致 → **no-op**（不动 version）；payload 变更 → 更新且 `version` 自增；仅元信息（name / genre_tag / source_path）变更 → 更新但不升版本 |

`genre-pull` **不采用** `pack.json` 里的 `version` 覆盖 DB（version 由 DB 单调维护；
文件里的 version 只作审计提示，回显在 CLI 输出的 `file_version`）。
因此「DB 的 version 比文件新」在 pull 里是**静默的**——本版为此**加告警不改语义**
（见「漂移巡检」章的「`genre-pull` 的反向漂移告警」）。

## 失效模式（写侧严格、读侧容错）

| 触发 | 行为 |
|---|---|
| `genre-push` 的 pack 不存在 | `ValueError` → CLI 退出码 1 |
| `genre-push` 的 payload 不合 schema（旁路改库） | `ValueError`，**不落盘**（避免把非法 `pack.json` 写进内容仓）→ 退出码 1 |
| `genre-pull` 的 `pack.json` 缺失 / JSON 非法 | `ValueError` → 退出码 1 |
| `genre-pull` 的 payload 不合 schema | `ValueError`，DB 不写 → 退出码 1 |
| `pack.json` 里 `pack_id` / `name` / `genre_tag` 缺失 | 回退口径：`pack_id` → 目录 slug；`name` → slug；`genre_tag` → name（容忍人工策展的文件） |
| `pack.json` 写失败（磁盘 / 权限） | 进 `errors`；有产物 → 退出码 2，零产物 → 退出码 1 |
| 根 `manifest.json` 存在但 JSON 非法 | 记 warning，按「新建」处理（覆盖为合法清单，不抛错） |
| `list` 遇到坏 `manifest.json` | 该目录仅缺字段，不影响其它行 |
| `genre-pull` 遇**反向漂移**（DB version 高于内容仓文件 version） | 进 `warnings` + stderr 告警；**既有行为不变**（仍以内容仓为准） |
| `genre-doctor` 遇到不可解析的 `pack.json` | 进 `errors`（不中断整次巡检）→ 退出码 2 |
| `genre-doctor` 遇到运行库「缺表 / 缺列」 | 降级为「DB 侧为空」+ 记 `errors` → 退出码 2 |
| `genre-doctor` 遇到运行库锁 / I/O 故障 | **上抛**（不静默降级——那会让「巡检通过」变成假结论）→ CLI 退出码 2 |

## 模块结构

| 文件 | 职责 |
|---|---|
| `paths.py` | 目录常量（`BOOK_DIRS` / `GENRES_DIR` / `GENRE_PACK_FILENAME` / 根 manifest 名）、文件名派生、`Manifest`（书）与 `GenrePackDoc`（`pack.json`）数据类 |
| `slug.py` | book / pack slug 归一化与冲突去重（无外部依赖、确定性） |
| `queries.py` | 书的 DB 读取层（全 `SELECT`，按 project_id 精确取数） |
| `service.py` | `ContentSyncService`：`init_book` / `push_book` / `list_books` / `push_genre_pack` / `pull_genre_pack` / `list_genres`；卷合并 txt 复用 exporter helper |
| `drift.py` | 题材包**版本漂移巡检**：`detect_genre_drift` / `compare_versions`（单点比较口径）/ `DriftItem` / `GenreDriftReport` |
| CLI | `scripts/content_sync.py`（`list` / `init` / `push` / `genre-push` / `genre-pull` / `genre-doctor`） |

## 依赖

- `packages.core.db.get_connection`、`packages.core.db.is_missing_schema_error`（降级范围收窄）、
  `packages.core.ids`、`packages.core.logging_config`；
- `packages.core.exporter.build_txt`（正文 txt）、`packages.core.backup.BackupService`（JSON 备份包）；
- `packages.core.genre`（题材包 CRUD / 绑定 / `validate_payload` 校验器——写侧严格
  与 API 422 同一口径，错误串格式 `[schema] <path>: <message>`）；
- schema 权威：`docs/state-model/schemas/genre-pack.schema.json`。

## 相关测试

- `tests/unit/test_content_sync_service.py` —— 书同步（产物齐全 / 幂等 / slug 稳定 / 隔离）；
- `tests/unit/test_content_sync_slug.py` —— book slug 派生与去重；
- `tests/unit/test_content_sync_genre.py` —— 题材包 push / pull / slug / manifest /
  双轨 / CLI 退出码；
- `tests/unit/test_content_sync_drift.py` —— 版本漂移巡检（四类方向 / `--json` 字段 /
  退出码 0-1-2）、`genre-pull` 反向漂移告警（**行为不变**）、降级收窄（缺表降级 /
  故障上抛）、**突变自检**（比较机制是承重的）。
