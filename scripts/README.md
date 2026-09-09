# scripts（运维脚本）

> 职责：NovelOS 后端的本地运维 CLI——迁移执行与开发服务器启动；Sprint 0 仅含两个最小可执行入口，可通过 `python scripts/<name>.py` 直接运行（脚本内部自动 `sys.path.insert(0, REPO_ROOT)`）。
> 状态：已实现 Sprint 0（migrate.py / serve.py）。

## 职责与边界

**做**：
- 提供「仅跑迁移、不启服务」的开发流程入口
- 提供「启动 uvicorn 服务」的开发流程入口
- 统一从 `packages.core.config.get_settings()` 读取配置，保证 CLI 与 API 行为一致

**不做**：
- 不实现生产部署脚本（属于 Phase B Tauri 壳任务，Sprint 12）
- 不实现数据库备份/恢复（属于 Sprint 7 Version 之后）

## 对外接口

| 脚本 | 来源 | 入口函数 | 用途 |
|---|---|---|---|
| `scripts/migrate.py` | `scripts/migrate.py:16` | `main() -> int` | 执行 SQLite 迁移并打印 `applied / skipped / tables / db` |
| `scripts/serve.py` | `scripts/serve.py:15` | `main() -> int` | 启动 uvicorn，host/port 取自 `Settings` |

### `scripts/migrate.py` 输出格式

```
applied=<N> skipped=<N> tables=<int> db=<path>
newly applied:
  - 0001_init.sql
```

### `scripts/serve.py` 行为

- 调用 `uvicorn.run("packages.core.api.main:app", host=settings.api_host, port=settings.api_port, reload=False, log_level=settings.log_level.lower())`
- 默认绑定 `127.0.0.1:8000`，可通过 `NOVELOS_API_HOST` / `NOVELOS_API_PORT` 环境变量覆盖

## 依赖

- 上游：`packages.core.config.get_settings`、`packages.core.db.apply_migrations`、`packages.core.logging_config.configure_logging`
- 外部库：uvicorn（仅 `serve.py`）
- 标准库：sqlite3（迁移 runner 内部使用）

## 使用 / 入口

```bash
# 仅执行迁移
python scripts/migrate.py

# 启动开发服务器
python scripts/serve.py

# 自定义端口 / 日志级别
NOVELOS_API_PORT=9000 NOVELOS_LOG_LEVEL=DEBUG python scripts/serve.py
```

## 维护注意点

- **sys.path hack**：脚本首行 `sys.path.insert(0, REPO_ROOT)`，让 `python scripts/xxx.py` 可解析 `packages.*`；不要移除。
- **配置单一来源**：CLI 一律走 `get_settings()`，避免硬编码路径 / 端口。
- **`migrate.py` 退出码**：成功返回 0；不抛异常即视为成功（迁移幂等）。
- **`serve.py` 不启用 reload**：避免 Sprint 0 阶段文件变更导致的双进程问题；如需 reload 用 `uvicorn --reload` 显式调用。
- **权威文档**：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 0 DoD（`uvicorn` 启动 `/health` 200、迁移后 28 表）。

---

## `scripts/content_sync.py` —— 内容仓同步器 CLI

把运行库（SQLite `data/novelos.db`）中某本书（project）的产出，按「每本书一个独立子目录」同步到内容仓（默认 `D:/zcodeproject/NovelOS-Content`），实现软件 / 内容两仓分离下的内容版本管理闭环。

> 仅写软件仓代码与脚本；不修改内容仓已有历史文件，仅新建 `<content-dir>/<book-slug>/` 子目录。

### 用法

```bash
# 列出内容仓下所有 book 子目录（slug / project_id / synced_at / name）
python scripts/content_sync.py --content-dir D:/zcodeproject/NovelOS-Content list

# 仅建书目录骨架 + manifest，不写其它产物（用于先验位置 / slug）
python scripts/content_sync.py \
    --db data/novelos.db \
    --content-dir D:/zcodeproject/NovelOS-Content \
    init --project prj_xxxxxxxx

# 全量同步一本书到内容仓；幂等可重跑
python scripts/content_sync.py \
    --db data/novelos.db \
    --content-dir D:/zcodeproject/NovelOS-Content \
    push --project prj_xxxxxxxx
```

### 目录约定

```
<content-dir>/<book-slug>/
    novel/
        全书-<slug>.txt                # 整书正文（复用 packages.core.exporter.build_txt kind=book）
        第<N>卷-<title>.txt            # 按卷合并（若该书有 volumes）
        第<N>章-<title>.txt            # 每章正文（复用 build_txt kind=chapter）
    canon/
        characters.json
        world_rules.json
        plot_events.json
        timeline_events.json
        volumes.json
    story_state/
        <state_version>.json           # 每版本 story_states 快照（按版本号去重）
    backup/
        <slug>-backup-<YYYYmmdd-HHMMSS>.json  # 复用 packages.core.backup.BackupService.export_project
    manifest.json
```

### `book-slug` 派生规则（确定性）

1. `projects.name` 做 NFKD 归一化 + lowercase + 仅保留 `[a-z0-9-]`；
2. 连续 `-` 折叠、首尾 `-` 删除；
3. 结果为空（纯中文 / 纯特殊字符）→ fallback 到 `book-<project_id 末 8 字符>`；
4. 同一 project_id 的 manifest 已存在 → 直接复用其所在目录（保证 push 重跑路径稳定）；
5. 与内容仓其它 book 子目录冲突 → 追加 `-2` / `-3` 后缀。

### 幂等性

| 产物 | 行为 |
|---|---|
| `novel/*.txt` | 每次 push 覆盖（章节正文可能变化） |
| `canon/*.json` | 每次 push 覆盖 |
| `story_state/<version>.json` | 同版本已存在则跳过（按 state_version 命名天然去重） |
| `backup/<slug>-backup-<ts>.json` | 每次 push 新建（带时间戳，保留历史快照） |
| `manifest.json` | 每次 push 覆盖 |

### 退出码

- `0`：全部产物写入成功，`errors` 为空；
- `1`：参数错误 / project 不存在 / 数据库读不开 / 全失败；
- `2`：部分产物写入失败（`errors` 非空但已写入部分产物）。

### 依赖

- 上游：`packages.core.content_sync`、`packages.core.exporter.builder.build_txt`、`packages.core.backup.BackupService.export_project`、`packages.core.ids.new_id`；
- 外部库：无新增；
- 标准库：`argparse` / `json` / `pathlib` / `datetime`。