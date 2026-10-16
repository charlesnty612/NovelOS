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


def characters_fingerprint(db_path: str | Path, project_id: str | None) -> str:
    """读该项目全部角色行并算指纹（writer / director 两条缓存键共用这一个维度）。

    **降级范围**（与 2026-10-16 批次十的收窄口径同形）：仅「结构缺失」（缺表 / 缺列）
    返回 ``_FINGERPRINT_NONE``；``database is locked`` / ``disk I/O error`` / ``malformed``
    等运行时故障**上抛**——角色指纹是缓存键维度，静默归 None = 用「读不到角色」的键形态
    去命中「角色已改」的旧装配，正是本函数要修的脏命中形状。
    判别：``tests/unit/test_e1_character_cache_key.py``。
    """
    if not project_id:
        return _FINGERPRINT_NONE
    try:
        conn = get_connection(db_path)
    except sqlite3.OperationalError as exc:
        if not is_missing_schema_error(exc):
            raise
        return _FINGERPRINT_NONE
    try:
        rows = conn.execute(_CHARACTERS_FINGERPRINT_SQL, (project_id,)).fetchall()
    except sqlite3.OperationalError as exc:
        if not is_missing_schema_error(exc):
            raise
        return _FINGERPRINT_NONE
    finally:
        conn.close()
    return _fingerprint_characters(rows)


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
