"""Context Engine 装配缓存——进程内缓存 + 指纹键
（拆分自 builders.py，2026-09-06 审查批次三；导出面见 builders.py）。"""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import threading as _threading
from pathlib import Path
from typing import Any

from packages.core.db import get_connection, is_missing_schema_error

_CACHE_MAX_SIZE = 256
# V2.0 Wave C P1-1：装配缓存键加入内容指纹维度。
# - director 键第 5 元 = sha256(plan_json 原文)[:16]（plan_json None → 'none'）；
# - writer 键第 5 元 = sha256(json.dumps(scene_plan, sort_keys=True))[:16]（None → 'none'）；
# - 内容指纹计算失败（不可序列化）→ 跳过缓存（直接走 uncached），避免脏命中。
# V3.9 批次 1.4：键再补齐「进 payload 的装配参数」维度——director 加 author_intent
# 指纹与 target_word_count（两者都进 payload），writer 加 target_word_count（决定
# chapter.target_word_count / word_band / 每 scene target_words）；键尾加命名空间标记
# 拆开 preview dry-run 与生产的键空间（见 ``_cache_namespace_tag``）。
# 2026-09-16 F-10 修复：writer 键同步补 ``author_intent`` 指纹（``intent_fp``，键尾
# 命名空间前）——writer payload 新增 ``author_intent`` 段（作者硬性要求）后，改意图
# 必须 miss（硬规则 2「凡进 payload 的装配参数必须入键」；与 director 键同形）。
# 2026-09-18（0029 项目写作圣经）：director / writer 键再各补一个 ``bible_fp``
# 维度（``projects.writing_bible`` 原文指纹）——圣经与运行期意图一起决定 payload 的
# ``author_intent.raw``（基线 + 增量拼接），两个来源各自独立，故各占一个键维度
# （与「项目级 wb_fp / 运行级 target_word_count 分列」同款）；缺该维度则改圣经后
# 同 state_version 脏命中旧装配。
# 2026-10-16（E1，外部报告「角色圣经写闺蜜、正文里却是业务员」）：director / writer
# 键再各补一个 ``chars_fp`` 维度（角色内容指纹，见 ``characters_fingerprint``）——
# ``characters.core_json`` 整份进 payload 的 ``character_state_excerpts.core_json``，
# 而 ``CharacterService.create / update``（角色页手动新增 / 改设定）**不写 story_state、
# 不推 state_version**（``packages/core/api/routers/characters.py`` 无 write_through），
# 故旧键里没有任何维度会因角色改动而变 ⇒ 同 state_version + 同 chapter_no 重跑该章
# 命中旧装配 ⇒ 正文沿用改动前的角色设定。与 0028 ``outline_json`` 逐字同形。
# 失效仍以 state_version + chapter_no 为主线；commit 完成后调
# ``_invalidate_cache_for_chapter`` 显式兜底（state_version 推进也会带走它）。
_assembly_cache: dict[tuple, dict[str, Any]] = {}
_cache_lock = _threading.Lock()

# 内容指纹长度（sha256 hexdigest 前 16 字符 = 64 bit；冲突概率可忽略）
_FINGERPRINT_LEN = 16
# 「不可序列化 / None」的指纹占位
_FINGERPRINT_NONE = "none"
# 「跳过缓存」的指纹占位（与 'none' 区分；调用方据此判走 uncached）
_FINGERPRINT_UNCACHED = "uncached"
# 「项目未绑定任何 skill」的指纹占位。
# ⚠️ 刻意**不是** ``_FINGERPRINT_NONE``（"none"）：skill 维度与题材包维度
# （``genre_pack_ref`` 的未绑定占位）同形，用 ``"__none__"`` ⇒ 两处的「未绑定」
# 在键里读起来一致，便于肉眼核对键形态（硬规则 2「主 JOIN / 降级路径 / 空态
# 三处键形态逐字同形」）。
_SKILL_FINGERPRINT_NONE = "__none__"

# preview dry-run（``preview_context``）的独立命名空间：默认口径是空意图 +
# 2200 字，与生产参数同值时也必须互不命中（前端挂载章节详情页即自动打预览，
# 若共用键空间会把空意图/默认字数写进生产条目，作者意图被静默丢弃）。
_PREVIEW_CACHE_NAMESPACE = "preview"


def _cache_namespace_tag(namespace: str | None) -> str:
    """把命名空间归一化为缓存键末元（生产 ``"ns:"``、预览 ``"ns:preview"``）。

    恒占一位（生产调用也追加），键形状统一；``"ns:"`` 前缀保证该元取值不会与
    role / mode / 指纹等既有维度混淆。
    """
    return f"ns:{namespace or ''}"


def _fingerprint_plan_json(plan_json_raw: Any) -> str:
    """计算 chapters.plan_json 原文的稳定指纹（sha256 前 16 字符）。

    - 输入是 DB 读出的原文 str（或已解析对象；统一按原文口径处理）。
    - 不可序列化 / 异常 → 返回 ``_FINGERPRINT_UNCACHED``，调用方据此跳过缓存。
    """
    try:
        if plan_json_raw is None:
            return _FINGERPRINT_NONE
        if isinstance(plan_json_raw, (dict, list)):
            normalized = json.dumps(plan_json_raw, sort_keys=True, ensure_ascii=False)
        else:
            normalized = str(plan_json_raw)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:_FINGERPRINT_LEN]
    except Exception:  # noqa: BLE001 —— 不可序列化时跳过缓存
        return _FINGERPRINT_UNCACHED


def _fingerprint_outline_json(outline_json_raw: Any) -> str:
    """计算 chapters.outline_json 原文的稳定指纹（sha256 前 16 字符）。

    outline_json（0028 策展大纲槽）进 director 装配 payload 的 ``chapter.outline``
    段 → 按装配缓存键纪律（AGENTS.md 硬规则 2「凡进 payload 的装配参数必须入键」）
    必须单列一个键维度；否则改大纲后同 state_version 下会脏命中旧装配。
    口径与 :func:`_fingerprint_plan_json` 逐字一致（原文 str / 已解析对象同款处理）。
    """
    return _fingerprint_plan_json(outline_json_raw)


# ---------------------------------------------------------------------------
# 角色内容指纹（2026-10-16 E1）——「改角色设定 / 手动新增角色」必须 miss 装配缓存
# ---------------------------------------------------------------------------
#
# **列集选择（本段是设计判断，不是实现细节）**
#
# 规则一句话：**指纹 = 该项目全部角色的「业务列」+ 各角色最新一条状态行；不含记账时间戳。**
#
# - **覆盖 ``_character_state_excerpts``（``builders_common/excerpts.py``）读取的每一列**，
#   外加两个「不直接进 payload 但决定 payload 形状」的列：
#   - ``aliases`` / ``inject_mode``：决定条目是 full / summary / suppressed。漏任一列，
#     改它时 payload 的**条目集合与形态**会变而键不变 ⇒ 脏命中（本次
#     ``test_e1_aliases_change_misses_cache`` / ``test_e1_inject_mode_change_misses_cache`` 钉住）。
#   - ``character_states`` 的最新 ``state_version`` / ``state_json``（LEFT JOIN）：进
#     payload 的 ``current_state`` / ``latest_state_version``。生产上角色状态由 observer
#     delta 推进且同时推 ``story_states.state_version``（常态已被键的主维度覆盖），
#     但手工写 character_states 不推 ⇒ 该列属「潜在脏命中面」，一并覆盖。
# - **额外纳入 ``who_knows``**：它不被 excerpt 读取，但同属角色的业务列（与
#   ``visibility`` 同属知识权限面）。纳入只多一次重装（免费），漏掉则要求另维护一份
#   「未被消费」手挑名单——那正是会随源码演进悄悄缩小的形状（判别面不得手挑）。
#   ⇒ **失效方向刻意不对称**：多算 = 一次重装；少算 = 静默的错正文。
# - **排除 ``created_at`` / ``updated_at``**：纯记账时间戳，不进 payload。纳入会让
#   「只 bump updated_at、内容一字未动」也 miss（payload 逐字相同，miss 是白付代价），
#   并让「拿 MAX(updated_at) 当指纹」这种假修通过全部判别。
# - **排除 ``project_id``**：它是查询条件本身，恒等于键首元。
#
# 上面四条排除/纳入都是**可执行的断言**（``tests/unit/test_e1_character_cache_key.py``）：
# 业务列全集由 ``PRAGMA table_info(characters)`` 推导，指纹覆盖面由本 SQL 推导，
# 且断言 excerpt SQL 的列集合 ⊆ 指纹 SQL 的列集合（两处都从源码文本推导，不手抄）。
#
# **指纹计算的位置**：不在 ``_peek_chapter_context``（``builders_common/common.py``，
# 本仓 Lead 独占）里，而在装配入口各自调用一次独立小查询。代价：命中路径 DB 连接
# 1 → 2（实测增量见 CHANGELOG）。若后续 Lead 愿意在 peek 的单条 JOIN 里带出
# ``characters_fp``，即可回到 1 连接——那需要 common.py 改动，不在本成员边界内。
#
# ⚠️ 残余边界（如实登记）：本 SQL 与 ``_character_state_excerpts`` 的 SQL 是**两份文本**，
# 由上面的「excerpt ⊆ fingerprint」断言看守同步；若将来 excerpt 改 JOIN 而断言被一并
# 改宽，守望即失效。

_CHARACTERS_FINGERPRINT_SQL = """
    SELECT c.character_id, c.name, c.role, c.core_json, c.visibility,
           c.aliases, c.inject_mode, c.who_knows,
           cs.state_version AS latest_state_version,
           cs.state_json   AS latest_state_json
    FROM characters c
    LEFT JOIN character_states cs
      ON cs.character_id = c.character_id
     AND cs.state_version = (
          SELECT MAX(state_version) FROM character_states WHERE character_id = c.character_id
     )
    WHERE c.project_id = ?
    ORDER BY c.character_id ASC
"""


def _fingerprint_characters(rows: Any) -> str:
    """计算角色内容指纹（sha256 前 16 字符），口径对齐 :func:`_fingerprint_plan_json`。

    - ``None``（读不到角色表 / 无项目行）→ ``_FINGERPRINT_NONE``；
    - ``[]``（项目确实没有角色）→ 空列表自身的稳定哈希，**与 None 区分**（两者落到
      同一占位符会让降级态与「空项目」共用键空间）；
    - 行是 ``tuple`` / ``list`` / ``sqlite3.Row``，一律转 list 后按 ``json.dumps(
      sort_keys=True, ensure_ascii=False)`` 计算 ⇒ 元素顺序即指纹的一部分（查询侧
      已用 ``ORDER BY c.character_id`` 定序）；
    - 不可序列化 → ``_FINGERPRINT_UNCACHED``，调用方据此跳过缓存（与同文件其余
      指纹函数逐字一致，绝不静默降级成某个稳定值——那正是脏命中）。
    """
    try:
        if rows is None:
            return _FINGERPRINT_NONE
        normalized_rows = [list(r) for r in rows]
        normalized = json.dumps(normalized_rows, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:_FINGERPRINT_LEN]
    except Exception:  # noqa: BLE001 —— 不可序列化时跳过缓存
        return _FINGERPRINT_UNCACHED


def _characters_fingerprint_conn(
    conn: sqlite3.Connection, project_id: str,
) -> str:
    """在**已持有连接**上算角色指纹（连接由调用方负责关闭）。"""
    try:
        rows = conn.execute(_CHARACTERS_FINGERPRINT_SQL, (project_id,)).fetchall()
    except sqlite3.OperationalError as exc:
        if not is_missing_schema_error(exc):
            raise
        return _FINGERPRINT_NONE
    return _fingerprint_characters(rows)


def characters_fingerprint(
    db_path: str | Path, project_id: str | None, *, conn: sqlite3.Connection | None = None,
) -> str:
    """读该项目全部角色行并算指纹（writer / director 两条缓存键共用这一个维度）。

    **降级范围**（与 2026-10-16 批次十的收窄口径同形）：仅「结构缺失」（缺表 / 缺列）
    返回 ``_FINGERPRINT_NONE``；``database is locked`` / ``disk I/O error`` / ``malformed``
    等运行时故障**上抛**——角色指纹是缓存键维度，静默归 None = 用「读不到角色」的键形态
    去命中「角色已改」的旧装配，正是本函数要修的脏命中形状。
    判别：``tests/unit/test_e1_character_cache_key.py``。

    ``conn``（2026-10-22 批次二十三，热路径优化）：**可选的已持有连接**——传入时
    复用该连接（不再自建连）；``None`` ⇒ 保持旧行为（自建连、用完即关）。
    ⚠️ 连接的**关闭责任在调用方**（本函数不关传入的连接）。
    """
    if not project_id:
        return _FINGERPRINT_NONE
    if conn is not None:
        return _characters_fingerprint_conn(conn, project_id)
    try:
        own = get_connection(db_path)
    except sqlite3.OperationalError as exc:
        if not is_missing_schema_error(exc):
            raise
        return _FINGERPRINT_NONE
    try:
        return _characters_fingerprint_conn(own, project_id)
    finally:
        own.close()


# ---------------------------------------------------------------------------
# 世界观三表 + reveal_policies 内容指纹（2026-10-21）——E1 角色指纹的同形状补齐
# ---------------------------------------------------------------------------
#
# **为什么必须补**：`_world_state_excerpts` 整份读 locations / factions /
# world_rules 进 payload；writer 侧还经 `_filter_hidden_by_reveal_policies` 按
# reveal_policies 裁剪实体。两者都是**装配输入**，却都无键维度 —— 而
# `packages/domain/**` 内没有任何 `story_states` 写入（手工 PATCH 设定、新加一条
# planned+reader 策略都不推 state_version），缓存失效又只在 chapter_commit 后按章
# 兜底 ⇒ **改设定 / 加知识权限策略后重跑同一章 ⇒ 命中旧装配**：前者正文沿用旧
# 设定，后者 HIDDEN 角色仍在 writer 上下文里（**剧透方向**，该拦的没拦）。
#
# **列集由 PRAGMA 推导，不手挑**（与 chars_fp 同源纪律）：业务列全集 −
# {project_id, created_at, updated_at}。时间戳进键会让「只 bump updated_at」白付
# 一次重装；project_id 是查询条件本身。
#
# **单表缺失不拖垮整个维度**：某张表不存在（极老库）时该表记 ``"missing"`` 占位，
# 其余表照常参与指纹——否则「缺一张表」会让整条维度恒 ``"none"``，把另外两张表的
# 判别力一起丢掉（这是 chars_fp 单表形态不会遇到的形状）。
_FINGERPRINT_ROW_EXCLUDED = frozenset({"project_id", "created_at", "updated_at"})

_WORLD_FINGERPRINT_TABLES: tuple[tuple[str, str], ...] = (
    ("locations", "location_id"),
    ("factions", "faction_id"),
    ("world_rules", "world_rule_id"),
)

_REVEAL_FINGERPRINT_TABLES: tuple[tuple[str, str], ...] = (
    ("reveal_policies", "policy_id"),
)

# 作者文风样例表（2026-10-22 批次十九）。列集同款由 PRAGMA 推导：
# 业务列全集 − {project_id, created_at, updated_at} ⇒ 保留
# {sample_id, title, content}——正是 writer 注入 ``author_style_samples.samples``
# 时读的那三列（``builders_common/summaries.py::_author_style_samples``）。
#
# ⚠️ **已知边界（2026-10-22 对抗审查 m1）**：payload 的**选样**是
# ``ORDER BY created_at DESC LIMIT 2`` ⇒ ``created_at`` 在语义上**也是装配输入**，
# 但本维度把它排除了。当前**不可达**：``author_style_samples`` 只有 INSERT / DELETE
# 端点，没有能改 ``created_at`` 的 UPDATE（删后重建会让 ``sample_id`` 与
# ``created_at`` 同时变 ⇒ 指纹照样变）。
# ⇒ **将来若新增「编辑样例」或「调整顺序」类端点，必须同批复审本维度**：
# 那时要么把 ``created_at`` 纳入指纹，要么证明选样不依赖它。
_STYLE_SAMPLE_FINGERPRINT_TABLES: tuple[tuple[str, str], ...] = (
    ("author_style_samples", "sample_id"),
)


# ``projects`` 表里**真正进装配 payload** 的三列（2026-10-22 批次二十一 P0-1-A）。
# ⚠️ **必须显式收窄**：``projects`` 的业务列全集含 ``writing_bible`` /
# ``word_band_json`` / ``fast_mode`` / ``genre_pack_id`` 等**已有独立键维度**的列
# ⇒ 若用「业务列全集」当指纹，那些列的改动会**重复**触发本维度（过度失效），
# 且本维度的语义会变得不可读（「项目元信息」实际含了字数带覆盖）。
# ⇒ 列集 = PRAGMA 推导后**显式收窄**到这三列（与 world_fingerprint 的列集纪律
# 同源，但**多一步收窄**——因为 projects 的「业务列」≠「装配消费的列」）。
_PROJECT_META_COLUMNS: tuple[str, ...] = ("name", "genre", "premise")


def _fingerprint_table_rows(
    conn: sqlite3.Connection, table: str, id_column: str, project_id: str,
    *, keep_columns: tuple[str, ...] | None = None,
) -> Any:
    """读一张表的业务列（列集 PRAGMA 推导）；表/列缺失 → ``"missing"`` 占位。

    ``keep_columns``：**显式收窄**的列集（None = 业务列全集 −
    {project_id, created_at, updated_at}）。收窄时仍以 PRAGMA 的列序为准
    （⇒ 指纹与列的物理顺序无关地稳定）。

    非「缺表/缺列」的运行时故障**上抛**（锁 / I/O 静默归 none = 缓存键维度失真）。
    """
    try:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    except sqlite3.OperationalError as exc:
        if not is_missing_schema_error(exc):
            raise
        return "missing"
    if not cols:
        return "missing"
    if keep_columns is not None:
        keep = [c for c in cols if c in keep_columns]
    else:
        keep = [c for c in cols if c not in _FINGERPRINT_ROW_EXCLUDED]
    if not keep:
        keep = [id_column]
    sql = (
        f"SELECT {', '.join(keep)} FROM {table} "
        f"WHERE project_id = ? ORDER BY {id_column} ASC"
    )
    try:
        rows = conn.execute(sql, (project_id,)).fetchall()
    except sqlite3.OperationalError as exc:
        if not is_missing_schema_error(exc):
            raise
        return "missing"
    return [list(r) for r in rows]


def _hash_snapshot(snapshot: Any) -> str:
    """把一个「行集快照」哈希成稳定指纹（sha256 前 16）。

    不可序列化 → ``_FINGERPRINT_UNCACHED``（调用方据此跳过缓存；绝不静默落到
    某个稳定值——那正是脏命中）。
    """
    try:
        normalized = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:_FINGERPRINT_LEN]
    except Exception:  # noqa: BLE001 —— 不可序列化时跳过缓存
        return _FINGERPRINT_UNCACHED


def _fingerprint_tables_conn(
    conn: sqlite3.Connection, project_id: str, tables: tuple[tuple[str, str], ...],
) -> str:
    """在**已持有连接**上算多表指纹（连接的关闭责任在调用方）。"""
    snapshot = [
        [table, _fingerprint_table_rows(conn, table, id_column, project_id)]
        for table, id_column in tables
    ]
    return _hash_snapshot(snapshot)


def _fingerprint_tables(
    db_path: str | Path, project_id: str | None, tables: tuple[tuple[str, str], ...],
    *, conn: sqlite3.Connection | None = None,
) -> str:
    """多表内容指纹：``[[表名, 行集], ...]`` 的稳定哈希（sha256 前 16）。

    - 无 project_id → ``_FINGERPRINT_NONE``；连接打不开 / 缺表 → ``"missing"`` 占位；
    - 不可序列化 → ``_FINGERPRINT_UNCACHED``，调用方据此跳过缓存（绝不静默落到某个
      稳定值——那正是脏命中）。

    ``conn``（2026-10-22 批次二十三，热路径优化）：**可选的已持有连接**——传入时复用
    该连接（不再自建连）；``None`` ⇒ 保持旧行为（自建连、用完即关）。
    ⚠️ 连接的**关闭责任在调用方**。
    """
    if not project_id:
        return _FINGERPRINT_NONE
    if conn is not None:
        return _fingerprint_tables_conn(conn, project_id, tables)
    try:
        own = get_connection(db_path)
    except sqlite3.OperationalError as exc:
        if not is_missing_schema_error(exc):
            raise
        return _FINGERPRINT_NONE
    try:
        return _fingerprint_tables_conn(own, project_id, tables)
    finally:
        own.close()


def with_fingerprint_conn(fn):
    """装饰器：装配器**一条连接**贯穿 peek + 全部指纹（2026-10-22 批次二十三 · 热路径）。

    - 打开连接 → 以 ``_conn=`` 关键字注入被装饰函数 → 调用 → **finally 关闭**；
    - 建连失败 ⇒ **不注入**（交回函数自己按旧口径降级：peek 走全空态、指纹各自
      返回 ``_FINGERPRINT_NONE``）⇒ **失败语义与改造前逐字相同**；
    - 连接**不跨调用缓存** ⇒ 无 WAL 快照滞留、无跨线程共享（每次调用自开自关）。

    ⚠️ **为什么用装饰器、而不是在装配函数体里 try/finally**：这条连接要贯穿
    「peek + 五个指纹」，而它们在函数体里**不连续**（中间夹着 intent / bible /
    genre_pack 等纯计算）⇒ 合并成一条连接就得把整段**缩进一层** ⇒ 而那整段里坐着
    **既有突变规格的锚点原文**（``e1_character_cache_key.py::E1-1`` 的两行键元组 +
    它的 cacheable 两行守卫、``l7_skill_cache_key.py::SKILL-KEY-2`` 的键元组行、
    ``q1_style_sample_key.py::STYLE-KEY-1`` 的键元组行）⇒ 缩进会**打断既有规格**
    （AGENTS 坑区「改一个模块会打断既有突变规格的锚点 ⇒ 执行器 [ABORT]」）。
    ⇒ 装饰器让**函数体一字不改**（锚点原文逐字存活）。
    """
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if kwargs.get("_conn") is not None:
            return fn(*args, **kwargs)
        db_path = kwargs.get("db_path")
        if db_path is None and args:
            db_path = args[0]
        try:
            conn = get_connection(db_path)
        except Exception:  # noqa: BLE001 —— 建连失败 ⇒ 交回函数自己降级（旧行为）
            return fn(*args, **kwargs)
        try:
            return fn(*args, _conn=conn, **kwargs)
        finally:
            conn.close()

    return wrapper


def world_fingerprint(
    db_path: str | Path, project_id: str | None, *, conn: sqlite3.Connection | None = None,
) -> str:
    """``locations`` / ``factions`` / ``world_rules`` 三表内容指纹（writer + director 共用）。

    判别：突变规格 ``tests/mutation_specs/e1_character_cache_key.py``
    （``E1-1`` 撤掉 ``world_fp`` 维度 ⇒ 改任一表键不变 ⇒ 必红）。
    """
    return _fingerprint_tables(db_path, project_id, _WORLD_FINGERPRINT_TABLES, conn=conn)


def reveal_policies_fingerprint(
    db_path: str | Path, project_id: str | None, *, conn: sqlite3.Connection | None = None,
) -> str:
    """``reveal_policies`` 内容指纹（writer 侧按它裁剪 HIDDEN 实体，见 P1-4）。

    判别：突变规格 ``tests/mutation_specs/e1_character_cache_key.py``
    （``E1-2`` 撤掉 ``reveal_fp`` 维度 ⇒ 加一条 planned+reader 策略键不变 ⇒ 必红，
    否则 HIDDEN 角色剧透）。
    """
    return _fingerprint_tables(db_path, project_id, _REVEAL_FINGERPRINT_TABLES, conn=conn)


def author_style_samples_fingerprint(
    db_path: str | Path, project_id: str | None, *, conn: sqlite3.Connection | None = None,
) -> str:
    """``author_style_samples`` 内容指纹（作者文风样例，writer 消费）。

    **为什么必须补**（硬规则 2「凡进 payload 的装配参数必须入键」的同形状第 4 次）：
    writer 装配把该项目最近 ≤2 篇「作者本人散文样例」注入 payload 的
    ``author_style_samples.samples`` 段（``_build_writer_input_uncached``），
    而作者新增 / 删除样例走 ``api/routers/author_style_samples.py`` 的
    INSERT / DELETE —— **只写该表，不推 ``story_states.state_version``**
    ⇒ 旧键里没有任何维度会因样例改动而变 ⇒ 同 ``state_version`` +
    同 ``chapter_no`` 重跑该章**命中旧装配** ⇒ 正文不按作者文风写
    （且 ``style_constraints.genre_style`` 的 ``priority`` 标记不同步），
    **全程零告警**。

    与 ``world_fingerprint`` / ``reveal_policies_fingerprint`` 逐字同形：
    复用 :func:`_fingerprint_tables` + :func:`_fingerprint_table_rows`，
    **列集由 PRAGMA 推导、不手挑**（见本文件「列集由 PRAGMA 推导」纪律段）。

    口径边界（如实登记）：
    - 指纹覆盖**该项目的全部样例行**，而 payload 只取**最近 ≤2 篇**
      （``ORDER BY created_at DESC, sample_id DESC LIMIT 2``）⇒ 指纹是
      payload 输入的**超集**。这是**刻意不对称**：多算 = 多一次重装（免费），
      少算 = 静默的错正文（AGENTS 坑区「失效方向刻意不对称」先例）。
    - ``created_at`` / ``updated_at`` 不进指纹（纯记账时间戳）；``content``
      进指纹 ⇒ 即便将来加「改样例内容」的端点，改内容也必然 miss。
    - 缺表（迁移 0008 未跑）→ 该表记 ``"missing"`` 占位，不拖垮整条维度。

    判别：突变规格 ``tests/mutation_specs/q1_style_sample_key.py``
    （``STYLE-KEY-1`` 撤掉 ``style_fp`` 维度 ⇒ 加/删样例后键不变 ⇒ 必红）。
    """
    return _fingerprint_tables(db_path, project_id, _STYLE_SAMPLE_FINGERPRINT_TABLES, conn=conn)


def project_meta_fingerprint(
    db_path: str | Path, project_id: str | None, *, conn: sqlite3.Connection | None = None,
) -> str:
    """``projects`` 的 **name / genre / premise** 三列内容指纹（2026-10-22 批次二十一 P0-1-A）。

    **为什么必须补**（硬规则 2「凡进 payload 的装配参数必须入键」的同形状**第 5 次**）：
    director 装配把 ``project["name"]`` / ``project.get("genre")`` /
    ``project.get("premise")`` 写进 payload 的 ``project`` 段
    （``director_input.py`` 的 ``_build_director_input_uncached``），
    而这三个字段**被 prompt 契约显式承诺**给模型
    （``docs/agents/prompts/director_planner-v4.md`` 的 §5：
    ``"project": { "project_id", "name", "genre", "premise" }``）。
    作者改项目前提走 ``PATCH /api/projects/{id}`` ⇒
    ``packages/domain/project/service.py`` 的 ``UPDATE projects`` **只 commit()，
    不推 ``story_states.state_version``** ⇒ 旧键里没有任何维度会因它而变 ⇒
    同 ``state_version`` + 同 ``chapter_no`` 重跑该章**命中旧装配** ⇒
    **planner 拿着改动前的 premise 排计划，零告警**。

    列集：**PRAGMA 推导 + 显式收窄**到 ``_PROJECT_META_COLUMNS``
    （name / genre / premise）——``projects`` 的业务列全集里还有
    ``writing_bible`` / ``word_band_json`` / ``genre_pack_id`` 等**已有独立键维度**
    的列，不收窄就会与那些维度重复（过度失效）且语义不可读。

    判别：突变规格 ``tests/mutation_specs/q1_meta_title_key.py``
    （``META-KEY-1`` 撤掉 ``project_meta_fp`` 维度 ⇒ 改 premise 后键不变 ⇒ 必红）。

    ``conn``（2026-10-22 批次二十三，热路径优化）：**可选的已持有连接**——传入时复用
    该连接（不再自建连）；``None`` ⇒ 保持旧行为（自建连、用完即关）。
    """
    if not project_id:
        return _FINGERPRINT_NONE
    if conn is not None:
        rows = _fingerprint_table_rows(
            conn, "projects", "project_id", project_id,
            keep_columns=_PROJECT_META_COLUMNS,
        )
        return _hash_snapshot([["projects", rows]])
    try:
        own = get_connection(db_path)
    except sqlite3.OperationalError as exc:
        if not is_missing_schema_error(exc):
            raise
        return _FINGERPRINT_NONE
    try:
        rows = _fingerprint_table_rows(
            own, "projects", "project_id", project_id,
            keep_columns=_PROJECT_META_COLUMNS,
        )
    finally:
        own.close()
    return _hash_snapshot([["projects", rows]])


def _fingerprint_chapter_title(conn: sqlite3.Connection, chapter_id: str) -> Any:
    """本章 ``title`` 一列（列集 PRAGMA 推导 + 显式收窄）；行/列缺失 → ``"missing"``。

    ⚠️ 与 :func:`_fingerprint_table_rows` 不同：这里按 **chapter_id** 取**单行**
    （不是「项目下全部行」）——装配消费的是**本章**的标题。
    """
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(chapters)").fetchall()]
    except sqlite3.OperationalError as exc:
        if not is_missing_schema_error(exc):
            raise
        return "missing"
    if "title" not in cols:
        return "missing"
    try:
        row = conn.execute(
            "SELECT title FROM chapters WHERE chapter_id = ?", (chapter_id,)
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if not is_missing_schema_error(exc):
            raise
        return "missing"
    if row is None:
        return "missing"
    return [row[0]]


def chapter_title_fingerprint(
    db_path: str | Path, chapter_id: str, *, conn: sqlite3.Connection | None = None,
) -> str:
    """``chapters.title``（**本章**）内容指纹（2026-10-22 批次二十一 P0-1-B）。

    **为什么必须补**（同形状第 5 次的另一个实例）：writer 装配把
    ``chapter.get("title")`` 写进 payload 的 ``chapter`` 段
    （``writer_input.py`` 的 ``_build_writer_input_uncached``），
    而章标题**被 prompt 契约显式承诺**给模型
    （``docs/agents/prompts/writer-v5.md`` 的 §5 与示例
    ``"chapter": {..., "title": "夜叩青石", ...}``）。
    作者改章标题走 ``PATCH /api/chapters/{id}`` ⇒
    ``packages/domain/chapter/service.py`` 的 ``UPDATE chapters`` 同样**只 commit()、
    不推 state_version** ⇒ 旧键里没有任何维度会因它而变 ⇒
    同 ``state_version`` + 同 ``chapter_no`` 重跑该章**命中旧装配** ⇒
    **正文按改动前的标题写，零告警**。

    判别：突变规格 ``tests/mutation_specs/q1_meta_title_key.py``
    （``TITLE-KEY-1`` 撤掉 ``title_fp`` 维度 ⇒ 改 title 后键不变 ⇒ 必红）。

    ``conn``（2026-10-22 批次二十三，热路径优化）：**可选的已持有连接**——传入时复用
    该连接（不再自建连）；``None`` ⇒ 保持旧行为（自建连、用完即关）。
    """
    if not chapter_id:
        return _FINGERPRINT_NONE
    if conn is not None:
        rows = _fingerprint_chapter_title(conn, chapter_id)
        return _hash_snapshot([["chapters.title", rows]])
    try:
        own = get_connection(db_path)
    except sqlite3.OperationalError as exc:
        if not is_missing_schema_error(exc):
            raise
        return _FINGERPRINT_NONE
    try:
        rows = _fingerprint_chapter_title(own, chapter_id)
    finally:
        own.close()
    return _hash_snapshot([["chapters.title", rows]])


# ---------------------------------------------------------------------------
# 台账 / 情节图 / 逾期阈值 内容指纹（2026-10-23 第二轮审查 M-1 / D-7）
# ---------------------------------------------------------------------------
#
# **为什么必须补**（硬规则 2「凡进 payload 的装配参数必须入键」的同形状**第 6/7 次**）：
# 第二轮审查实测抓到 **director 侧又缺 5 个维度**——伏笔台账（``hooks``）/ 叙事债务
# （``narrative_debts``）/ 情节图（``plot_events`` + ``branches``）/ 项目级逾期阈值
# （``projects.foreshadow_overdue_chapters``）/ **director 侧**章标题（``chapters.title``）。
# 它们各自进 director payload（``hook_ledger_excerpt`` / ``narrative_debt_excerpt`` /
# ``plot_graph_excerpt`` / ``open_foreshadow_list`` / ``chapter.title``），而它们的写路径
# （LedgerService / PlotService / ProjectService / ChapterService）**只 commit()、不推
# ``state_version``** ⇒ 改数据后重跑同一章**命中旧装配**，planner 拿着改动前的数据排计划，
# **零告警**。
#
# **列集纪律**：与既有 ``world_fingerprint`` / ``author_style_samples_fingerprint`` 同款——
# **PRAGMA 推导、不手挑**（业务列全集 − {project_id, created_at, updated_at}）。
# 指纹覆盖面是 payload 消费面的**超集**（台账视图只取其中几列）⇒ 刻意不对称：
# 多算 = 多一次重装（免费）；少算 = 静默的错计划（AGENTS 坑区「失效方向刻意不对称」）。
#
# **与 ``project_meta_fp`` 的分工**：``projects.foreshadow_overdue_chapters`` 是**项目级**
# 输入，但它**不**进 ``project_meta_fp``（那一维显式收窄到 name/genre/premise，见
# ``_PROJECT_META_COLUMNS``）⇒ 单独占一维 ``foreshadow_fp``：语义可读、失效可归因
# （改阈值不会与「改 premise」混在同一个维度里）。
#
# **判别**：``tests/unit/test_ctx_c_ledger_key.py``（5 场景 + 反向不变量）+
# 突变规格 ``tests/mutation_specs/l22_director_ledger_key.py``。
_HOOKS_FINGERPRINT_TABLES: tuple[tuple[str, str], ...] = (("hooks", "hook_id"),)
_DEBTS_FINGERPRINT_TABLES: tuple[tuple[str, str], ...] = (("narrative_debts", "debt_id"),)
# 情节图 = ``plot_events``（planned/recorded 事件）+ ``branches``（ACTIVE 未解决分支）
# —— 二者同进 payload 的 ``plot_graph_excerpt`` 段 ⇒ 合为一维（失效归因到「情节图」）。
_PLOT_FINGERPRINT_TABLES: tuple[tuple[str, str], ...] = (
    ("plot_events", "event_id"),
    ("branches", "branch_id"),
)
#: 项目级逾期阈值列（``projects`` 表）；单列一维（不进 ``project_meta_fp``，见上段）。
_FORESHADOW_THRESHOLD_COLUMN = "foreshadow_overdue_chapters"


def hooks_fingerprint(
    db_path: str | Path, project_id: str | None, *, conn: sqlite3.Connection | None = None,
) -> str:
    """``hooks``（伏笔台账）内容指纹（director 键用）。

    进 payload 的 ``hook_ledger_excerpt``（台账视图）与 ``open_foreshadow_list``
    （待核销视图）都读这张表 ⇒ 必须单列一维（否则改伏笔后同 state_version 命中旧装配）。
    """
    return _fingerprint_tables(db_path, project_id, _HOOKS_FINGERPRINT_TABLES, conn=conn)


def debts_fingerprint(
    db_path: str | Path, project_id: str | None, *, conn: sqlite3.Connection | None = None,
) -> str:
    """``narrative_debts``（叙事债务）内容指纹（director 键用）。"""
    return _fingerprint_tables(db_path, project_id, _DEBTS_FINGERPRINT_TABLES, conn=conn)


def plot_fingerprint(
    db_path: str | Path, project_id: str | None, *, conn: sqlite3.Connection | None = None,
) -> str:
    """``plot_events`` + ``branches`` 内容指纹（director 键用）。

    二者同进 payload 的 ``plot_graph_excerpt`` 段（事件 + ACTIVE 分支）⇒ 合为一维。
    ⚠️ ``branches`` 行的写路径在 ``packages/core/story_state/branches.py``（**不在**
    本次修复的文件所有权内）⇒ 该写路径的 ``state_version`` 推进见任务报告的
    「需要 Lead 协调」段；键维度已在本批补齐（脏命中面已封）。
    """
    return _fingerprint_tables(db_path, project_id, _PLOT_FINGERPRINT_TABLES, conn=conn)


def foreshadow_threshold_fingerprint(
    db_path: str | Path, project_id: str | None, *, conn: sqlite3.Connection | None = None,
) -> str:
    """``projects.foreshadow_overdue_chapters``（项目级逾期阈值）单列指纹。

    该阈值进 director payload 的 ``open_foreshadow_list``（``overdue`` 判定与排序），
    而 ``ProjectService.update`` 改它时**不推** ``state_version`` ⇒ 必须单列一维。

    口径：PRAGMA 推导 + 显式收窄到**这一列**（与 ``project_meta_fingerprint`` 同款
    ``keep_columns`` 收窄法）。极老库（迁移 0008 未跑）缺该列 ⇒ 收窄后回落到
    ``project_id`` 单列 ⇒ 指纹恒定 —— 与「阈值恒为 fallback 30」的有效输入一致
    （**不是**静默失真：那一维本来就该是常量）。
    """
    if not project_id:
        return _FINGERPRINT_NONE
    if conn is not None:
        rows = _fingerprint_table_rows(
            conn, "projects", "project_id", project_id,
            keep_columns=(_FORESHADOW_THRESHOLD_COLUMN,),
        )
        return _hash_snapshot([["projects.foreshadow_overdue_chapters", rows]])
    try:
        own = get_connection(db_path)
    except sqlite3.OperationalError as exc:
        if not is_missing_schema_error(exc):
            raise
        return _FINGERPRINT_NONE
    try:
        rows = _fingerprint_table_rows(
            own, "projects", "project_id", project_id,
            keep_columns=(_FORESHADOW_THRESHOLD_COLUMN,),
        )
    finally:
        own.close()
    return _hash_snapshot([["projects.foreshadow_overdue_chapters", rows]])


# ---------------------------------------------------------------------------
# 装配指纹的**连接复用**（2026-10-22 批次二十三 · 热路径优化）
# ---------------------------------------------------------------------------
#
# **为什么**：AGENTS 坑区登记「``chars_fp`` 单独一项就是 +4.05ms / +64%，且 85% 的
# 增量是**多开了一次连接**、不是多查了一次数据」。原先每个指纹函数**各自
# ``get_connection(...)``** ⇒ 一次装配要开 5~6 条连接，只为读几个 id 集合。
#
# **怎么做的**：六个指纹函数全部接受**可选的 ``conn``**（``None`` ⇒ 保持旧行为
# 自建连，**API 兼容**：既有调用方与测试零改动）；装配器用
# :func:`with_fingerprint_conn` **一条连接**贯穿 peek + 全部指纹。
#
# ⚠️ **连接生命周期**：本模块**不缓存连接**、不复用跨调用的连接 ⇒ 无 WAL 快照
# 滞留、无跨线程共享（每次调用自开自关，与改造前同生命周期）。
# --------------------------------------------------------------------------


def _fingerprint_scene_plan(scene_plan: Any) -> str:
    """计算 scene_plan 的稳定指纹（json.dumps sort_keys=True 的 sha256 前 16）。

    - None → ``_FINGERPRINT_NONE``；scene 是会话级信号——不同 scene 必须区分缓存。
    - 不可序列化 → ``_FINGERPRINT_UNCACHED``，调用方据此跳过缓存。
    """
    try:
        if scene_plan is None:
            return _FINGERPRINT_NONE
        normalized = json.dumps(scene_plan, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:_FINGERPRINT_LEN]
    except Exception:  # noqa: BLE001
        return _FINGERPRINT_UNCACHED


def _fingerprint_author_intent(author_intent: Any) -> str:
    """计算 author_intent 的稳定指纹（sha256 前 16 字符，与 plan_fp 同款风格）。

    director 与 writer 两个装配键共用（2026-09-16 F-10 修复后 writer 也消费
    author_intent——它进 payload ``author_intent`` 段 ⇒ 必须进键）。

    - ``None`` → ``_FINGERPRINT_NONE``；空串按原文（``""``）计算，与 None 区分；
    - 非 str → ``json.dumps(sort_keys=True, ensure_ascii=False)`` 后计算；
    - 不可序列化 → ``_FINGERPRINT_UNCACHED``，调用方据此跳过缓存。
    """
    try:
        if author_intent is None:
            return _FINGERPRINT_NONE
        normalized = (
            author_intent
            if isinstance(author_intent, str)
            else json.dumps(author_intent, sort_keys=True, ensure_ascii=False)
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:_FINGERPRINT_LEN]
    except Exception:  # noqa: BLE001 —— 不可序列化时跳过缓存
        return _FINGERPRINT_UNCACHED


def _fingerprint_writing_bible(writing_bible: Any) -> str:
    """计算 ``projects.writing_bible``（项目写作圣经）的稳定指纹（sha256 前 16 字符）。

    圣经（0029）与运行期 ``author_intent`` 一起决定装配 payload 的
    ``author_intent.raw`` 段 ⇒ 按装配缓存键纪律（AGENTS.md 硬规则 2「凡进 payload
    的装配参数必须入键」）必须**单列一个键维度**；否则改圣经后同
    ``state_version`` 下会脏命中旧装配（正是本条要修的「改了权威输入却复用旧装配」
    缺陷形状，与 outline_json 的 0028 同款）。

    与 :func:`_fingerprint_author_intent` 同款风格：``None`` → ``_FINGERPRINT_NONE``；
    非 str → ``json.dumps(sort_keys=True, ensure_ascii=False)`` 后计算；
    不可序列化 → ``_FINGERPRINT_UNCACHED``，调用方据此跳过缓存。
    """
    return _fingerprint_author_intent(writing_bible)


def _fingerprint_skills(skills_ref: Any) -> str:
    """计算项目绑定 skill 集合的稳定指纹（sha256 前 16 字符）。

    口径（设计钉死）：``sha256(json.dumps([{"skill_id": sid, "version": ver} for ...
    按 skill_id 排序], sort_keys=True, ensure_ascii=False))[:16]``。

    - **必须含 version**：payload 变更 ⇒ version 自增（PUT 改 payload 时）⇒ 只按 id
      算会脏命中（改了 skill 的 instructions 却命中改之前的旧装配）。这与
      ``genre_pack_ref``（``<pack_id>@<version>``）同款纪律。
    - 未绑定（``None`` / 空串 / 空集合）→ ``"__none__"``（与 ``genre_pack_ref`` 的
      未绑定占位**同形**，便于肉眼核对键形态）。
    - **顺序无关**：内部按 ``skill_id`` 排序 ⇒ 两条读取路径（主 JOIN 的
      ``group_concat`` 聚合 / 降级路径的逐项读）即便聚合顺序不同，也算出**同一个**
      指纹（硬规则 2「主 JOIN 与降级路径的键形态必须逐字同形」的落地方式）。
      ⚠️ **如实登记的边界**：绑定**顺序**（``project_skills.ord``）不进指纹——两个
      skill 的相对顺序对被注入方无语义（各自独立的通用手艺，不存在「后者覆盖前者」）。
      若将来引入「有序 skill 栈」（后一条覆盖前一条的写法），**必须**把 ``ord``
      进指纹，否则改顺序不 miss ⇒ 脏命中。
    - 串形态非法（``skills_ref`` 不是 str）→ ``_FINGERPRINT_UNCACHED``（调用方据此
      跳过缓存；绝不静默降级成某个稳定值——那正是脏命中）。

    输入串形态：``"<skill_id>:<version>|<skill_id>:<version>"``（由 peek 的
    ``group_concat(skill_id || ':' || version, '|')`` 产生）。``skill_id`` 的字符集
    被 :class:`packages.core.skill.model.SkillCreate` 限死（不含 ``:`` / ``|``），
    故切分无歧义。
    """
    if skills_ref is None or skills_ref == "":
        return _SKILL_FINGERPRINT_NONE
    if not isinstance(skills_ref, str):
        return _FINGERPRINT_UNCACHED
    try:
        entries: list[dict[str, Any]] = []
        for part in skills_ref.split("|"):
            if not part:
                continue
            skill_id, _, version = part.rpartition(":")
            if not skill_id:
                continue
            entries.append({"skill_id": skill_id, "version": int(version)})
        if not entries:
            return _SKILL_FINGERPRINT_NONE
        entries.sort(key=lambda e: e["skill_id"])
        normalized = json.dumps(entries, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:_FINGERPRINT_LEN]
    except Exception:  # noqa: BLE001 —— 不可序列化时跳过缓存
        return _FINGERPRINT_UNCACHED


def _cache_reset() -> None:
    """测试辅助：清空装配缓存。

    必须原地 ``clear()`` 而非重赋值新 dict：``builders`` 门面 re-export 的
    ``_assembly_cache`` 与外部测试持有的引用都指向本模块定义时的 dict 对象，
    ``global`` 重绑定会让那些引用读到旧容器（2026-09-06 拆分后实测踩坑）。
    """
    with _cache_lock:
        _assembly_cache.clear()


def _invalidate_cache_for_chapter(project_id: str, chapter_no: int) -> int:
    """显式失效指定 (project_id, chapter_no) 的所有 role 键。返回失效条数。

    V2.0 Wave C P1-1：缓存键为多元组，第 1 元 project_id、第 3 元索引是 chapter_no
    （键尾后续追加过 content 指纹与 word_band 指纹等元，均不影响此处索引口径）。
    chapter_commit 成功后兜底调用以避免脏命中。
    """
    global _assembly_cache
    removed = 0
    with _cache_lock:
        keys_to_drop = [
            k for k in _assembly_cache
            if k[0] == project_id and k[2] == chapter_no
        ]
        for k in keys_to_drop:
            del _assembly_cache[k]
            removed += 1
    return removed


def _cache_get(key: tuple) -> dict[str, Any] | None:
    """读缓存；命中返回**深拷贝**，未命中返回 None。

    V3.9 批次 1.4：调用方会就地改写装配结果（chapter_plan 改
    ``chapter.expected_role``、chapter_write 补 ``mode/draft_text/revision_note``、
    paged 装配改 ``context_mode`` 等），共享同一 dict 会让脏数据滞留缓存被后续
    命中读到。改为双向拷贝隔离（此处 + ``_cache_put``），payload 为纯 JSON 结构
    （≤ 百 KB 量级），deepcopy 开销可忽略。
    """
    with _cache_lock:
        cached = _assembly_cache.get(key)
        return copy.deepcopy(cached) if cached is not None else None


def _cache_put(key: tuple, value: dict[str, Any]) -> None:
    """写入缓存（存深拷贝，调用方后续就地改写不影响缓存）；
    超过上限按 dict 插入顺序淘汰最旧（dict 有序）。"""
    global _assembly_cache
    with _cache_lock:
        if len(_assembly_cache) >= _CACHE_MAX_SIZE:
            # 淘汰最早写入的键（Python 3.7+ dict 保插入顺序）
            try:
                oldest_key = next(iter(_assembly_cache))
                del _assembly_cache[oldest_key]
            except StopIteration:
                pass
        _assembly_cache[key] = copy.deepcopy(value)
