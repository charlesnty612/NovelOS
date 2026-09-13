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
- `list` / `init`：内容仓书目录枚举与骨架预建。

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
```

`--db` 默认 `data/novelos.db`；`--content-dir` 默认 `D:/zcodeproject/NovelOS-Content`。

### 退出码

| 码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 参数错误 / project 或题材包不存在 / 数据库读不开 / `pack.json` 缺失或不合 schema |
| `2` | 部分产物写入失败（产物计数 > 0 但 `errors` 非空） |

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

## 模块结构

| 文件 | 职责 |
|---|---|
| `paths.py` | 目录常量（`BOOK_DIRS` / `GENRES_DIR` / `GENRE_PACK_FILENAME` / 根 manifest 名）、文件名派生、`Manifest`（书）与 `GenrePackDoc`（`pack.json`）数据类 |
| `slug.py` | book / pack slug 归一化与冲突去重（无外部依赖、确定性） |
| `queries.py` | 书的 DB 读取层（全 `SELECT`，按 project_id 精确取数） |
| `service.py` | `ContentSyncService`：`init_book` / `push_book` / `list_books` / `push_genre_pack` / `pull_genre_pack` / `list_genres`；卷合并 txt 复用 exporter helper |
| CLI | `scripts/content_sync.py`（`list` / `init` / `push` / `genre-push` / `genre-pull`） |

## 依赖

- `packages.core.db.get_connection`、`packages.core.ids`、`packages.core.logging_config`；
- `packages.core.exporter.build_txt`（正文 txt）、`packages.core.backup.BackupService`（JSON 备份包）；
- `packages.core.genre`（题材包 CRUD / 绑定 / `validate_payload` 校验器——写侧严格
  与 API 422 同一口径，错误串格式 `[schema] <path>: <message>`）；
- schema 权威：`docs/state-model/schemas/genre-pack.schema.json`。

## 相关测试

- `tests/unit/test_content_sync_service.py` —— 书同步（产物齐全 / 幂等 / slug 稳定 / 隔离）；
- `tests/unit/test_content_sync_slug.py` —— book slug 派生与去重；
- `tests/unit/test_content_sync_genre.py` —— 题材包 push / pull / slug / manifest /
  双轨 / CLI 退出码。
