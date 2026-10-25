"""SQLite 迁移 runner（Sprint 0）。

职责：
- ``get_connection(db_path)``：开启外键的 sqlite3 连接。
- ``apply_migrations(db_path, migrations_dir=None)``：按文件名升序执行
  ``migrations_dir`` 下所有 ``*.sql``；通过 ``_migrations`` 表幂等追踪。
- ``default_migrations_dir()``：默认迁移目录（锚定仓库根，与进程 CWD 无关）。

默认目录锚定（V3.9 全量检修 M4）：``migrations_dir=None`` 时用 ``default_migrations_dir()``
= ``<repo>/database/migrations``，**不随 CWD 漂移**（CWD 相对的旧实现从仓库根以外启动会
静默 0 迁移）；传参覆盖优先级不变。目录缺失 / 0 个 ``*.sql`` 时记 WARNING 但不抛——
嵌入式 / 空目录用法合法，静默才是事故形态。

幂等策略：执行前先查 ``_migrations``，跳过已记录的脚本。脚本执行成功
后才写入记录。重复执行不会重复跑 DDL。
已知限制：DDL 脚本自身不带 ``IF NOT EXISTS``，若脚本执行成功但写记录
前进程崩溃，重跑会因「表已存在」报错，需人工处置（删除半成品 db 重来）。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from pathlib import Path

log = logging.getLogger("novelos.db")

# 默认迁移目录锚定**仓库根**，而非进程 CWD（V3.9 全量检修 M4，R1/R3 双探针实证）：
# 本文件位于 ``<repo>/packages/core/db.py`` → ``parents[2]`` 即仓库根。
# 修正前是 ``Path("database")/"migrations"``（CWD 相对）——从仓库根以外的 CWD 启动时
# 目录不存在 → 静默 0 迁移（``/api/health`` 仍报 ok、业务端点全 500）。
# 显式传参仍可覆盖（嵌入式 / 测试用法零变化）。
_DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "database" / "migrations"
# V2.0 Wave C 任务一：FTS5 虚表（chapter_fts 及其内部表 chapter_fts_config/data/docsize/idx）
# 在 sqlite_master 中均登记为 ``type='table'``；``count_tables`` / health.tables 的
# "业务表"口径只关心真业务表，故排除 ``chapter_fts%`` 前缀的所有表。虚表本身（1 张）
# + 内部表（4 张，共 5 张）统一被这一条排除规则覆盖。
_TABLE_COUNT_QUERY = (
    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
    "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'chapter_fts%'"
)


# ---------------------------------------------------------------------------
# 库落点闸（BUG-59 / 落码批 C-01）
# ---------------------------------------------------------------------------
#: 软件仓根（本文件位于 ``<repo>/packages/core/db.py`` → ``parents[2]``）。
#: ⚠️ 与 ``packages/core/db.py`` 的迁移目录锚点**同一个**仓根口径（不随 CWD 漂移）。
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 仓内**允许**的库落点子树（口径①：**仅** ``data/``；**不放** ``tmp/``——
#: ``tmp/`` 虽被 ``.gitignore`` 盖住，但实测仓内存在过 247 件 ⇒ 放行会削弱闸的判别力）。
ALLOWED_REPO_DB_SUBDIRS: tuple[str, ...] = ("data",)

#: 库族四件套（判定对象）。⚠️ 实际判定按**所在目录**（四件套同目录 ⇒ 同判）
#: ⇒ 比「按后缀枚举」更严：``.sqlite`` / ``.db3`` 之类**换后缀绕过**面同样被堵。
DB_FAMILY_SUFFIXES: tuple[str, ...] = (".db", ".db-wal", ".db-shm", ".db-journal")

#: 环境变量**只作传播，永不兜底放行**（口径②）：
#: - 置 ``0`` / ``off`` / ``false`` / ``no`` / 空串 ⇒ **收紧**：连调用点显式
#:   ``allow_repo_local=True`` 也拒绝（总闸）；
#: - 置其它真值 ⇒ **不放宽**（仍须调用点显式传 True）；
#: ⇒ **任何取值下 env 都不能单独让仓内库落点通过**（判别用例须清空该 env 再跑）。
ENV_ALLOW_REPO_LOCAL_DB = "NOVELOS_ALLOW_REPO_LOCAL_DB"

#: 库落点被拒的退出码（与既有写落点闸的 ``EXIT_WRITE_PATH_REJECTED`` 同值域、语义独立）。
EXIT_DB_PATH_REJECTED = 2


class RepoLocalDbPathError(RuntimeError):
    """库落点被闸拒绝（仓内非允许子树）。**抛出前无任何副作用**。"""


def _env_forbids_repo_local() -> bool:
    """env 是否处于「收紧」取值（只收紧、不放宽）。"""
    raw = os.environ.get(ENV_ALLOW_REPO_LOCAL_DB)
    if raw is None:
        return False
    return raw.strip().lower() in {"0", "off", "false", "no", ""}


def check_db_path(db_path: Path | str, *, allow_repo_local: bool = False) -> Path:
    """库落点闸：仓内**仅**允许 ``data/`` 子树；仓外一律放行。返回规范化后的绝对路径。

    判定口径（口径① ＋ 判别力）：

    - 路径先 ``Path.resolve()`` 规范化（消掉 ``..`` / 别名 / 符号链接）；
    - 落在软件仓**外** ⇒ 放行（系统临时目录、痕迹仓等）；
    - 落在软件仓**内** ⇒ 必须位于 ``data/`` 子树（``parts[0]`` **精确匹配**，
      **禁子串包含** ⇒ 否则 ``data2/``、``not_data/`` 会被误放行）；
    - 判定按**所在目录** ⇒ 库族四件套（``.db`` / ``.db-wal`` / ``.db-shm`` /
      ``.db-journal``）同目录同判，无换后缀绕过面。

    ``allow_repo_local=True`` ＝ **调用点显式授权**（默认 False）；环境变量
    ``NOVELOS_ALLOW_REPO_LOCAL_DB`` **只收紧不放宽**（见 :data:`ENV_ALLOW_REPO_LOCAL_DB`）。

    拒绝时抛 :class:`RepoLocalDbPathError`，**零副作用**（不建连接、不建库、
    不建 ``-wal``/``-shm``、不删任何文件）。
    """
    target = Path(db_path).expanduser().resolve()
    try:
        rel = target.relative_to(_REPO_ROOT)
    except ValueError:
        return target  # 仓外 ⇒ 放行
    parts = rel.parts
    if parts and parts[0] in ALLOWED_REPO_DB_SUBDIRS:
        return target
    shown = rel if parts else Path(".")
    if _env_forbids_repo_local():
        raise RepoLocalDbPathError(
            f"拒绝库落点：{target}（相对仓库根 {shown}）；"
            f"环境变量 {ENV_ALLOW_REPO_LOCAL_DB} 处于收紧取值 ⇒ 即使调用点显式授权也拒绝"
        )
    if allow_repo_local:
        return target
    raise RepoLocalDbPathError(
        f"拒绝库落点：{target}（相对仓库根 {shown}）；"
        f"软件仓内只允许 {'、'.join(f'{d}/' for d in ALLOWED_REPO_DB_SUBDIRS)} 子树"
        "（库族四件套同目录同判）"
    )


def require_db_path(db_path: Path | str, *, allow_repo_local: bool = False) -> Path:
    """库落点闸（脚本入口用）：不合法 ⇒ 诊断到 stderr 并 ``raise SystemExit(2)``。"""
    try:
        return check_db_path(db_path, allow_repo_local=allow_repo_local)
    except RepoLocalDbPathError as exc:
        print(f"[拒绝库落点] {exc}", file=sys.stderr)
        raise SystemExit(EXIT_DB_PATH_REJECTED) from None

# ---------------------------------------------------------------------------
# 「结构缺失」判别式（2026-10-16 批次十 · 清欠账时提为共享单点）
# ---------------------------------------------------------------------------
#: SQLite「缺表 / 缺列」报错的特征子串（迁移未跑的极老库）。
#:
#: **故意取子串而非异常类**：这两类与运行时故障**共用** ``sqlite3.OperationalError``，
#: 只有消息能区分「结构缺失」与「故障」——本机实测（3.12 / SQLite 3.x）：
#:
#: ==============================  ==========================================
#: 实测消息                          判定
#: ==============================  ==========================================
#: ``no such table: nope_t``         结构缺失 ✅（降级为「按未建处理」）
#: ``no such column: nope_col``     结构缺失 ✅
#: ``database is locked``            故障 ❌（必须上抛）
#: ``disk I/O error``                故障 ❌
#: ``database disk image is malformed`` 故障 ❌
#: ``attempt to write a readonly database`` 故障 ❌
#: ``database or disk is full``      故障 ❌
#: ``table t already exists``        故障 ❌（迁移重复执行，必须上抛）
#: ==============================  ==========================================
_MISSING_SCHEMA_MARKERS: tuple[str, ...] = ("no such table", "no such column")


def is_missing_schema_error(exc: sqlite3.OperationalError) -> bool:
    """``exc`` 是否属「缺表 / 缺列」（即迁移未跑的极老库）。

    **用途**：把 ``except sqlite3.OperationalError`` 的**降级范围**收窄到「结构缺失」，
    让真正的运行时故障（锁 / I/O / 损坏 / 只读 / 磁盘满）**上抛**而不是被静默吞掉。

    **为什么值得收窄**：静默吞掉 ``database is locked`` 的后果不是崩溃，而是
    **降级被当成正常**——例如题材包绑定失败被读成「项目没绑定题材包」，
    于是「作者以为体裁约束生效、产出的正文却没带体裁」，**全程零告警**。
    同 ``routers/workflows/fast_mode.py`` 的收窄理由。

    ⚠️ **用它的前提**（两条，缺一条就别用）：
    ① 那个 ``except`` 的注释**只**声称覆盖「老库 / 缺表」——若注释声称的更宽，
       收窄就是**改变契约**而非修缺陷，须先确认；
    ② 调用点外层**没有**再套一层无差别 ``except`` 把它重新吞回去——
       批次九实测：内层收窄了而 ``planner_input.py`` / ``scene_planner.py``
       外层无差别再吞 ⇒ 那两条调用链的修复**完全无效**。**收窄必须连外层一起看。**
    """
    msg = str(exc).lower()
    return any(marker in msg for marker in _MISSING_SCHEMA_MARKERS)


def get_connection(db_path: Path | str, *, allow_repo_local: bool = False) -> sqlite3.Connection:
    """开启外键 PRAGMA 与 WAL 日志模式的连接；row_factory 设为 Row 便于查询。

    ⚠️ **库落点闸**（BUG-59 / 落码批 C-01）：**先**过闸、**后**连接 ⇒ 仓内非
    ``data/`` 落点**拒绝且零副作用**（不建库、不建 ``-wal``/``-shm``、不删任何文件）。
    ``allow_repo_local=True`` 是**调用点显式授权**（默认 False）；环境变量
    ``NOVELOS_ALLOW_REPO_LOCAL_DB`` **只收紧不放宽**（见 :func:`check_db_path`）。
    """
    resolved = check_db_path(db_path, allow_repo_local=allow_repo_local)
    conn = sqlite3.connect(str(resolved))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL 模式：读写并发更高；对测试库（tmp_path）同样适用。
    #
    # ⚠️ 2026-10-16 **实测推翻**旧注释「sqlite 自动忽略失败」——切 journal 模式需要
    # 排他锁，他连接持写锁时本句**抛** ``sqlite3.OperationalError: database is locked``。
    # （只读介质 / 网络盘 / 从未成功切过 WAL 的库上，这条路径真实可达。）
    #
    # ⚠️ **顺序刻意排在 busy_timeout 之前**（勿"顺手优化"成之后）：把 busy_timeout
    # 提前会让本句先等满 5s 再抛——同样的异常、多花 5 秒。本仓 M1 事故的教训就是
    # 「宁可立刻失败也不要挂住」，连接创建是热路径。
    #
    # 为什么不直接让它抛：WAL 是**并发优化、不是正确性前提**，失败时退回 rollback
    # journal 继续用，功能完全不受影响。可一旦冒出去，调用方
    # ``_peek_chapter_context`` / ``_peek_project_context`` 的
    # ``except Exception -> 全空态`` 会把「打不开库」当成「库里没这个章」——
    # 于是 ``state_version`` / ``genre_pack_ref`` 等缓存键维度被写成 ``0`` /
    # ``'__none__'``（硬规则 2「漏键 = 脏命中」），**且零告警**。
    # 这是本仓反复登记的「静默降级」形状最隐蔽的一层：脏的不是数据，是缓存键。
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        log.warning(
            "切 WAL 失败，退回 rollback journal（并发降低，功能不受影响）：%s",
            db_path,
            exc_info=True,
        )
    # 写锁竞争时等待 5s，避免并发写入触发 SQLITE_BUSY 报错
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def default_migrations_dir() -> Path:
    """默认迁移目录（仓库根锚定，与 CWD 无关）；供启动自检日志 / 外部探针引用。"""
    return _DEFAULT_MIGRATIONS_DIR


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS _migrations (
            filename   TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL,
            checksum   TEXT
        )
        """
    )
    conn.commit()


def _list_sql_files(migrations_dir: Path) -> list[Path]:
    if not migrations_dir.exists():
        return []
    return sorted(p for p in migrations_dir.glob("*.sql") if p.is_file())


def _already_applied(conn: sqlite3.Connection) -> set[str]:
    # 唯一调用方 apply_migrations 必定先跑 _ensure_migrations_table(conn)，
    # 故本 SELECT 不会因「表不存在」抛 OperationalError——原 try/except 正是为
    # 该情形设的防御（真出错时会被读成「0 条已应用」⇒ 全部迁移重跑），
    # 2026-09-27 A-003 删除。出错就让它抛。
    rows = conn.execute("SELECT filename FROM _migrations").fetchall()
    return {r["filename"] for r in rows}


def apply_migrations(
    db_path: Path | str,
    migrations_dir: Path | str | None = None,
) -> dict[str, list[str] | int]:
    """执行 migrations_dir 下所有 SQL，按文件名升序，幂等。

    返回 ``{"applied": [...], "skipped": [...], "tables": <int>}``。
    """
    db_path = Path(db_path)
    if migrations_dir is None:
        migrations_dir = _DEFAULT_MIGRATIONS_DIR
    migrations_dir = Path(migrations_dir)

    sql_files = _list_sql_files(migrations_dir)
    if not sql_files:
        # 目录缺失 / 0 个 SQL：不抛（嵌入式与空目录用法合法），但必须留痕——
        # 「静默 0 迁移」正是 M4 事故的形态（health 仍 ok、业务端点全 500）。
        log.warning(
            "apply_migrations: no *.sql under %s (dir_exists=%s); 0 migrations applied",
            migrations_dir,
            migrations_dir.exists(),
        )

    conn = get_connection(db_path)
    try:
        _ensure_migrations_table(conn)
        already = _already_applied(conn)

        applied: list[str] = []
        skipped: list[str] = []
        for sql_file in sql_files:
            name = sql_file.name
            if name in already:
                skipped.append(name)
                continue
            script = sql_file.read_text(encoding="utf-8")
            # executescript 内部会做隐式提交；外层无需再 commit
            conn.executescript(script)
            # 表单记录（applied_at 用 UTC ISO-8601）
            from datetime import datetime, timezone

            ts = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO _migrations(filename, applied_at) VALUES (?, ?)",
                (name, ts),
            )
            conn.commit()
            applied.append(name)

        tables = conn.execute(_TABLE_COUNT_QUERY).fetchone()[0]
        return {"applied": applied, "skipped": skipped, "tables": tables}
    finally:
        conn.close()


def count_tables(db_path: Path | str) -> int:
    """查询总表数量（不含 sqlite_* 与 chapter_fts% 影子表，含 _migrations）。

    业务表数随迁移链增长，调用方按需减一得不含 ``_migrations`` 的业务表数。
    **本 docstring 不写死具体张数**（2026-09-27 A-009）：表数在每次迁移后即漂移，
    写死的数字必然过期，且与 AGENTS.md §六一致性矩阵的记载分属两处、更难同步。
    权威口径以 ``database/migrations/`` 实际迁移链为准，测试断言见
    ``tests/unit/test_migrations.py`` 与 ``tests/integration/test_health.py``。
    """
    conn = get_connection(db_path)
    try:
        return conn.execute(_TABLE_COUNT_QUERY).fetchone()[0]
    finally:
        conn.close()
