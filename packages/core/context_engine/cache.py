"""Context Engine 装配缓存——进程内缓存 + 指纹键
（拆分自 builders.py，2026-09-06 审查批次三；导出面见 builders.py）。"""

from __future__ import annotations

import hashlib
import json
import threading as _threading
from typing import Any

_CACHE_MAX_SIZE = 256
# V2.0 Wave C P1-1：装配缓存键加入内容指纹维度。
# - director 键第 5 元 = sha256(plan_json 原文)[:16]（plan_json None → 'none'）；
# - writer 键第 5 元 = sha256(json.dumps(scene_plan, sort_keys=True))[:16]（None → 'none'）；
# - 内容指纹计算失败（不可序列化）→ 跳过缓存（直接走 uncached），避免脏命中。
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
    with _cache_lock:
        return _assembly_cache.get(key)


def _cache_put(key: tuple, value: dict[str, Any]) -> None:
    """写入缓存；超过上限按 dict 插入顺序淘汰最旧（dict 有序）。"""
    global _assembly_cache
    with _cache_lock:
        if len(_assembly_cache) >= _CACHE_MAX_SIZE:
            # 淘汰最早写入的键（Python 3.7+ dict 保插入顺序）
            try:
                oldest_key = next(iter(_assembly_cache))
                del _assembly_cache[oldest_key]
            except StopIteration:
                pass
        _assembly_cache[key] = value
