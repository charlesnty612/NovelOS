"""Context Engine 装配缓存——进程内缓存 + 指纹键
（拆分自 builders.py，2026-09-06 审查批次三；导出面见 builders.py）。"""

from __future__ import annotations

import copy
import hashlib
import json
import threading as _threading
from typing import Any

_CACHE_MAX_SIZE = 256
# V2.0 Wave C P1-1：装配缓存键加入内容指纹维度。
# - director 键第 5 元 = sha256(plan_json 原文)[:16]（plan_json None → 'none'）；
# - writer 键第 5 元 = sha256(json.dumps(scene_plan, sort_keys=True))[:16]（None → 'none'）；
# - 内容指纹计算失败（不可序列化）→ 跳过缓存（直接走 uncached），避免脏命中。
# V3.9 批次 1.4：键再补齐「进 payload 的装配参数」维度——director 加 author_intent
# 指纹与 target_word_count（两者都进 payload），writer 加 target_word_count（决定
# chapter.target_word_count / word_band / 每 scene target_words）；键尾加命名空间标记
# 拆开 preview dry-run 与生产的键空间（见 ``_cache_namespace_tag``）。
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
