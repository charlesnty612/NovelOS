# -*- coding: utf-8 -*-
"""produce_chapters —— 「把这些章节推到 COMMITTED」的一等生产入口（CLI，默认 dry-run）。

## 为什么需要它

此前每一本书都是靠**手写的一次性 HTTP 驱动**跑出来的（``_refs/arc1_driver.py`` …
``_refs/arc6_driver.py``，每个都是上一个的进化版），累计的排障经验从未进仓。本脚本
把这套闭环收进仓库：对每个章节跑 ``plan → write → review（人工节点 PAUSE）→ 批准 →
commit（可能 PAUSE 在风控门）→ 批准``，外加定向改稿轮。

驱动必须自己处理四道**已知会咬人**的坎（每条都来自实机事故，见本仓 CHANGELOG 与
AGENTS.md 坑区；驱动源码在软件仓外的 ``_refs/arc*_driver.py``）：

1. **409 竞态**：章节行在 workflow **内部**先翻状态、run 行后落终态，两者之间有窗口期；
   窗口期内 start/commit 会被 ``already has an active workflow run`` 拒收。故：
   - 所有 start 走「有界退避重试」，只把**已知瞬态** 409 当可重试（按 detail 判别，
     别的 409 立即硬停——否则就是「重试到天荒地老」）；
   - commit 之前先等该章**没有** RUNNING/PENDING run（``wait_no_active_run`` 轮询）。
2. **resume 409 会静默杀死进程**：对已不在 PAUSED 的 run 发 resume 会 409/404，朴素的
   ``raise_for_status()`` 客户端会抛异常退出（实机：弧停了 30 分钟没人发现）。故本脚本的
   HTTP 层**任何调用都不抛异常**（网络错 → status=0），resume 一律「尽力而为」，
   失败由后续轮询兜底。
3. **review 的人工节点**（``author_review``）PAUSE 后要读报告做判定：无 error ⇒ 批准；
   只有**可定向改稿**的规则（见 :data:`REVISABLE_RULE_IDS`）⇒ 驳回改稿；
   其余（未知 rule_id / 无 rule_id / 不可改的规则）⇒ **停下来报告**，不猜。
4. **改稿子 run 不在响应里**：驳回改稿后触发的是服务端 daemon 回路，其 write / review
   子 run 不会出现在 resume 的响应中——只能轮询 ``GET /projects/{pid}/runs`` 找
   **更新的、PAUSED 的 chapter-review run**；**不要**盯着 chapter.status 等（章状态在
   改稿轮不翻转，历史驱动在这里傻等超时）。

## 不静默批准坏章（本脚本存在的理由）

批准之前，脚本**直接**从最新草稿算一遍**章内重复率**（13 字 shingle，重复 shingle 数 /
shingle 总数，去空白归一化——与 ``packages/core/quality/ai_trace.py:intra_chapter_repetition``
同口径，但本地实现，不受该包并发改动影响），超过 ``--repetition-threshold``（默认 8%）
**拒绝批准**并把比值报出来。自动批准重复文本的驱动，比没有驱动更糟。

## 用法

::

    # 体检：只报告会对哪些章做什么，不发起任何 run（默认模式）
    python scripts/produce_chapters.py --project prj_xxxx --chapters 1-3,7
    python scripts/produce_chapters.py --project prj_xxxx --dry-run

    # 生产：把第 1~3 章与第 7 章推到 COMMITTED（已 COMMITTED 的自动跳过）
    python scripts/produce_chapters.py --project prj_xxxx --chapters 1-3,7 --apply

    # 机器可读摘要（stdout 仅 JSON；人类可读日志走 stderr）
    python scripts/produce_chapters.py --project prj_xxxx --chapters 1-3,7 --apply --json

    # 指定作者意图 / 目标字数 / 门禁模式
    python scripts/produce_chapters.py --project prj_xxxx --apply \
        --author-intent "单章可见字数 2200~2800" --target-word-count 2500 \
        --quality-gate-mode enforce

**本脚本只走 HTTP，不直连 SQLite**（因此没有 ``--db`` 参数）：事实源是**服务端**的库，
客户端另开一个连接只会制造第二写者。``--base-url`` 缺省由
``packages.core.config.get_settings()`` 的 ``api_host`` / ``api_port`` 拼出（与其他
scripts 一样从同一份配置读，不硬编码端口）。

退出码：``0`` = 全部完成（含「已 COMMITTED 被跳过」）；``1`` = 参数 / 配置错误
（选择非法、服务不可达、project 不存在）；``2`` = 某章失败、整轮停止（failure 见摘要）。

## 已知边界（不做的部分）

- **不做 gate_override 的自动填写**：``confirm`` 档门禁（``AI-BEAT-REPEAT``，以及
  ``RULE_STYLE_REPETITION_TRIGRAM`` 超确认阈值 0.25 那一档——都是「不许静默通过、
  必须显式接受并写明理由」的规则）命中时，
  本脚本一律**硬停并报告**（:func:`is_confirm_tier_failure`），绝不重试、绝不代填声明。
  「接受」是作者的判断，不是驱动的判断。
- **不重产已 COMMITTED 的章**：``COMMITTED`` / ``RELEASED`` 直接跳过（重产需要先回退章
  状态，那是另一件事，不在本入口的权限范围内）。
- **不自动改标题 / 不写策展大纲**：大纲走 ``scripts/open_volume.py``，标题走
  ``PATCH /api/chapters/{id}``。
- **不把「占比 / 频率」类指标当处方**（F-19）：``AI-DIALOGUE-LOW`` 之类可优化指标
  不进 :data:`REVISABLE_RULE_IDS`；改稿意见只覆盖确定性算子与字数带。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.config import get_settings  # noqa: E402

# --- 退出码（与 open_volume.py / content_sync.py 同口径）--------------------------

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_CHAPTER_FAILED = 2

# --- 算法常量 -------------------------------------------------------------------

# 章内重复率的 shingle 窗口（与 packages/core/quality/ai_trace.py:WINDOW 同口径）。
SHINGLE_WINDOW = 13
# 批准前的章内重复率上限（超过即拒绝批准）。8% 是任务书给死的口径。
DEFAULT_REPETITION_THRESHOLD = 0.08

# --- 工作流 / 人工节点契约 -------------------------------------------------------

# 章节工作流四级（按序）。
STAGE_PLAN = "plan"
STAGE_WRITE = "write"
STAGE_REVIEW = "review"
STAGE_COMMIT = "commit"
STAGES = (STAGE_PLAN, STAGE_WRITE, STAGE_REVIEW, STAGE_COMMIT)
# workflow_name（GET /projects/{pid}/runs 返回）→ 阶段短名。
WORKFLOW_NAME_TO_STAGE = {
    "chapter-plan": STAGE_PLAN,
    "chapter-write": STAGE_WRITE,
    "chapter-review": STAGE_REVIEW,
    "chapter-commit": STAGE_COMMIT,
}
# Human 节点的 pause_payload.stage（前缀判别；见 chapter_review / chapter_commit pipeline）。
PAUSE_STAGE_AUTHOR_REVIEW = "chapter-review"
PAUSE_STAGE_RISK_GATE = "chapter-commit.high_risk_approval"

# 终态（引擎状态机；PAUSED 是等人工决议的中间态）。
RUN_TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")

# 可**定向改稿**的评审 error 规则（= 确定性算子 + 字数带；驳回触发服务端 auto_revise 回路）。
#
# 【与 2026-09-18 服务端策略表的关系】服务端把「失败形状 → 修复动作」写成了
# ``packages/core/api/routers/workflows/repair_policy.py`` 的规则表（regenerate / revise /
# stop 三态）。本集合是该表 ``REVISE_RULE_IDS ∪ LENGTH_RULE_IDS`` 的**子集**——驱动只做
# 「驳回 + 交给服务端修」，因此它认的规则必须都是服务端能修的；子集关系由
# ``tests/unit/test_repair_policy.py::test_driver_revisable_set_agrees_with_server_policy_table``
# 钉住。两处刻意差异，都在保守侧：
#   - 重复类（``RULE_STYLE_REPETITION_TRIGRAM`` / ``AI-BEAT-REPEAT``）服务端走 regenerate
#     （重产），驱动不进本集合——驱动另有一条**更强**的闸门：草稿自身章内重复率超阈值就
#     拒绝批准（:func:`decide_review` 第 1 步），重复的判定权不该交给客户端。
#   - 服务端 ``stop`` 的规则（未知 / 连续性 / 设定类）驱动同样不认。
#
# 【F-19 纪律】凡「占比 / 频率」类指标一律不得进本集合：这类指标可被优化，模型凑数最省力
# 的手段就是灌水（实测：为凑对话占比让主角主动交代底牌；为凑字重写整章变灌水）。
# ``AI-DIALOGUE-LOW`` 已被明确移除（_refs/arc6_driver.py:REVISABLE 的 2026-09-17 撤回留痕）。
REVISABLE_RULE_IDS: frozenset[str] = frozenset({
    "W-LEN-DEVIATION",
    "GENRE-WORD-BAND-DEVIATION",
    "AI-PUNCT-ABUSE",
    "AI-SHORT-PARA",
    "AI-LONG-PARA",
    "AI-CONTRAST-PAIR",
    "AI-PRONOUN-PILE",
    "AI-EXPLAIN-TONE",
    "AI-FORBIDDEN-WORD",
})

# confirm 档（不许静默通过、必须显式接受）规则的已知成员——**前向兼容用**：同事正在把
# 「confirm 档」加进质量门禁（quality.issues.CONFIRM_RULES），本脚本不复制那份名单的
# 权威性，只在错误文本里**认出**它们并硬停，绝不重试、绝不代填 override。
CONFIRM_TIER_RULE_IDS: tuple[str, ...] = (
    "RULE_STYLE_REPETITION_TRIGRAM",
    "AI-BEAT-REPEAT",
)

# commit 失败里**允许重试**的瞬态形状（观察器偶发抖动：FK / 畸形 delta / 库锁）。
# 其余一律硬停——「重试到天荒地老」是本仓登记过的缺陷形状。
TRANSIENT_COMMIT_PATTERNS: tuple[str, ...] = (
    "database is locked",
    "integrity error",
    "FOREIGN KEY",
    "malformed delta",
)
# commit 阶段瞬态失败的最大重试次数（有界）。
COMMIT_RETRY_ROUNDS = 2

_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# 纯函数 1：章节选择解析
# ---------------------------------------------------------------------------


def parse_chapter_selection(spec: str | None) -> list[int] | None:
    """解析 ``--chapters`` / ``--only`` 的章号选择表达式。

    ``"1-3,7"`` → ``[1, 2, 3, 7]``（升序去重）；``None`` / 空串 → ``None``（= 全选）。
    非法输入抛 :class:`ValueError`（调用方转 exit 1）——**不做**「跳过坏项继续」的宽容解析，
    一个写错的章号意味着用户以为跑的是另一批章。
    """
    if spec is None:
        return None
    text = str(spec).strip()
    if not text:
        return None
    picked: list[int] = []
    for part in text.split(","):
        token = part.strip()
        if not token:
            raise ValueError(f"章节选择 {spec!r} 含空项（多余的逗号？）")
        if "-" in token:
            bits = [b.strip() for b in token.split("-")]
            if len(bits) != 2 or not all(b.isdigit() for b in bits):
                raise ValueError(f"章节区间 {token!r} 非法（形如 1-3）")
            low, high = int(bits[0]), int(bits[1])
            if low < 1 or high < low:
                raise ValueError(f"章节区间 {token!r} 非法（要求 1 ≤ 起点 ≤ 终点）")
            picked.extend(range(low, high + 1))
        else:
            if not token.isdigit():
                raise ValueError(f"章号 {token!r} 非法（必须是正整数）")
            number = int(token)
            if number < 1:
                raise ValueError(f"章号 {token!r} 非法（必须 ≥ 1）")
            picked.append(number)
    return sorted(set(picked))


def planned_stages(status: str) -> list[str]:
    """按章节当前状态给出本次要跑的阶段（可续跑口径，按状态而非「猜」）。

    - ``PLANNED`` ⇒ plan → write → review → commit；
    - ``DRAFTED`` ⇒ review → commit（已有草稿，不重复烧 token 重写）；
    - ``REVIEWED`` ⇒ commit；
    - ``COMMITTED`` / ``RELEASED`` ⇒ 空（已完成；重产是另一件事）。
    """
    if status in ("COMMITTED", "RELEASED"):
        return []
    if status == "REVIEWED":
        return [STAGE_COMMIT]
    if status == "DRAFTED":
        return [STAGE_REVIEW, STAGE_COMMIT]
    return list(STAGES)


# ---------------------------------------------------------------------------
# 纯函数 2：章内重复率
# ---------------------------------------------------------------------------


def shingle_repetition_ratio(text: str, *, window: int = SHINGLE_WINDOW) -> float:
    """章内重复率 = 「出现次数 ≥2 的 shingle 的总重复次数」/「shingle 总数」。

    口径与 ``packages/core/quality/ai_trace.py:intra_chapter_repetition`` 逐字一致
    （去空白归一化 → 13 字滑动窗口 → 重复计数 / 总数）；**本地实现**是刻意的：
    这个脚本的拒批闸门不能被该包（正在被并发修改）的改动牵着走。
    文本短于窗口 / 空 ⇒ ``0.0``。
    """
    normalized = _WS_RE.sub("", text or "")
    if window <= 0 or len(normalized) < window:
        return 0.0
    total = len(normalized) - window + 1
    counts: dict[str, int] = {}
    for i in range(total):
        shingle = normalized[i:i + window]
        counts[shingle] = counts.get(shingle, 0) + 1
    extras = sum(c - 1 for c in counts.values() if c > 1)
    return extras / total


# ---------------------------------------------------------------------------
# 纯函数 3：评审报告 → 决议
# ---------------------------------------------------------------------------


@dataclass
class ReviewDecision:
    """对一份评审报告的决议（``approve`` / ``revise`` / ``stop``）。"""

    kind: str
    reason: str
    note: str = ""
    rule_ids: list[str] = field(default_factory=list)
    repetition_ratio: float = 0.0
    word_count: int | None = None
    target_word_count: int | None = None
    error_count: int = 0
    warning_count: int = 0

    @property
    def approved(self) -> bool:
        return self.kind == "approve"


def _error_rule_ids(errors: list[Any]) -> tuple[list[str], list[Any]]:
    """抽 error 的 rule_id；返回 (rule_ids, 缺 rule_id 的条目)。"""
    rule_ids: list[str] = []
    missing: list[Any] = []
    for item in errors:
        if not isinstance(item, dict):
            missing.append(item)
            continue
        rule_id = str(item.get("rule_id") or "").strip()
        if not rule_id:
            missing.append(item)
        else:
            rule_ids.append(rule_id)
    return rule_ids, missing


def build_revision_note(errors: list[Any]) -> str:
    """按评审 errors 生成**定向**改稿意见（一轮覆盖全部可改项）。

    只认 :data:`REVISABLE_RULE_IDS` 的规则；字数带给**双向**意见（超限给上限口径、
    欠带给下限口径），防「4272 → 1853」乒乓球。
    """
    ids = {str(e.get("rule_id")) for e in errors if isinstance(e, dict)}
    parts: list[str] = []
    if "W-LEN-DEVIATION" in ids or "GENRE-WORD-BAND-DEVIATION" in ids:
        over = any(
            isinstance(e, dict) and (e.get("deviation_pct") or 0) > 0
            for e in errors
        )
        if over:
            parts.append("字数超限：压缩到字数带内，保留核心情节与爽点节拍，砍重复铺陈，"
                         "不得压到带下限以下。")
        else:
            parts.append("字数欠带：扩写到字数带内——补事件、补场面、补在场人的动作，"
                         "不要靠加对白凑字，保留情节骨架，不新开支线。")
    if "AI-PUNCT-ABUSE" in ids:
        parts.append("破折号 / 省略号滥用：把成对破折号插入语改写成逗号或冒号串联的完整句子。")
    if "AI-SHORT-PARA" in ids:
        parts.append("短句独立成段过多：把单句段落并进相邻段落；"
                     "不得为并段把句子改写成 40 字以上的复合长句。")
    if "AI-LONG-PARA" in ids:
        parts.append("段落过长：把超过 80 字的段落按句拆成 2~3 段。")
    if "AI-CONTRAST-PAIR" in ids:
        parts.append("减少「不是 A 而是 B」对举句式，改成直陈句。")
    if "AI-PRONOUN-PILE" in ids:
        parts.append("代词堆叠：把连续出现的「他」换成名字或身份指称。")
    if "AI-EXPLAIN-TONE" in ids:
        parts.append("去掉解释型收尾与旁白总结（替读者下结论的句子一律删）。")
    if "AI-FORBIDDEN-WORD" in ids:
        parts.append("删掉套话，用具体动作或细节替代。")
    return " ".join(parts) or "按评审意见定向修改，保留情节骨架。"


def decide_review(
    report: dict[str, Any] | None,
    draft_text: str,
    *,
    repetition_threshold: float = DEFAULT_REPETITION_THRESHOLD,
    revisable: frozenset[str] = REVISABLE_RULE_IDS,
) -> ReviewDecision:
    """把「评审报告 + 最新草稿正文」判成一个动作。

    判定顺序（**重复率优先**——它是对草稿本身的否决，先于对评审条目的分类）：

    1. 章内重复率 > ``repetition_threshold`` ⇒ ``stop``（拒绝批准重复文本）；
    2. ``errors`` 为空 ⇒ ``approve``；
    3. errors 全部落在 ``revisable`` 内 ⇒ ``revise``（附定向意见）；
    4. 其余（有 error 却拿不到 rule_id / 出现不可定向改的规则）⇒ ``stop``，不猜。
    """
    report = report or {}
    errors = list(report.get("errors") or [])
    warnings = list(report.get("warnings") or [])
    ratio = shingle_repetition_ratio(draft_text)
    base: dict[str, Any] = {
        "repetition_ratio": ratio,
        "word_count": report.get("word_count"),
        "target_word_count": report.get("target_word_count"),
        "error_count": len(errors),
        "warning_count": len(warnings),
    }
    if ratio > repetition_threshold:
        return ReviewDecision(
            kind="stop",
            reason="repetition_exceeds_threshold",
            note=(f"章内重复率 {ratio:.4f} > 阈值 {repetition_threshold:.4f}"
                  f"（13 字 shingle 口径）——拒绝批准，请人工处置"),
            **base,
        )
    rule_ids, missing = _error_rule_ids(errors)
    if not errors:
        return ReviewDecision(kind="approve", reason="no_review_errors", rule_ids=[], **base)
    if missing:
        return ReviewDecision(
            kind="stop",
            reason="error_without_rule_id",
            rule_ids=rule_ids,
            note=f"{len(missing)} 条 error 没有 rule_id，无法定向改稿——人工处置",
            **base,
        )
    unknown = sorted({r for r in rule_ids if r not in revisable})
    if unknown:
        return ReviewDecision(
            kind="stop",
            reason="non_revisable_rules",
            rule_ids=rule_ids,
            note=f"评审 error 含不可定向改稿的规则 {unknown}——人工处置（不猜、不盲目重写）",
            **base,
        )
    return ReviewDecision(
        kind="revise",
        reason="revisable_review_errors",
        rule_ids=rule_ids,
        note=build_revision_note(errors),
        **base,
    )


def is_confirm_tier_failure(message: str | None) -> bool:
    """错误 / 响应文本是否命中 **confirm 档门禁**（不许静默通过，必须显式接受）。

    前向兼容：同事正在加 ``confirm`` 档（``quality.issues.CONFIRM_RULES``），命中时
    quality_gate 抛的 ValueError 形如
    ``quality gate blocked: [...] | gate=confirm | override_error=... | evidence=...``。
    本函数的职责只有一件：**认出来**，让调用方硬停并报告，不重试、不代填 override。
    """
    text = str(message or "")
    if not text:
        return False
    if "gate=confirm" in text:
        return True
    return any(rule_id in text for rule_id in CONFIRM_TIER_RULE_IDS)


def is_transient_commit_failure(message: str | None) -> bool:
    """commit 失败是否属「观察器抖动」级瞬态（有界重试）而非确定性拒绝。"""
    text = str(message or "")
    return any(pattern in text for pattern in TRANSIENT_COMMIT_PATTERNS)


def is_transient_conflict(detail: str | None) -> bool:
    """409 是否是**已知瞬态**并发竞态（同章已有活跃 run）——只有它值得退避重试。"""
    return "already has an active workflow run" in str(detail or "")


def pause_kind(payload: dict[str, Any] | None) -> str:
    """把 PAUSED run 的 pause_payload 归成三类：作者决议 / 风控门 / 未知人工节点。"""
    stage = str((payload or {}).get("stage") or "")
    if stage.startswith(PAUSE_STAGE_AUTHOR_REVIEW):
        return "author_review"
    if stage.startswith(PAUSE_STAGE_RISK_GATE):
        return "risk_gate"
    return "unknown"


# ---------------------------------------------------------------------------
# HTTP 层（**任何调用都不抛异常**）
# ---------------------------------------------------------------------------


class ApiClient:
    """极简 HTTP 客户端：网络错误 / 非 JSON 响应一律降级为 ``(status, body)``。

    事故留痕：历史驱动用 ``requests.raise_for_status()``，一次 409 就让整个量产进程
    静默死亡（stderr 去了 /dev/null，弧停了 30 分钟没人知道）。本类是那条教训的固化。
    """

    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def request(self, method: str, path: str, *, json_body: dict | None = None) -> tuple[int, Any]:
        try:
            resp = self._client.request(method, path, json=json_body)
        except httpx.HTTPError as exc:
            return 0, {"detail": f"{type(exc).__name__}: {exc}"}
        try:
            body: Any = resp.json() if resp.content else {}
        except ValueError:
            body = {"detail": resp.text[:300]}
        return resp.status_code, body

    def get(self, path: str) -> tuple[int, Any]:
        return self.request("GET", path)

    def post(self, path: str, json_body: dict | None = None) -> tuple[int, Any]:
        return self.request("POST", path, json_body=json_body)

    def close(self) -> None:
        self._client.close()


def http_detail(body: Any, limit: int = 200) -> str:
    """从响应体里取一份可读的 detail 文本（供日志与 failure.detail）。"""
    if isinstance(body, dict):
        text = body.get("detail") or body.get("error") or json.dumps(body, ensure_ascii=False)
    else:
        text = str(body)
    return " ".join(str(text).split())[:limit]


def _compact_review_items(items: Any, limit: int = 20) -> list[Any]:
    """把评审报告的 errors / warnings 压成摘要条目（rule_id + 截断 message）。

    ``warnings`` 是纯文本行（无 rule_id），``errors`` 是 dict（带 rule_id），两形态都收。
    """
    if not isinstance(items, list):
        return []
    out: list[Any] = []
    for item in items[:limit]:
        if isinstance(item, dict):
            out.append({
                "rule_id": item.get("rule_id"),
                "message": " ".join(str(item.get("message") or "").split())[:200],
            })
        else:
            out.append({"rule_id": None, "message": " ".join(str(item).split())[:200]})
    return out


# ---------------------------------------------------------------------------
# 逐章结果
# ---------------------------------------------------------------------------


@dataclass
class ChapterOutcome:
    """一章的产线结果（--json 摘要的单元）。"""

    number: int
    chapter_id: str | None = None
    initial_status: str | None = None
    final_status: str | None = None
    skipped: str | None = None
    stages_run: list[str] = field(default_factory=list)
    word_count: int | None = None
    target_word_count: int | None = None
    review_errors: list[Any] = field(default_factory=list)
    review_error_count: int = 0
    review_error_rule_ids: list[str] = field(default_factory=list)
    review_warnings: list[Any] = field(default_factory=list)
    review_warning_count: int = 0
    repetition_ratio: float | None = None
    draft_version: int | None = None
    revise_rounds: int = 0
    auto_approve_rounds: int = 0
    auto_approvals: list[dict] = field(default_factory=list)
    commit_retries: int = 0
    elapsed_s: float = 0.0
    failure: dict | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None

    def to_dict(self) -> dict:
        return {
            "chapter": self.number,
            "chapter_id": self.chapter_id,
            "initial_status": self.initial_status,
            "final_status": self.final_status,
            "skipped": self.skipped,
            "ok": self.ok,
            "stages_run": list(self.stages_run),
            "word_count": self.word_count,
            "target_word_count": self.target_word_count,
            "review_error_count": self.review_error_count,
            "review_errors": list(self.review_errors),
            "review_error_rule_ids": list(self.review_error_rule_ids),
            "review_warning_count": self.review_warning_count,
            "review_warnings": list(self.review_warnings),
            "repetition_ratio": (
                round(self.repetition_ratio, 4) if self.repetition_ratio is not None else None
            ),
            "draft_version": self.draft_version,
            "revise_rounds": self.revise_rounds,
            "auto_approve_rounds": self.auto_approve_rounds,
            "auto_approvals": list(self.auto_approvals),
            "commit_retries": self.commit_retries,
            "elapsed_s": round(self.elapsed_s, 1),
            "failure": self.failure,
        }


# ---------------------------------------------------------------------------
# 驱动
# ---------------------------------------------------------------------------


class ChapterProducer:
    """把一批章节推到 COMMITTED 的驱动器（四道守卫见模块 docstring）。"""

    def __init__(
        self,
        api: ApiClient,
        args: argparse.Namespace,
        *,
        log_stream: Any,
    ) -> None:
        self.api = api
        self.args = args
        self._log_stream = log_stream
        self.project_id = args.project
        self.mocks = load_mock_providers(args.mock_providers) if args.mock_providers else None

    # --- 日志 ---------------------------------------------------------------

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] {message}", file=self._log_stream, flush=True)

    # --- 只读端点 -----------------------------------------------------------

    def chapters(self) -> list[dict] | None:
        code, body = self.api.get(f"/api/projects/{self.project_id}/chapters")
        if code != 200 or not isinstance(body, list):
            return None
        return body

    def runs(self) -> list[dict] | None:
        code, body = self.api.get(f"/api/projects/{self.project_id}/runs")
        if code != 200 or not isinstance(body, list):
            return None
        return body

    def get_run(self, run_id: str) -> dict | None:
        code, body = self.api.get(f"/api/runs/{run_id}")
        if code != 200 or not isinstance(body, dict):
            return None
        return body

    def latest_draft(self, chapter_id: str) -> dict | None:
        """最新草稿行（``GET /chapters/{cid}/drafts`` 按 version 降序，取首行）。"""
        code, body = self.api.get(f"/api/chapters/{chapter_id}/drafts")
        if code != 200 or not isinstance(body, list) or not body:
            return None
        return body[0] if isinstance(body[0], dict) else None

    # --- 守卫 1：409 容忍的启动（有界退避）----------------------------------

    def _stage_payload(self, stage: str) -> dict:
        """按阶段组装 start 请求体。

        ``author_intent`` 只发给 **plan** 与 **review**：
        - plan：唯一真正消费它的 AI 链（director / director_planner）；
        - review：不是给审校看的，而是给**改稿回路**当继承源——服务端 auto_revise 的
          write / review 子 run 从触发它的 review run ctx 继承 author_intent，不带它
          等于改稿轮在「无作者约束」状态重写正文（2026-09-16 实证）。
        **不发给 write**：writer 的 prompt 模板里没有该字段，端点收下即忘（AGENTS.md
        硬规则 9 的第二次实证形状）——发了只会制造「看起来传了」的假象。
        """
        body: dict[str, Any] = {}
        if self.args.author_intent and stage in (STAGE_PLAN, STAGE_REVIEW):
            body["author_intent"] = self.args.author_intent
        if self.args.target_word_count and stage in (STAGE_PLAN, STAGE_WRITE, STAGE_REVIEW):
            body["target_word_count"] = self.args.target_word_count
        if self.args.quality_gate_mode:
            body["quality_gate_mode"] = self.args.quality_gate_mode
        if self.mocks:
            body["mock_providers"] = self.mocks
        return body

    def start_stage(self, chapter: dict, stage: str, rec: ChapterOutcome) -> str | None:
        """启动一个阶段：409（已知瞬态竞态）退避重试，其余硬错立即返回 None。"""
        chapter_id = chapter["chapter_id"]
        path = f"/api/projects/{self.project_id}/chapters/{chapter_id}/{stage}"
        payload = self._stage_payload(stage)
        deadline = time.monotonic() + self.args.start_timeout
        backoff = self.args.poll_interval + 1.0
        while True:
            code, body = self.api.post(path, payload)
            if code in (200, 201) and isinstance(body, dict) and body.get("run_id"):
                return str(body["run_id"])
            detail = http_detail(body)
            retriable = code == 0 or code >= 500 or (code == 409 and is_transient_conflict(detail))
            if not retriable:
                self.log(f"  ch{chapter['number']} {stage} 启动被拒 HTTP {code}: {detail}")
                rec.failure = {
                    "reason": "start_rejected",
                    "stage": stage,
                    "http": code,
                    "detail": detail,
                }
                return None
            if time.monotonic() >= deadline:
                self.log(f"  ch{chapter['number']} {stage} 启动超时（{code}: {detail}）")
                rec.failure = {
                    "reason": "start_timeout",
                    "stage": stage,
                    "http": code,
                    "detail": detail,
                }
                return None
            self.log(
                f"  ch{chapter['number']} {stage} 启动遇 {code}（{detail}）→ {backoff:.0f}s 后重试"
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, 20.0)

    # --- 守卫 2：等「无活跃 run」-------------------------------------------

    def wait_no_active_run(self, chapter_id: str) -> bool:
        """等该章没有 RUNNING/PENDING run（章节行先翻状态、run 行后落终态的窗口期）。

        超时返回 False（调用方据此硬停——绝不带着竞态去发 commit）。
        """
        deadline = time.monotonic() + self.args.start_timeout
        while True:
            runs = self.runs()
            if runs is not None and not any(
                r.get("chapter_id") == chapter_id and r.get("status") in ("RUNNING", "PENDING")
                for r in runs
            ):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.args.poll_interval)

    # --- 守卫 3：等 run 落定（含风控门的有界自动批准）------------------------

    def wait_settled(
        self,
        run_id: str,
        rec: ChapterOutcome,
        *,
        timeout: float,
        auto_approve: bool,
    ) -> dict | None:
        """轮询到「PAUSED（需人工决策）」或终态；超时返回 None。

        ``auto_approve=True``（commit 阶段）时，命中风控门（``risk_gate``）会**有界**
        自动批准（``--max-auto-approve`` 轮），每次批准都把 pause_payload 摘要记进
        ``rec.auto_approvals``（可审计）。**未知人工节点一律不批准**——交回调用方处理。
        """
        deadline = time.monotonic() + timeout
        while True:
            run = self.get_run(run_id)
            if run is None:
                if time.monotonic() >= deadline:
                    self.log(f"  run {run_id} 查询超时 / 不存在")
                    return None
                time.sleep(self.args.poll_interval)
                continue
            status = run.get("status")
            if status in RUN_TERMINAL:
                return run
            if status == "PAUSED":
                payload = run.get("pause_payload") or {}
                kind = pause_kind(payload)
                if not (auto_approve and kind == "risk_gate"):
                    return run
                if rec.auto_approve_rounds >= self.args.max_auto_approve:
                    self.log(
                        f"  风控门自动批准轮次已用尽（{rec.auto_approve_rounds}/"
                        f"{self.args.max_auto_approve}）→ 交回人工"
                    )
                    return run
                summary = {
                    "round": rec.auto_approve_rounds + 1,
                    "stage": payload.get("stage"),
                    "delta_id": payload.get("delta_id"),
                    "message": payload.get("message"),
                    "changes": {
                        key: len(val) if isinstance(val, list) else 0
                        for key, val in (payload.get("changes") or {}).items()
                    },
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                code, body = self.api.post(
                    f"/api/runs/{run_id}/resume", {"human_input": {"approved": True}}
                )
                rec.auto_approve_rounds += 1
                summary["http"] = code
                rec.auto_approvals.append(summary)
                self.log(
                    f"  风控门自动批准 #{rec.auto_approve_rounds}"
                    f"（stage={summary['stage']}, delta_id={summary['delta_id']}, "
                    f"changes={summary['changes']}, HTTP {code}）"
                )
                if code not in (200, 201):
                    # resume 失败（run 已不在 PAUSED / 同章另有活跃 run）：不炸进程，
                    # 回到轮询——真实状态由 GET /runs/{id} 说了算。
                    self.log(f"  resume {run_id} HTTP {code}: {http_detail(body)}（转入轮询兜底）")
                continue
            if time.monotonic() >= deadline:
                self.log(
                    f"  run {run_id} 超时未落定: status={status!r} "
                    f"current_node={run.get('current_node')!r}"
                )
                return None
            time.sleep(self.args.poll_interval)

    # --- 守卫 4：改稿子 run 靠轮询「更新的 PAUSED review run」发现 ------------

    def wait_new_paused_review(
        self, chapter_id: str, known_run_ids: set[str], timeout: float
    ) -> dict | None:
        """等一个**更新的、PAUSED 的 chapter-review run**（改稿回路的子 run）。

        为什么不能盯 chapter.status：改稿轮里章节状态停在 DRAFTED 不翻转，盯着它只会
        傻等超时（历史驱动的实机坑）。子 run 也不在 resume 的响应里——只能从
        ``GET /projects/{pid}/runs`` 里认出来。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            runs = self.runs()
            if runs:
                candidates = [
                    r for r in runs
                    if r.get("chapter_id") == chapter_id
                    and r.get("workflow_name") == "chapter-review"
                    and r.get("status") == "PAUSED"
                    and r.get("run_id") not in known_run_ids
                ]
                if candidates:
                    candidates.sort(key=lambda r: r.get("started_at") or "", reverse=True)
                    run = self.get_run(str(candidates[0]["run_id"]))
                    if run is not None:
                        return run
            time.sleep(self.args.poll_interval)
        return None

    # --- 阶段：review -------------------------------------------------------

    def _fetch_review_inputs(self, chapter_id: str, run_id: str) -> tuple[dict, str, dict | None]:
        """读 PAUSED review run 的报告 + 最新草稿正文（批准判定的两份输入）。"""
        run = self.get_run(run_id) or {}
        report = (run.get("pause_payload") or {}).get("review_report") or {}
        draft = self.latest_draft(chapter_id) or {}
        return report, str(draft.get("content") or ""), draft

    def _record_review(
        self, rec: ChapterOutcome, decision: ReviewDecision, draft: dict, report: dict
    ) -> None:
        rec.word_count = decision.word_count
        rec.target_word_count = decision.target_word_count
        rec.review_error_count = decision.error_count
        rec.review_warning_count = decision.warning_count
        rec.review_error_rule_ids = list(decision.rule_ids)
        rec.repetition_ratio = decision.repetition_ratio
        rec.review_errors = _compact_review_items(report.get("errors"))
        rec.review_warnings = _compact_review_items(report.get("warnings"))
        if draft.get("version") is not None:
            rec.draft_version = int(draft["version"])
    def _approve_review(self, run_id: str) -> bool:
        code, body = self.api.post(f"/api/runs/{run_id}/resume", {"human_input": {"approved": True}})
        if code not in (200, 201):
            self.log(f"  批准 review run {run_id} HTTP {code}: {http_detail(body)}")
            return False
        return True

    def _reject_for_revision(self, run_id: str, note: str) -> None:
        """驳回 + 改稿：**尽力而为**（409/404 只记日志，不炸进程），子 run 靠轮询发现。"""
        code, body = self.api.post(
            f"/api/runs/{run_id}/resume",
            {"human_input": {"approved": False, "revise": True, "note": note}},
        )
        if code not in (200, 201):
            self.log(
                f"  驳回改稿 resume {run_id} HTTP {code}: {http_detail(body)}"
                f"（转入轮询，子 run 由 run 列表兜底发现）"
            )

    def run_review_stage(self, chapter: dict, rec: ChapterOutcome) -> bool:
        """review 阶段：PAUSE → 读报告 → 批准 / 定向改稿（有界）/ 硬停。"""
        chapter_id = chapter["chapter_id"]
        run_id = self.start_stage(chapter, STAGE_REVIEW, rec)
        if run_id is None:
            return False
        rec.stages_run.append(STAGE_REVIEW)
        known = {run_id}
        run = self.wait_settled(run_id, rec, timeout=self.args.stage_timeout, auto_approve=False)
        if run is None:
            rec.failure = {"reason": "review_timeout", "stage": STAGE_REVIEW, "run_id": run_id}
            return False
        if run.get("status") != "PAUSED":
            rec.failure = {
                "reason": "review_not_paused",
                "stage": STAGE_REVIEW,
                "run_id": run_id,
                "status": run.get("status"),
                "detail": http_detail(run.get("error")),
            }
            return False

        while True:
            report, draft_text, draft = self._fetch_review_inputs(chapter_id, run_id)
            decision = decide_review(
                report, draft_text, repetition_threshold=self.args.repetition_threshold
            )
            self._record_review(rec, decision, draft, report)
            self.log(
                f"  ch{chapter['number']} 审校（草稿 v{rec.draft_version}）："
                f"{decision.word_count}/{decision.target_word_count} 字，"
                f"error {decision.error_count}（{decision.rule_ids or '—'}），"
                f"warning {decision.warning_count}，"
                f"重复率 {decision.repetition_ratio:.4f} → 决议 {decision.kind}"
            )
            # 改稿回路的子 run 自 2026-09-18 起**继承** target_word_count（服务端按
            # 「resume 请求体 > 原 review run ctx」解析后透传给每轮 write / review 子 run，
            # 见 packages/core/api/routers/workflows/control.py 的 effective_target_word_count）。
            # 因此第二轮起审校与首轮同口径。此处的差异告警保留为**探针**：它一旦响，说明
            # 服务端没继承（老版本服务端 / 显式覆盖），口径漂移必须如实报出来，不能吞。
            if (
                self.args.target_word_count
                and decision.target_word_count
                and int(decision.target_word_count) != int(self.args.target_word_count)
            ):
                self.log(
                    f"  ⚠ 本轮审校目标字数 {decision.target_word_count} ≠ 命令行 "
                    f"--target-word-count {self.args.target_word_count}"
                    f"（服务端未继承该参数？按服务端默认 / 题材包解析，口径已漂）"
                )

            if decision.kind == "approve":
                if not self._approve_review(run_id):
                    rec.failure = {
                        "reason": "approve_resume_failed",
                        "stage": STAGE_REVIEW,
                        "run_id": run_id,
                    }
                    return False
                settled = self.wait_settled(
                    run_id, rec, timeout=self.args.stage_timeout, auto_approve=False
                )
                if settled is None or settled.get("status") != "COMPLETED":
                    rec.failure = {
                        "reason": "review_run_not_completed",
                        "stage": STAGE_REVIEW,
                        "run_id": run_id,
                        "status": (settled or {}).get("status"),
                        "detail": http_detail((settled or {}).get("error")),
                    }
                    return False
                return True

            if decision.kind == "stop":
                rec.failure = {
                    "reason": decision.reason,
                    "stage": STAGE_REVIEW,
                    "run_id": run_id,
                    "detail": decision.note,
                    "rule_ids": decision.rule_ids,
                    "repetition_ratio": round(decision.repetition_ratio, 4),
                }
                return False

            # kind == "revise"
            if rec.revise_rounds >= self.args.max_revise_rounds:
                rec.failure = {
                    "reason": "revise_rounds_exhausted",
                    "stage": STAGE_REVIEW,
                    "run_id": run_id,
                    "detail": f"已用满 {rec.revise_rounds} 轮定向改稿仍未通过",
                    "rule_ids": decision.rule_ids,
                }
                return False
            self.log(f"  驳回改稿（第 {rec.revise_rounds + 1} 轮）：{decision.note[:160]}")
            self._reject_for_revision(run_id, decision.note)
            rec.revise_rounds += 1
            child = self.wait_new_paused_review(
                chapter_id, known, timeout=self.args.stage_timeout
            )
            if child is None:
                rec.failure = {
                    "reason": "auto_revise_child_timeout",
                    "stage": STAGE_REVIEW,
                    "run_id": run_id,
                    "detail": "未在超时内发现改稿回路产出的新 PAUSED review run",
                }
                return False
            run_id = str(child["run_id"])
            known.add(run_id)
            self.log(f"  发现改稿子 run {run_id}（PAUSED，读报告续判）")

    # --- 阶段：plan / write --------------------------------------------------

    def run_simple_stage(self, chapter: dict, stage: str, rec: ChapterOutcome) -> bool:
        run_id = self.start_stage(chapter, stage, rec)
        if run_id is None:
            return False
        rec.stages_run.append(stage)
        run = self.wait_settled(run_id, rec, timeout=self.args.stage_timeout, auto_approve=False)
        if run is None:
            rec.failure = {"reason": f"{stage}_timeout", "stage": stage, "run_id": run_id}
            return False
        if run.get("status") != "COMPLETED":
            rec.failure = {
                "reason": f"{stage}_failed",
                "stage": stage,
                "run_id": run_id,
                "status": run.get("status"),
                "detail": http_detail(run.get("error")),
            }
            return False
        return True

    # --- 阶段：commit -------------------------------------------------------

    def run_commit_stage(self, chapter: dict, rec: ChapterOutcome) -> bool:
        chapter_id = chapter["chapter_id"]
        for attempt in range(COMMIT_RETRY_ROUNDS + 1):
            if not self.wait_no_active_run(chapter_id):
                rec.failure = {
                    "reason": "active_run_timeout",
                    "stage": STAGE_COMMIT,
                    "detail": "commit 前仍检测到该章的 RUNNING/PENDING run",
                }
                return False
            run_id = self.start_stage(chapter, STAGE_COMMIT, rec)
            if run_id is None:
                return False
            if attempt == 0:
                rec.stages_run.append(STAGE_COMMIT)
            run = self.wait_settled(
                run_id, rec, timeout=self.args.stage_timeout, auto_approve=True
            )
            if run is None:
                rec.failure = {"reason": "commit_timeout", "stage": STAGE_COMMIT, "run_id": run_id}
                return False
            if run.get("status") == "COMPLETED":
                return True

            error_text = str(run.get("error") or "")
            # confirm 档门禁：硬停，绝不重试、绝不代填 override（前向兼容契约）。
            if is_confirm_tier_failure(error_text):
                rec.failure = {
                    "reason": "gate_confirm_tier",
                    "stage": STAGE_COMMIT,
                    "run_id": run_id,
                    "detail": error_text[:600],
                    "rule_ids": [r for r in CONFIRM_TIER_RULE_IDS if r in error_text],
                    "needs_gate_override": True,
                }
                return False
            if run.get("status") == "PAUSED":
                rec.failure = {
                    "reason": "commit_paused_manual",
                    "stage": STAGE_COMMIT,
                    "run_id": run_id,
                    "detail": f"停在人工节点（自动批准轮次 {rec.auto_approve_rounds}）",
                    "pause_payload": (run.get("pause_payload") or {}).get("stage"),
                }
                return False
            if is_transient_commit_failure(error_text) and attempt < COMMIT_RETRY_ROUNDS:
                rec.commit_retries += 1
                self.log(
                    f"  ch{chapter['number']} commit 瞬态失败（{error_text[:120]}）"
                    f"→ 第 {rec.commit_retries} 次重试"
                )
                time.sleep(min(20.0, 5.0 * (attempt + 1)))
                continue
            rec.failure = {
                "reason": "commit_failed",
                "stage": STAGE_COMMIT,
                "run_id": run_id,
                "status": run.get("status"),
                "detail": error_text[:600],
            }
            return False
        return False

    # --- 逐章主流程 ---------------------------------------------------------

    def produce_chapter(self, chapter: dict, rec: ChapterOutcome) -> bool:
        number = int(chapter["number"])
        rec.chapter_id = chapter.get("chapter_id")
        rec.initial_status = chapter.get("status")
        stages = planned_stages(str(chapter.get("status")))
        if not stages:
            rec.skipped = f"already {chapter.get('status')}"
            rec.final_status = chapter.get("status")
            self.log(f"ch{number} 已是 {chapter.get('status')}，跳过")
            return True

        self.log(f"ch{number}（{rec.chapter_id}）起点 {rec.initial_status} → 计划 {stages}")
        for stage in stages:
            # 每个阶段前先等清场：章节行先翻状态、run 行后落终态，窗口期内 start 会 409。
            if not self.wait_no_active_run(str(rec.chapter_id)):
                rec.failure = {
                    "reason": "active_run_timeout",
                    "stage": stage,
                    "detail": "该章仍有 RUNNING/PENDING run（可能是上一轮被中断的孤儿 run）",
                }
                return False
            if stage == STAGE_REVIEW:
                if not self.run_review_stage(chapter, rec):
                    return False
            elif stage == STAGE_COMMIT:
                if not self.run_commit_stage(chapter, rec):
                    return False
            else:
                if not self.run_simple_stage(chapter, stage, rec):
                    return False
            self.log(f"  ch{number} {stage} ✓")
        rec.final_status = self._chapter_status(str(rec.chapter_id))
        return True

    def _chapter_status(self, chapter_id: str) -> str | None:
        code, body = self.api.get(f"/api/chapters/{chapter_id}")
        if code == 200 and isinstance(body, dict):
            return str(body.get("status")) if body.get("status") else None
        return None

    # --- 顶层循环 -----------------------------------------------------------

    def run(self) -> tuple[int, dict]:
        """跑完选中章节；返回 (exit_code, summary)。"""
        started = time.monotonic()
        chapters = self.chapters()
        if chapters is None:
            self.log(f"读章节列表失败（project={self.project_id}）——project 不存在或服务不可达")
            return EXIT_USAGE, {}

        selection = parse_chapter_selection(self.args.chapters or self.args.only)
        by_number = {int(c["number"]): c for c in chapters}
        if selection is None:
            selected = sorted(by_number.values(), key=lambda c: int(c["number"]))
        else:
            missing = [n for n in selection if n not in by_number]
            if missing:
                self.log(f"选中的章号在项目中不存在: {missing}")
                return EXIT_USAGE, {}
            selected = [by_number[n] for n in selection]

        if self.args.dry_run:
            plan: list[dict] = []
            for chapter in selected:
                stages = planned_stages(str(chapter.get("status")))
                plan.append({
                    "chapter": int(chapter["number"]),
                    "chapter_id": chapter.get("chapter_id"),
                    "status": chapter.get("status"),
                    "stages": stages,
                    "skip": None if stages else f"already {chapter.get('status')}",
                })
            return EXIT_OK, {
                "mode": "dry-run",
                "base_url": self.api.base_url,
                "project_id": self.project_id,
                "chapters": plan,
                "counts": {"selected": len(plan), "would_run": sum(1 for p in plan if p["stages"])},
                "elapsed_s": round(time.monotonic() - started, 1),
            }

        outcomes: list[ChapterOutcome] = []
        for chapter in selected:
            rec = ChapterOutcome(number=int(chapter["number"]))
            chapter_started = time.monotonic()
            try:
                self.produce_chapter(chapter, rec)
            except Exception as exc:  # noqa: BLE001 —— 驱动异常也必须落成可读失败，不静默死
                rec.failure = {"reason": "driver_exception", "detail": f"{type(exc).__name__}: {exc}"}
            rec.elapsed_s = time.monotonic() - chapter_started
            if rec.final_status is None and rec.chapter_id:
                rec.final_status = self._chapter_status(rec.chapter_id)
            outcomes.append(rec)
            if rec.ok:
                ratio = "—" if rec.repetition_ratio is None else f"{rec.repetition_ratio:.4f}"
                self.log(
                    f"ch{rec.number} 完成（{rec.elapsed_s:.0f}s，"
                    f"{rec.word_count}/{rec.target_word_count} 字，重复率 {ratio}，"
                    f"改稿 {rec.revise_rounds} 轮，风控自动批准 {rec.auto_approve_rounds} 次）"
                )
            else:
                failure = rec.failure or {}
                self.log(
                    f"ch{rec.number} 失败 @{failure.get('reason')}："
                    f"{failure.get('detail') or failure} —— 整轮停止"
                )
                break

        counts = {
            "selected": len(selected),
            "produced": sum(1 for r in outcomes if r.ok and not r.skipped),
            "skipped": sum(1 for r in outcomes if r.skipped),
            "failed": sum(1 for r in outcomes if not r.ok),
            "not_attempted": len(selected) - len(outcomes),
        }
        summary = {
            "mode": "apply",
            "base_url": self.api.base_url,
            "project_id": self.project_id,
            "counts": counts,
            "chapters": [r.to_dict() for r in outcomes],
            "elapsed_s": round(time.monotonic() - started, 1),
        }
        return (EXIT_OK if counts["failed"] == 0 else EXIT_CHAPTER_FAILED), summary


# ---------------------------------------------------------------------------
# mock_providers（离线 / 测试用；生产留空）
# ---------------------------------------------------------------------------


def load_mock_providers(path: Path | str) -> dict[str, list[str]]:
    """读 ``--mock-providers`` 的 JSON 文件：``{agent_name: [json_str, ...]}``。

    与 start 请求体的 ``mock_providers`` 字段同形（服务端 MockProvider.scripted）。
    非法 JSON / 非对象 / 值非字符串数组 → ValueError（调用方转 exit 1）。
    """
    raw = Path(path).read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"mock-providers 不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("mock-providers 顶层必须是对象 {agent_name: [payload_json_str, ...]}")
    out: dict[str, list[str]] = {}
    for agent, script in parsed.items():
        if not isinstance(script, list) or not all(isinstance(item, str) for item in script):
            raise ValueError(f"mock-providers[{agent!r}] 必须是字符串数组")
        out[str(agent)] = list(script)
    return out


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------


def render_dry_run(summary: dict) -> str:
    lines = [
        f"produce_chapters: dry-run project={summary['project_id']} base_url={summary['base_url']}",
    ]
    for item in summary["chapters"]:
        stages = " → ".join(item["stages"]) if item["stages"] else "（跳过）"
        lines.append(
            f"  ch{item['chapter']}（{item['chapter_id']}）status={item['status']} → {stages}"
        )
    lines.append(
        f"[结论] dry-run：{summary['counts']['would_run']}/{summary['counts']['selected']} 章需要跑"
        "（未发起任何 run）"
    )
    return "\n".join(lines)


def render_summary(summary: dict) -> str:
    counts = summary["counts"]
    lines = [
        f"produce_chapters: project={summary['project_id']} base_url={summary['base_url']}",
        f"[计数] 选中 {counts['selected']} / 产出 {counts['produced']} / 跳过 {counts['skipped']}"
        f" / 失败 {counts['failed']} / 未处理 {counts['not_attempted']}"
        f"（{summary['elapsed_s']}s）",
    ]
    for item in summary["chapters"]:
        head = (
            f"  ch{item['chapter']} status={item['final_status']} "
            f"{item['word_count']}/{item['target_word_count']} 字 "
            f"重复率={item['repetition_ratio']} 改稿={item['revise_rounds']} "
            f"风控批准={item['auto_approve_rounds']} {item['elapsed_s']}s"
        )
        if item["skipped"]:
            lines.append(head + f"（跳过：{item['skipped']}）")
        elif item["ok"]:
            lines.append(head)
        else:
            failure = item["failure"] or {}
            lines.append(head + f" → 失败[{failure.get('reason')}]")
            detail = failure.get("detail") or ""
            if detail:
                lines.append(f"      {detail[:300]}")
            if failure.get("rule_ids"):
                lines.append(f"      rule_ids={failure['rule_ids']}")
    lines.append("[结论] " + ("全部完成" if counts["failed"] == 0 else "有章节失败，整轮已停止"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="produce_chapters",
        description=(
            "把指定章节推到 COMMITTED：plan → write → review（人工节点）→ commit，"
            "带 409 容忍启动、commit 前清场、报告驱动决议与有界风控门自动批准。"
            "默认 dry-run 只报告不发起 run；--apply 才真跑。"
            "退出码 0=完成, 1=参数/配置错误, 2=某章失败整轮停止。"
        ),
        epilog="只走 HTTP（事实源在服务端库）；章节选择形如 --chapters 1-3,7。",
    )
    parser.add_argument("--project", required=True, help="目标 project_id（prj_…）")
    parser.add_argument(
        "--chapters",
        default=None,
        help="章节选择表达式，形如 1-3,7（缺省=项目全部章节，已 COMMITTED 的自动跳过）",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="--chapters 的旧名（与 --chapters 同语法；两者都给则报错）",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="API 根地址（缺省由 get_settings() 的 api_host/api_port 拼出）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只报告会对哪些章做什么（默认行为）")
    parser.add_argument("--apply", action="store_true", help="真跑（缺省=dry-run）")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="stdout 仅输出机器可读摘要（人类日志走 stderr）")
    parser.add_argument("--author-intent", default=None, help="作者硬性要求（发给 plan 与 review）")
    parser.add_argument("--target-word-count", type=int, default=None,
                        help="单章目标字数（发给 plan/write/review；缺省由服务端按题材包解析）")
    parser.add_argument("--max-revise-rounds", type=int, default=2,
                        help="定向改稿最大轮数（缺省 2）")
    parser.add_argument("--max-auto-approve", type=int, default=3,
                        help="风控门自动批准最大轮数（缺省 3）")
    parser.add_argument("--repetition-threshold", type=float, default=DEFAULT_REPETITION_THRESHOLD,
                        help=f"批准前的章内重复率上限（缺省 {DEFAULT_REPETITION_THRESHOLD}）")
    parser.add_argument("--stage-timeout", type=float, default=1500.0,
                        help="单个阶段的等待上限秒数（缺省 1500；plan/write 可能跑几分钟）")
    parser.add_argument("--start-timeout", type=float, default=300.0,
                        help="启动 409 退避 / 等清场的上限秒数（缺省 300）")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="轮询间隔秒数（缺省 5）")
    parser.add_argument("--http-timeout", type=float, default=60.0, help="单次 HTTP 超时秒数（缺省 60）")
    parser.add_argument("--quality-gate-mode", default=None, choices=("enforce", "report"),
                        help="门禁模式透传（缺省不发该字段，由服务端环境决定）")
    parser.add_argument("--mock-providers", type=Path, default=None,
                        help="离线 / 测试用：mock_providers 脚本 JSON 文件路径")
    return parser


def _force_utf8_stdio() -> None:
    """把 stdout / stderr 切到 UTF-8（errors=replace）。

    Windows 控制台默认 GBK：报告里的 ✓ / → / ⚠ 与 ``--help`` 的中文都会
    UnicodeEncodeError 或变成乱码。**必须在 parse_args 之前调用**——否则 ``--help``
    的文本在切 UTF-8 之前就按 GBK 写出去了。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):  # pragma: no cover - 非 TTY / 已重定向
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.dry_run = not args.apply
    # --json：人类日志走 stderr，stdout 只留 JSON（与其他脚本的 --json 约定一致）。
    log_stream = sys.stderr if args.as_json else sys.stdout

    if args.chapters and args.only:
        sys.stderr.write("ERROR: --chapters 与 --only 不能同时给（同语法，给一个即可）\n")
        return EXIT_USAGE
    try:
        parse_chapter_selection(args.chapters or args.only)
    except ValueError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return EXIT_USAGE
    if args.max_revise_rounds < 0 or args.max_auto_approve < 0:
        sys.stderr.write("ERROR: --max-revise-rounds / --max-auto-approve 不得为负\n")
        return EXIT_USAGE
    if not 0.0 <= args.repetition_threshold <= 1.0:
        sys.stderr.write("ERROR: --repetition-threshold 必须落在 [0, 1]\n")
        return EXIT_USAGE

    settings = get_settings()
    base_url = args.base_url or f"http://{settings.api_host}:{settings.api_port}"
    api = ApiClient(base_url, args.http_timeout)
    try:
        if args.mock_providers:
            try:
                load_mock_providers(args.mock_providers)
            except (OSError, ValueError) as exc:
                sys.stderr.write(f"ERROR: mock-providers 读不出 / 非法: {exc}\n")
                return EXIT_USAGE
        code, health = api.get("/api/health")
        if code != 200:
            sys.stderr.write(
                f"ERROR: API 不可达 {base_url}/api/health（HTTP {code}: {http_detail(health)}）"
                "——先起服务（python scripts/serve.py）或用 --base-url 指向正确端口\n"
            )
            return EXIT_USAGE
        producer = ChapterProducer(api, args, log_stream=log_stream)
        exit_code, summary = producer.run()
    finally:
        api.close()

    if args.as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if summary:
            print(render_dry_run(summary) if summary.get("mode") == "dry-run" else render_summary(summary))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
