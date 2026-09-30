"""chapter_write · writer 段（2026-09-27 B-1 拆分自 pipeline.py）。

含 writer 节点的辅助：context_mode 解析 / 最新稿读取 / 改稿核销表解析 /
重试次数解析 / 尝试度量 / 最优尝试挑选 / 重写 payload 构造。
节点本体 _writer_node 因 monkeypatch 契约留在门面。
同包内搬移，逻辑逐字不变。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any

from packages.core.agent_runtime.revision_fidelity import REVISION_CHECKLIST_MARKER
from packages.core.agent_runtime.structured_output import strip_think_blocks
from packages.core.db import get_connection
from packages.core.quality.wordcount import classify_prose_length

_log = logging.getLogger(__name__)
def _resolve_writer_context_mode(ctx: dict[str, Any]) -> str:
    """解析 writer context_mode（V3.2 P2-1）。

    优先级：
    1. workflow 上下文 ``writer_context_mode`` 字段（显式传参 > 一切）；
    2. 环境变量 ``NOVELOS_WRITER_CONTEXT_MODE``（全局开关，便于回归测试一键切换）；
    3. 默认 ``"paged"``（V3.2 行为变更：writer 默认按 L0/L1/L2 分页注入）。

    返回值仅做白名单校验，非合法值回退默认 ``"paged"`` 并发出警告（不抛错——
    装配阶段抛错会让运行中的 writer workflow 失败，违反「上下文裁剪是性能
    优化、不应是阻断级」的初衷）。
    """
    raw = ctx.get("writer_context_mode")
    if raw is None:
        raw = os.environ.get("NOVELOS_WRITER_CONTEXT_MODE")
    if raw is None:
        return "paged"
    if raw in ("full", "paged"):
        return raw
    # 非法值：兜底 paged + 不抛错（仅开发期日志可见）
    _log.warning(
        "writer_context_mode=%r is invalid; falling back to 'paged'", raw,
    )
    return "paged"


def _latest_draft_text(db_path: str, chapter_id: str) -> str:
    """读该章最新 draft content（drafts 表，按 version DESC LIMIT 1）。

    无 draft 行或 content 为空 → 返回空串。DB 异常（缺失表/列）→ 返回空串，
    与既有"上下文缺失不阻断 writer run"口径一致。
    """
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT content FROM drafts WHERE chapter_id = ? "
            "ORDER BY version DESC LIMIT 1",
            (chapter_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return ""
    finally:
        conn.close()
    if row is None:
        return ""
    return row["content"] or ""


# 修订模式机读核销表（writer-v1.md §6.1 第 11 条）尾块标记。
# 约定：writer revise 模式输出 prose 末尾追加独占一行的
# "---REVISION-CHECKLIST---" 分隔行 + 一行 JSON 数组。管线按此分隔行切分，
# 前半 = 真正正文（落 drafts.content），后半 = 核销表（落 run 节点产出）。
# canonical 常量在 core（revision_fidelity，契约层剥尾块比对保真度也要用同一行），
# 此处别名引用防两份字面量漂移。
_REVISION_CHECKLIST_MARKER = REVISION_CHECKLIST_MARKER


def _parse_revision_checklist(prose: str) -> tuple[str, list[dict[str, Any]] | None]:
    """从 prose 末尾剥离 REVISION-CHECKLIST 尾块；返回 (clean_prose, checklist_or_None)。

    行为契约（与 writer-v1.md §6.1 第 11 条 + 测试断言对齐）：
    - 仅当 prose 含 `---REVISION-CHECKLIST---` 分隔行（独占一行）时进入切分逻辑；
      缺失 → 返回 ``(prose.rstrip(), None)``（不报错，revise 模式允许模型漏写）。
    - 切分后，分隔行后的剩余文本先 rstrip（剥掉模型可能留的尾部空白），
      再尝试 ``json.loads``：成功且为 list → 返回核销表；失败/非 list → ``None``（fail-soft）。
    - 前半（正文侧）rstrip 一次，去掉分隔行前的多余空行；不改变正文内容本身。

    调用方须依据 ``checklist is None`` 决定是否在 ctx 写 ``revision_checklist=None``
    + log warning（不阻断 writer run）。
    """
    if not isinstance(prose, str):
        return ("", None)
    if _REVISION_CHECKLIST_MARKER not in prose:
        return (prose.rstrip(), None)
    # 仅在分隔行**独占一行**时切分（避免误切正文内偶然出现的同名行；不过核销表
    # 约定就是独占一行，故首处命中即认定为契约边界）。
    head, _, tail = prose.partition(_REVISION_CHECKLIST_MARKER)
    checklist_text = tail.strip()
    if not checklist_text:
        # 分隔行后为空 → 模型把分隔行写出但忘了 JSON → 当作缺失，fail-soft
        return (head.rstrip(), None)
    try:
        parsed = json.loads(checklist_text)
    except (TypeError, ValueError):
        # 坏 JSON：分隔行仍要剥掉（它不属正文，留着会污染 drafts.content / word_count），
        # 但 checklist 为 None —— 调用方据此判「revise 契约违约」（2026-09-21 m5 前
        # 这里原样返回整段 prose，导致坏 JSON 的尾块连同行落稿）。
        return (head.rstrip(), None)
    if not isinstance(parsed, list):
        return (head.rstrip(), None)
    # 仅保留 dict 元素；非 dict 元素丢弃（不阻断）
    items: list[dict[str, Any]] = [it for it in parsed if isinstance(it, dict)]
    return (head.rstrip(), items)


# ---------------------------------------------------------------------------
# 生成期内字数闭环常量（P0-2，2026-09-18）
# ---------------------------------------------------------------------------
#
# 背景（实证 prj_2567bb8de642 / ch_92bac068ff0d，target 2500 / 带 2125~2875）：
# writer 首次产出 932 字（多次抽样 713 / 854 / 1096 / 1199 / 1434，普遍只有目标的
# 30~50%），节点照常完成，欠带一路滚到 review 才被 W-LEN 抓出；唯一能把字数推进
# 带内的路径是**审校触发的 revise 回路**，而那条路径同时产出了伤害（终稿章内重复
# 段落 9 处、重复率 20.3%，writer 自陈「draft_text 为上游既成稿…不做破坏性扩写」）。
# 结论：长度必须在**生成期**闭环，不得推给 review/revise。
#
# 为什么重写必须走 mode='write'：revise 被 writer-v3.md 规则 20 + §6.1 钉死为
# 「逐字保留未提及部分 / 净增 ≤ +5%」的局部修改——用它追 -65% 的缺口在数学上追不回，
# 且「逐字保留 + 小幅增补」正是成段重复的来源。
#
# 环境变量命名沿用 README 环境表风格（NOVELOS_<域>_<量>），解析优先级与
# control.py::_resolve_auto_revise_max / _resolve_writer_context_mode 同款：
# ctx 显式 > 环境变量 > 默认。
_LENGTH_RETRY_ENV = "NOVELOS_WRITER_LENGTH_RETRIES"
# 默认额外尝试上限：2（即最坏 3 次 writer 调用）。取值依据：实证首写普遍欠带
# 30~50%，一次整章重写通常足以落带；第二次为兜底。再往上加是纯成本（单次 writer
# 调用 ~1-2 分钟）而边际收益递减。
_LENGTH_RETRY_DEFAULT = 2


def _resolve_writer_length_retries(ctx: dict[str, Any]) -> int:
    """解析「writer 欠带时节点内整章重写」的最大额外尝试次数。

    优先级：``ctx["writer_length_retries"]`` > 环境变量
    ``NOVELOS_WRITER_LENGTH_RETRIES`` > 默认 ``2``。``0`` = 关闭（只写一次）。
    非法值（非整数）→ 默认 2 + 告警；负数 → 0。
    """
    raw = ctx.get("writer_length_retries")
    if raw is None:
        raw = os.environ.get(_LENGTH_RETRY_ENV)
    if raw is None:
        return _LENGTH_RETRY_DEFAULT
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        _log.warning(
            "writer_length_retries=%r is invalid; falling back to %d",
            raw, _LENGTH_RETRY_DEFAULT,
        )
        return _LENGTH_RETRY_DEFAULT
    return max(0, n)


def _measure_writer_attempt(
    *,
    attempt: int,
    mode: str,
    out: Any,
    target: int,
    band_cfg: dict[str, Any],
) -> dict[str, Any]:
    """把一次 writer 产出折算为「离字数带多远」的尝试记录（确定性、零 LLM）。

    - ``status`` / ``visible_chars`` 走权威口径 :func:`classify_prose_length`
      （与 length_check 同源）；revise 模式先剥离 ``---REVISION-CHECKLIST---`` 尾块
      再量——该尾块不落库、不属正文，计入会把欠带误判为在带。
    - ``distance``：带内 = 0；欠带 = 距下限缺口；超带 = 距上限超出量。用于选优。
    - 返回值全部为 JSON 原生类型（进 ctx → checkpoint_json / run 节点产出）。
    """
    prose = out.get("prose") if isinstance(out, dict) else None
    prose = strip_think_blocks(prose) if isinstance(prose, str) else ""
    if mode == "revise" and prose:
        prose, _ = _parse_revision_checklist(prose)
    report = classify_prose_length(prose, target, **band_cfg)
    status = report["status"]
    if status == "under":
        distance = report["band_low"] - report["visible_chars"]
    elif status == "over":
        distance = report["visible_chars"] - report["band_high"]
    else:
        distance = 0
    return {
        "attempt": attempt,
        "mode": mode,
        "visible_chars": report["visible_chars"],
        "target": target,
        "band_low": report["band_low"],
        "band_high": report["band_high"],
        "status": status,
        "distance": distance,
        "in_band": status == "in_band",
    }


def _best_writer_attempt(attempts: list[dict[str, Any]]) -> int:
    """选「离带最近」的尝试下标；带内优先，同距取更早的一次（确定性）。

    ``min`` 返回首个最小值 ⇒ 等距时保留先写的版本——不让一次等长的重写顶掉
    已通过审校约束（revise 语义 / 核销表）的产出。
    """
    return min(range(len(attempts)), key=lambda i: attempts[i]["distance"])


def _build_length_retry_payload(
    base_payload: dict[str, Any], previous: dict[str, Any],
) -> dict[str, Any]:
    """构造「欠带整章重写」轮的 writer 输入：``mode='write'`` + 显式扩写指令。

    - ``mode='write'`` + 清空 ``draft_text`` + 移除 ``revision_note``：绕开 revise 的
      「逐字保留未提及部分 / 净增 ≤ +5%」契约（见本模块常量区注释）；
    - 顶层注入 ``length_directive``（writer-v3.md §6 规则 22 消费）：携带上一版实测
      字数、带上下限、缺口字数与扩写纪律——只给正向量，不要求模型自报目标；
    - 只做浅拷贝 + 覆盖顶层键，不改 base_payload（后者可能是装配缓存返回的深拷贝，
      同一 run 内后续节点/断言仍读它）。
    """
    payload = dict(base_payload)
    payload["mode"] = "write"
    payload["draft_text"] = ""
    payload.pop("revision_note", None)
    shortfall = max(0, int(previous["band_low"]) - int(previous["visible_chars"]))
    payload["length_directive"] = {
        "kind": "expand_under_band",
        "previous_attempt": int(previous["attempt"]),
        "previous_attempt_mode": previous["mode"],
        "previous_visible_chars": int(previous["visible_chars"]),
        "shortfall_chars": shortfall,
        "target_word_count": int(previous["target"]),
        "band_low": int(previous["band_low"]),
        "band_high": int(previous["band_high"]),
        "instruction": (
            f"上一版正文实测 {int(previous['visible_chars'])} 字，低于本章字数带下限 "
            f"{int(previous['band_low'])} 字（差 {shortfall} 字）。本轮按 write 模式"
            "**整章重写**（不是局部修改、不是上一版的续写、不复述上一版任何段落）："
            f"输出完整本章正文，净字数落在 {int(previous['band_low'])}~"
            f"{int(previous['band_high'])} 字之间。扩写靠内容——补齐 scene_plan 中"
            "尚未展开的 slot、补足场景的环境与人物动作细节、把概述式叙述展开为可感知的"
            "场面；禁止靠注水、重复意象或重复句式凑字数。"
        ),
    }
    return payload


