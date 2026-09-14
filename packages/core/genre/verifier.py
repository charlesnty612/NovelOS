"""题材包核销层 v2（题材库 P1b 核销口径修订）——``verify_chapter`` 与 ``GenreCheckResult``。

职责：把题材包 payload 里的**规定性约束**与某一章的既有产物对账，产出
``GenreIssue``（``rule_id`` 前缀 ``GENRE-``、``severity`` 恒 ``warning``）列表。
v1 逐章核两类（配比偏差 + 节奏红线）；v2 按实战证据（书1 全弧 21 章）修订四点：

1. **配比核销升格为弧级**（主项）。v1 逐章按 scene 字数分摊对账——声明 5 个键 vs
   每章 2~6 个 scene，单章总偏离均值 40%（阈值 10%）是采样噪声（弧级池化后稳定在
   32%，且噪声项消失）。v2 改为：

   - **章级**（``ratio_check``）：照旧算并保留明细（declared / observed / deviations
     / total_deviation），但 ``checked`` / ``deviation_exceeded`` 只作报告事实，
     **不再产 issue**（``degraded=True`` / ``issue_emitted=False`` 留痕）；章级唯一
     的 issue 是 untyped scene 信号（见 3）。
   - **弧级**（``arc_check``）：把同卷（``chapters.volume_id``）各章 scene 池化后与
     ``ratio_declarations`` 对账，``GENRE-RATIO-DEVIATION`` 只在弧级产出。结论就绪
     判据：**本章是弧内末章**（口径上弧已阶段性完整）**或弧内累计 typed scene ≥
     :data:`ARC_MIN_TYPED_SCENES`**（样本足够盖过采样噪声）；未就绪时只记明细 +
     ``skipped`` 原因（``arc_ratio:arc_not_ready``）。样本下限的取舍见常量注释。
   - 无卷章节（``volume_id IS NULL``）不与单章同弧——按「同项目无卷章节池」聚合
     （见 :func:`_load_arc_rows`），否则等于把章级噪声原样搬到弧级。

2. **键不对称容错**。声明与观测的键集不必相同（observed 超集/子集都合法），
   核销按「交集直比 + catch-all 残差桶」归并（:func:`_compare_shares`）：声明里的
   catch-all 键（:data:`RATIO_RESIDUAL_KEY`，默认 ``other``）是残差桶，观测侧的
   声明外 scene_type 字数并入该桶一起比对——v1 的 union 口径会把「声明 other /
   实测具体维度」算成双向偏离（other 负偏离 + 具体键正偏离），是单章误报的机制之一。

3. **untyped scene 显式化**。scene 缺 ``scene_type`` 时不再静默跳过：声明了配比
   （``scene_type`` 契约生效）时产 ``GENRE-SCENE-UNTYPED``（warning，逐章一条带计数）。
   实测 ch3 / ch17 整章 untyped，v1 只写 ``skipped=no_scene_type``，无任何可读信号。

4. **红线语料扩面**。命中口径不变（整条红线归一后为语料子串，大小写不敏感），语料从
   「``plan_json.deviations`` + ``length_report``」（实测 ~140 字）扩到「整章正文 +
   计划关键段 + 偏差注记 + length_report」，并回报 ``corpus_chars`` / ``corpus_sources``
   ——让「0 命中」从不可信变成可信（v1 无法区分「真无命中」与「语料太小」）。

**settlement 弧末核销待 scene 标注覆盖**：实战 redline 含「卷末无结算 = 结构位缺失」，
但 scene / chapter / draft 三层都没有 settlement 标注字段（21 章零出现），判据对象
无数据通路。本层不造假核销——在 ``arc_check.pending`` 显式登记
（:data:`SETTLEMENT_PENDING_NOTE`）。

数据来源（全部只读；任一缺失 → 该项跳过并记录原因，不抛错、不阻断评审）：

- ``chapters``：``project_id`` / ``number`` / ``volume_id`` / ``plan_json``；
- ``drafts``：最新草稿正文（字数兜底口径 :func:`packages.core.quality.wordcount.visible_chars`；
  v2 起同时作为红线语料）；
- ``workflow_runs``（``JOIN workflows``，``name='chapter-write'``）：``checkpoint_json``
  里的 ``scene_plan``（scene_planner 权威产出）与 ``length_report``；弧级池化按同卷
  逐章取最近一条 write run；
- ``projects.genre_pack_id`` → ``genre_packs``：未显式传 ``pack`` 时的绑定题材包。

边界（与 P1a README 一致的「读侧全容错」）：
- 无绑定题材包 → ``GenreCheckResult(bound=False, checked=False)``，零 issue
  （调用方据此整段跳过，零行为变化）；
- 极老库缺 ``genre_packs`` / ``volumes`` 表、缺 ``workflow_runs`` 行、脏
  ``checkpoint_json`` → 对应来源缺席，按「跳过 + 原因」处理；
- 本模块**不产 error / 不进** :data:`packages.core.quality.issues.BLOCKING_RULES`
  （质量门禁阻断白名单不动——v2 的四点修订全部保持 warning / report-only）；
  ``GenreIssue.to_quality_issue()`` 给出与 quality ``Issue`` 同形的 informational 条目。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packages.core.db import get_connection
from packages.core.quality.wordcount import visible_chars

__all__ = [
    "ARC_MIN_TYPED_SCENES",
    "GENRE_ISSUE_RULE_PREFIX",
    "GENRE_ISSUE_SEVERITY",
    "RATIO_DEVIATION_THRESHOLD",
    "RATIO_RESIDUAL_KEY",
    "RULE_RATIO_DEVIATION",
    "RULE_REDLINE_HIT",
    "RULE_SCENE_UNTYPED",
    "RULE_WORD_BAND_DEVIATION",
    "SETTLEMENT_PENDING_NOTE",
    "GenreCheckResult",
    "GenreIssue",
    "verify_chapter",
]

# issue 规则前缀 / severity：核销层恒 warning（informational，不阻断）。
GENRE_ISSUE_RULE_PREFIX = "GENRE-"
GENRE_ISSUE_SEVERITY = "warning"

# 配比总偏离阈值（各键 |观测份额 - 声明份额| 之和）。v2 起该阈值**只在弧级触发 issue**；
# 章级仍按同一阈值标 ``deviation_exceeded``，但只作明细（见 _check_ratio）。
RATIO_DEVIATION_THRESHOLD = 0.10

# 弧级结论的样本下限：弧内累计 typed scene 数。
# 取舍依据：份额 p 的抽样标准误 SE = sqrt(p(1-p)/n)。声明 5 个键、p≈0.2 时
# n=20 → SE≈0.089（最小整数 n 使 SE 落到 10% 阈值之下）；章级的 2~6 个 scene
# → SE≈0.16~0.28，即「单章总偏离均值 40%」的量级来源。
# 达不到下限时不在卷中途下结论，只在「弧内末章」出薄样本结论（message 标注
# 「样本偏薄」）——避免把弧中途的采样噪声当偏离。
ARC_MIN_TYPED_SCENES = 20

# 配比残差键（声明里的 catch-all 维度）：观测侧声明外 scene_type 的字数并入此键比对。
RATIO_RESIDUAL_KEY = "other"

# 规则 id（v2：RULE_RATIO_DEVIATION 的 scope 从章级升格为弧级）
RULE_RATIO_DEVIATION = "GENRE-RATIO-DEVIATION"
RULE_SCENE_UNTYPED = "GENRE-SCENE-UNTYPED"
RULE_WORD_BAND_DEVIATION = "GENRE-WORD-BAND-DEVIATION"
RULE_REDLINE_HIT = "GENRE-REDLINE-HIT"

# quality category 口径（packages.core.quality.issues.Category 的合法值）：
# 配比 / untyped 属 payoff 维度、字数带 / 红线属 pacing 维度。
_CATEGORY_RATIO = "payoff"
_CATEGORY_PACING = "pacing"

# chapter-write 工作流名（checkpoint_json 里 scene_plan / length_report 的来源）。
_WRITE_WORKFLOW_NAME = "chapter-write"

# 版本内容字段作为「张/弧」标识的降级：无 volume_id 时的弧作用域前缀。
_VOLUMELESS_SCOPE_PREFIX = "project:"

# 计划侧进入红线语料的关键段（整章正文之外的补充语料）。
_PLAN_CORPUS_FIELDS = ("chapter_goal", "core_conflict", "turning_point", "deviations")

# settlement 弧末核销的诚实登记（无数据通路，不做假核销）。
SETTLEMENT_PENDING_NOTE = (
    "settlement_arc_end: 弧末结算核销待 scene 标注覆盖"
    "（scene / chapter / draft 三层均无 settlement 字段，判据对象无数据通路）"
)


@dataclass(frozen=True)
class GenreIssue:
    """题材核销 issue（report-only）。

    字段与 ``packages.core.quality.models.Issue`` 同形（``to_quality_issue`` 直接
    构造合法 dict），但**不是** quality engine 的 Issue 实例——核销层不写
    ``quality_reports``，只把条目挂到评审报告 / 调用方。

    ``severity`` 恒 ``"warning"``（:data:`GENRE_ISSUE_SEVERITY`）；``rule_id`` 恒以
    ``GENRE-`` 开头，故 :func:`packages.core.quality.issues.is_blocking_issue` 对它
    恒为 False（阻断白名单不含 GENRE- 规则）。
    """

    rule_id: str
    message: str
    category: str = _CATEGORY_PACING
    severity: str = GENRE_ISSUE_SEVERITY
    location: str = "<unknown>"
    suggestion: str | None = None
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """评审报告 / API 用 dict 形态（与 quality issue dump 同形）。"""
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "location": self.location,
            "suggestion": self.suggestion,
            "evidence_refs": list(self.evidence_refs),
        }

    def to_quality_issue(self) -> dict[str, Any]:
        """quality ``Issue`` 兼容 dict（informational 通道；不落库、不参与评分）。"""
        return self.to_dict()


@dataclass
class GenreCheckResult:
    """核销结果（``genre_check`` 段的数据源）。

    - ``bound`` / ``checked``：是否绑定题材包 / 是否真的核了至少一项；
    - ``issues``：``GENRE-`` 前缀 issue 列表（可能为空）；
    - ``ratio_check``：**章级**配比明细（v2 起不产配比 issue，只记事实）；
    - ``arc_check``：**弧级**配比明细 + 结论（``GENRE-RATIO-DEVIATION`` 的产地，
      含 ``pending``（settlement 待覆盖登记）；``None`` 表示核销未走到该步）；
    - ``redline_check``：字数带 + 红线的明细（``checked=False`` 时带 ``reason``）；
      ``None`` 表示 payload 未声明 ``pacing``；
    - ``skipped``：跳过原因清单（``"no_binding"`` / ``"ratio_declarations_not_declared"``
      / ``"arc_ratio:arc_not_ready"`` …）；
    - ``error``：只在读路径整体失败时非空（仍不抛错）。
    """

    bound: bool = False
    checked: bool = False
    pack_id: str | None = None
    pack_version: int | None = None
    issues: list[GenreIssue] = field(default_factory=list)
    ratio_check: dict[str, Any] | None = None
    arc_check: dict[str, Any] | None = None
    redline_check: dict[str, Any] | None = None
    skipped: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def rule_ids(self) -> list[str]:
        return [i.rule_id for i in self.issues]

    def to_dict(self) -> dict[str, Any]:
        """``review_report["genre_check"]`` 的落点形态（纯 JSON 可序列化）。"""
        return {
            "bound": self.bound,
            "checked": self.checked,
            "pack_id": self.pack_id,
            "pack_version": self.pack_version,
            "issues": [i.to_dict() for i in self.issues],
            "issue_count": len(self.issues),
            "rule_ids": self.rule_ids,
            "ratio_check": self.ratio_check,
            "arc_check": self.arc_check,
            "redline_check": self.redline_check,
            "skipped": list(self.skipped),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# 读路径（全容错）
# ---------------------------------------------------------------------------


def _parse_json(raw: Any) -> Any:
    """JSON 原文 → 对象；已是 dict/list 原样返回；非法 → None。"""
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _load_chapter_sources(db_path: str | Path, chapter_id: str) -> dict[str, Any]:
    """一次性读核销所需来源（章节行 / 最新草稿 / 最近 chapter-write run）。

    任一查询失败 → 该来源缺席（``None``），不抛错（与 P1a 读侧全容错同款）。
    """
    sources: dict[str, Any] = {
        "found": False,
        "project_id": None,
        "number": None,
        "volume_id": None,
        "plan_json": {},
        "draft_content": None,
        "draft_version": None,
        "scene_plan": None,
        "length_report": None,
        "write_run_id": None,
    }
    try:
        conn = get_connection(db_path)
    except Exception:  # noqa: BLE001 —— 打不开库 → 全缺席
        return sources
    try:
        try:
            row = conn.execute(
                "SELECT project_id, number, volume_id, plan_json FROM chapters "
                "WHERE chapter_id = ?",
                (chapter_id,),
            ).fetchone()
        except sqlite3.Error:
            row = None
        if row is not None:
            sources["found"] = True
            sources["project_id"] = row["project_id"]
            sources["number"] = row["number"]
            sources["volume_id"] = row["volume_id"]
            plan = _parse_json(row["plan_json"])
            sources["plan_json"] = plan if isinstance(plan, dict) else {}

        try:
            draft = conn.execute(
                "SELECT content, version FROM drafts WHERE chapter_id = ? "
                "ORDER BY version DESC LIMIT 1",
                (chapter_id,),
            ).fetchone()
        except sqlite3.Error:
            draft = None
        if draft is not None:
            sources["draft_content"] = draft["content"] or ""
            sources["draft_version"] = draft["version"]

        try:
            run = conn.execute(
                """
                SELECT wr.run_id AS run_id, wr.checkpoint_json AS checkpoint_json
                FROM workflow_runs wr
                JOIN workflows wf ON wf.workflow_id = wr.workflow_id
                WHERE wr.chapter_id = ? AND wf.name = ?
                ORDER BY wr.started_at DESC, wr.run_id DESC
                LIMIT 1
                """,
                (chapter_id, _WRITE_WORKFLOW_NAME),
            ).fetchone()
        except sqlite3.Error:
            run = None
        if run is not None:
            sources["write_run_id"] = run["run_id"]
            checkpoint = _parse_json(run["checkpoint_json"])
            if isinstance(checkpoint, dict):
                scene_plan = checkpoint.get("scene_plan")
                sources["scene_plan"] = scene_plan if isinstance(scene_plan, dict) else None
                length_report = checkpoint.get("length_report")
                sources["length_report"] = (
                    length_report if isinstance(length_report, dict) else None
                )
    finally:
        conn.close()
    return sources


def _load_arc_rows(
    db_path: str | Path,
    *,
    volume_id: str | None,
    project_id: str | None,
) -> list[dict[str, Any]]:
    """读弧级池化输入：弧内逐章 ``(chapter_id, number, checkpoint_json)``（最近一条
    chapter-write run 的 checkpoint）。

    作用域口径（v2）：
    - ``volume_id`` 非空 → 同卷章节（``chapters.volume_id = ?``）；
    - ``volume_id`` 为空 → **同项目无卷章节池**（``project_id = ? AND volume_id IS NULL``）。
      单章自成弧会把章级噪声原样搬到弧级（等于白升格），故退化为「同项目存量无卷章节
      池」这一跨章口径；两个作用域都不成立（无卷且无项目）→ 空列表。

    全容错：打不开库 / 查询失败（含极老库无 ``volumes``）→ 空列表（弧级按
    「未就绪」跳过）。

    返回**整个作用域**的章节行（含当前章之后的章）；「章号 ≤ 当前章」的累计截断在
    :func:`_check_arc_ratio` 里做——那里同时要用未截断的章号判「是否弧内末章」。
    """
    if volume_id:
        where, params = "c.volume_id = ?", (volume_id,)
    elif project_id:
        where, params = "c.project_id = ? AND c.volume_id IS NULL", (project_id,)
    else:
        return []
    try:
        conn = get_connection(db_path)
    except Exception:  # noqa: BLE001 —— 打不开库 → 弧级缺席
        return []
    try:
        try:
            rows = conn.execute(
                f"""
                SELECT c.chapter_id AS chapter_id, c.number AS number,
                       wr.checkpoint_json AS checkpoint_json
                FROM chapters c
                LEFT JOIN workflow_runs wr ON wr.run_id = (
                    SELECT wr2.run_id FROM workflow_runs wr2
                    JOIN workflows wf2 ON wf2.workflow_id = wr2.workflow_id
                    WHERE wr2.chapter_id = c.chapter_id AND wf2.name = ?
                    ORDER BY wr2.started_at DESC, wr2.run_id DESC
                    LIMIT 1
                )
                WHERE {where}
                ORDER BY c.number ASC, c.chapter_id ASC
                """,
                (_WRITE_WORKFLOW_NAME, *params),
            ).fetchall()
        except sqlite3.Error:
            return []
    finally:
        conn.close()
    return [
        {
            "chapter_id": row["chapter_id"],
            "number": row["number"],
            "checkpoint": _parse_json(row["checkpoint_json"]),
        }
        for row in rows
    ]


def _load_bound_pack(db_path: str | Path, project_id: str | None) -> dict[str, Any] | None:
    """读项目绑定题材包行（``pack_id`` / ``version`` / ``payload``）；无 → None。"""
    if not project_id:
        return None
    conn = get_connection(db_path)
    try:
        try:
            row = conn.execute(
                """
                SELECT gp.pack_id, gp.version, gp.payload_json
                FROM projects pj
                JOIN genre_packs gp ON gp.pack_id = pj.genre_pack_id
                WHERE pj.project_id = ?
                """,
                (project_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            # 0025 未跑过的极老库 → 视为未绑定。
            return None
    finally:
        conn.close()
    if row is None:
        return None
    payload = _parse_json(row["payload_json"])
    return {
        "pack_id": row["pack_id"],
        "version": int(row["version"] or 0),
        "payload": payload if isinstance(payload, dict) else {},
    }


def _split_pack(pack: Any) -> tuple[dict[str, Any], str | None, int | None]:
    """``pack`` 参数归一 → ``(payload, pack_id, version)``。

    接受：题材包行 dict（含 ``payload`` 键，service 投影）/ 裸 payload dict /
    None（未传）。无法识别 → 空 payload（各段缺席 → 全跳过）。
    """
    if not isinstance(pack, dict):
        return {}, None, None
    if isinstance(pack.get("payload"), dict):
        version = pack.get("version")
        try:
            version_int = int(version) if version is not None else None
        except (TypeError, ValueError):
            version_int = None
        return pack["payload"], pack.get("pack_id"), version_int
    return pack, pack.get("pack_id"), None


# ---------------------------------------------------------------------------
# 检查 a：配比偏差（章级明细 + 弧级结论）
# ---------------------------------------------------------------------------


def _normalize_declared(declared: dict[str, Any]) -> dict[str, float]:
    """配比声明归一：丢弃非数值 / 非正份额，按总和归一化到 1。

    声明值之和为 0（或全部非法）→ 空 dict（调用方按「未声明」处理）。
    """
    cleaned: dict[str, float] = {}
    for key, value in declared.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value <= 0:
            continue
        cleaned[key.strip()] = float(value)
    total = sum(cleaned.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in cleaned.items()}


def _scene_words(scene_plan: dict[str, Any] | None) -> tuple[dict[str, int], int, int]:
    """``scene_plan`` → ``(words_by_type, untyped_count, typed_count)``。

    字数口径：scene 的 ``target_words``（scene_planner 原始产出；缺席 / 非法 / 负数
    记 0）。``scene_type`` 非字符串或空白 → 计入 untyped（v2 起不再静默：调用方据此
    产 ``GENRE-SCENE-UNTYPED``）。
    """
    scenes = scene_plan.get("scenes") if isinstance(scene_plan, dict) else None
    if not isinstance(scenes, list):
        return {}, 0, 0
    words_by_type: dict[str, int] = {}
    untyped = 0
    typed = 0
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        scene_type = scene.get("scene_type")
        if not isinstance(scene_type, str) or not scene_type.strip():
            untyped += 1
            continue
        typed += 1
        target = scene.get("target_words")
        if isinstance(target, bool) or not isinstance(target, (int, float)):
            target = 0
        key = scene_type.strip()
        words_by_type[key] = words_by_type.get(key, 0) + max(0, int(target))
    return words_by_type, untyped, typed


def _compare_shares(
    declared: dict[str, float], observed: dict[str, float],
) -> tuple[dict[str, float], float, dict[str, Any]]:
    """份额对比（键不对称容错）：交集直比 + catch-all 残差桶归并。

    口径（v2，修 v1 union 口径的键不对称误报）：

    - **交集键**（``declared ∩ observed``，去掉残差键）逐键比对；
    - 声明含 :data:`RATIO_RESIDUAL_KEY`（``other``）时，观测侧的**声明外 scene_type**
      字数并入该残差桶一起比对——写手把 ``other`` 预算花在具体维度上（或写到声明外
      维度）在 v1 下被算成双向偏离（other 负偏离 + 具体键正偏离），这是单章误报的
      机制之一；观测侧缺 ``other`` 时该桶观测值为 0（真的一处 ``other`` 都没标）。
    - **声明了但观测缺席**的键仍按 0 比对（份额缺口是真信号，不进残差桶——否则
      「整类没写」会被残差桶吸收成零偏离）；
    - 声明不含残差键 → 不引入隐式桶，全部按 union 直比（v1 口径）。

    返回 ``(deviations, total_deviation, residual_detail)``；``total_deviation`` =
    各键 ``|观测 - 声明|`` 之和（与 :data:`RATIO_DEVIATION_THRESHOLD` 同口径）。
    """
    residual_key = RATIO_RESIDUAL_KEY
    has_residual = residual_key in declared
    declared_keys = set(declared)
    observed_keys = set(observed)
    direct = sorted((declared_keys & observed_keys) - {residual_key})
    declared_only = sorted((declared_keys - observed_keys) - {residual_key})
    observed_only = sorted((observed_keys - declared_keys) - {residual_key})

    deviations: dict[str, float] = {k: observed[k] - declared[k] for k in direct}
    residual_detail: dict[str, Any] = {
        "key": residual_key if has_residual else None,
        "declared": 0.0,
        "observed": 0.0,
        "declared_only_keys": declared_only,
        "observed_only_keys": observed_only,
        "direct_keys": direct,
    }
    if has_residual:
        declared_residual = declared[residual_key]
        observed_residual = observed.get(residual_key, 0.0) + sum(
            observed[k] for k in observed_only
        )
        deviations[residual_key] = observed_residual - declared_residual
        residual_detail.update({"declared": declared_residual, "observed": observed_residual})
    # 声明了但观测缺席：按 0 比对（缺口是真信号）。
    for key in declared_only:
        deviations[key] = -declared[key]
    if not has_residual:
        # 无残差键 → 声明外维度同样按 0 比对（v1 口径）。
        for key in observed_only:
            deviations[key] = observed[key]

    total = sum(abs(v) for v in deviations.values())
    return deviations, total, residual_detail


def _untyped_issue(untyped: int, typed: int, *, location: str) -> GenreIssue:
    """untyped scene 的显式信号（GENRE-SCENE-UNTYPED，warning）。"""
    return GenreIssue(
        rule_id=RULE_SCENE_UNTYPED,
        category=_CATEGORY_RATIO,
        message=(
            f"本章 scene_plan 有 {untyped} 个 scene 未标注 scene_type"
            f"（共 {untyped + typed} 个），未计入配比核销"
        ),
        location=location,
        suggestion=(
            "按题材配比契约补齐标注（ratio_declarations 非空时逐 scene 必填 scene_type、"
            "取值=声明键）；untyped 会让配比核销静默少算样本。"
        ),
        evidence_refs=["scene_plan.scenes[].scene_type"],
    )


def _check_ratio(
    scene_plan: dict[str, Any] | None,
    ratio_declarations: Any,
    *,
    location: str,
) -> tuple[dict[str, Any], list[GenreIssue]]:
    """**章级**配比明细；返回 ``(明细 dict, issues)``。

    v2 语义修订：章级配比**不产 issue**（``degraded=True`` / ``issue_emitted=False``）
    ——单章 2~6 个 scene 对上 5 个声明键的总偏离均值 40% 是采样噪声，弧级（见
    :func:`_check_arc_ratio`）才是判据。``issues`` 至多含 untyped scene 信号。

    ``checked=False`` + ``reason`` 表示跳过（未声明配比 / 无 scene_plan /
    无任何 scene 声明 scene_type / 无 target_words）；``checked=True`` 时给出
    declared / observed / deviations / total_deviation / residual。
    """
    detail: dict[str, Any] = {
        "scope": "chapter",
        "checked": False,
        "reason": None,
        "degraded": True,
        "issue_emitted": False,
        "degraded_note": (
            "章级配比只记明细；GENRE-RATIO-DEVIATION 只在弧级（arc_check）产出"
            "（v2 口径修订：章级 scene 数不足以支撑 5 键配比判定）。"
        ),
        "declared": {},
        "observed": {},
        "deviations": {},
        "residual": None,
        "total_deviation": 0.0,
        "threshold": RATIO_DEVIATION_THRESHOLD,
        "deviation_exceeded": False,
        "typed_scene_count": 0,
        "untyped_scene_count": 0,
        "typed_words": 0,
    }
    if not isinstance(ratio_declarations, dict) or not ratio_declarations:
        detail["reason"] = "ratio_declarations_not_declared"
        return detail, []
    declared = _normalize_declared(ratio_declarations)
    if not declared:
        detail["reason"] = "ratio_declarations_invalid"
        return detail, []
    detail["declared"] = declared

    scenes = scene_plan.get("scenes") if isinstance(scene_plan, dict) else None
    if not isinstance(scenes, list) or not scenes:
        detail["reason"] = "no_scene_plan"
        return detail, []

    words_by_type, untyped, typed = _scene_words(scene_plan)
    typed_words = sum(words_by_type.values())
    detail.update(
        {
            "untyped_scene_count": untyped,
            "typed_scene_count": typed,
            "typed_words": typed_words,
        }
    )
    # untyped 显式化（v2）：声明了配比即 scene_type 契约生效，缺失不再是静默跳过。
    issues: list[GenreIssue] = []
    if untyped:
        issues.append(_untyped_issue(untyped, typed, location=location))

    if not words_by_type:
        detail["reason"] = "no_scene_type"
        return detail, issues
    if typed_words <= 0:
        # 有 scene_type 但没有任何 scene 声明 target_words（预算口径由 writer 装配
        # 兜底等分，checkpoint 里的 scene_plan 是 scene_planner 原始产出）→ 无可核。
        detail["reason"] = "no_scene_target_words"
        return detail, issues

    observed = {k: v / typed_words for k, v in words_by_type.items()}
    deviations, total, residual = _compare_shares(declared, observed)
    detail.update(
        {
            "checked": True,
            "observed": observed,
            "deviations": deviations,
            "total_deviation": round(total, 6),
            "deviation_exceeded": total > RATIO_DEVIATION_THRESHOLD,
            "residual": residual,
        }
    )
    return detail, issues


def _render_shares(
    keys: list[str], declared: dict[str, float], observed: dict[str, float],
) -> str:
    """``k 声明 X% / 实际 Y%`` 串（确定性顺序，便于报告 diff）。"""
    return "、".join(
        f"{k} 声明 {declared.get(k, 0.0):.0%} / 实际 {observed.get(k, 0.0):.0%}"
        for k in keys
    )


def _check_arc_ratio(
    rows: list[dict[str, Any]],
    ratio_declarations: Any,
    *,
    scope_key: str,
    volume_id: str | None,
    current_number: int | None,
    location: str,
) -> tuple[dict[str, Any], list[GenreIssue]]:
    """**弧级**配比核销（v2 主项）；返回 ``(明细 dict, issues)``。

    池化口径：``rows`` 内**章号 ≤ 当前章**（累计口径——核第 N 章时只用弧内
    第 1~N 章的既有产出，不把后续章节算进来，否则重评旧章会看到未来数据）中，
    每章最近一条 chapter-write run 的 ``scene_plan`` 里带 ``scene_type`` 的 scene
    字数按类型累加，再归一成份额与声明比对（与章级同一字数口径，只是样本合并到弧）。

    结论就绪判据（``sample_ready or is_arc_end``）：
    - ``sample_ready``：弧内累计 typed scene ≥ :data:`ARC_MIN_TYPED_SCENES`；
    - ``is_arc_end``：``current_number`` 已是**整个弧**的最大章号（弧内末章——口径上
      弧阶段性完整；判据用弧内全部章号，不受累计截断影响）。

    两者都不成立 → ``checked=False`` / ``reason="arc_not_ready"``（只记
    ``typed_scene_count`` 等事实，不下结论）。就绪后偏离超阈值 → 产
    ``GENRE-RATIO-DEVIATION``（scope 弧级；样本偏薄时在 message 标注）。

    ``pending`` 恒登记 :data:`SETTLEMENT_PENDING_NOTE`（settlement 无数据通路）。
    """
    scope_numbers = [r["number"] for r in rows if isinstance(r["number"], int)]
    is_arc_end = bool(scope_numbers) and isinstance(current_number, int) and (
        current_number >= max(scope_numbers)
    )
    volume_chapter_count = len(rows)
    if isinstance(current_number, int):
        rows = [
            r for r in rows
            if not isinstance(r["number"], int) or r["number"] <= current_number
        ]
    detail: dict[str, Any] = {
        "scope": "arc",
        "scope_key": scope_key,
        "volume_id": volume_id,
        "chapter_count": len(rows),
        "volume_chapter_count": volume_chapter_count,
        "chapters_with_scene_plan": 0,
        "chapters_without_scene_type": 0,
        "is_arc_end": is_arc_end,
        "sample_ready": False,
        "min_typed_scenes": ARC_MIN_TYPED_SCENES,
        "checked": False,
        "reason": None,
        "declared": {},
        "observed": {},
        "deviations": {},
        "residual": None,
        "total_deviation": 0.0,
        "threshold": RATIO_DEVIATION_THRESHOLD,
        "deviation_exceeded": False,
        "typed_scene_count": 0,
        "untyped_scene_count": 0,
        "typed_words": 0,
        "pending": [SETTLEMENT_PENDING_NOTE],
    }
    if not isinstance(ratio_declarations, dict) or not ratio_declarations:
        detail["reason"] = "ratio_declarations_not_declared"
        return detail, []
    declared = _normalize_declared(ratio_declarations)
    if not declared:
        detail["reason"] = "ratio_declarations_invalid"
        return detail, []

    words_by_type: dict[str, int] = {}
    typed = 0
    untyped = 0
    with_plan = 0
    without_type = 0
    for row in rows:
        checkpoint = row.get("checkpoint")
        scene_plan = checkpoint.get("scene_plan") if isinstance(checkpoint, dict) else None
        if not isinstance(scene_plan, dict):
            continue
        with_plan += 1
        chapter_words, chapter_untyped, chapter_typed = _scene_words(scene_plan)
        if chapter_untyped and not chapter_typed:
            without_type += 1
        typed += chapter_typed
        untyped += chapter_untyped
        for key, value in chapter_words.items():
            words_by_type[key] = words_by_type.get(key, 0) + value

    typed_words = sum(words_by_type.values())
    detail.update(
        {
            "declared": declared,
            "chapters_with_scene_plan": with_plan,
            "chapters_without_scene_type": without_type,
            "typed_scene_count": typed,
            "untyped_scene_count": untyped,
            "typed_words": typed_words,
            "sample_ready": typed >= ARC_MIN_TYPED_SCENES,
        }
    )
    if not (detail["sample_ready"] or is_arc_end):
        detail["reason"] = "arc_not_ready"
        return detail, []
    if not words_by_type:
        detail["reason"] = "arc_no_scene_type"
        return detail, []
    if typed_words <= 0:
        detail["reason"] = "arc_no_scene_target_words"
        return detail, []

    observed = {k: v / typed_words for k, v in words_by_type.items()}
    deviations, total, residual = _compare_shares(declared, observed)
    exceeded = total > RATIO_DEVIATION_THRESHOLD
    detail.update(
        {
            "checked": True,
            "observed": observed,
            "deviations": deviations,
            "residual": residual,
            "total_deviation": round(total, 6),
            "deviation_exceeded": exceeded,
        }
    )
    if not exceeded:
        return detail, []

    scope_label = f"卷 {volume_id}" if volume_id else "项目无卷章节池"
    sample_label = (
        "样本充分" if detail["sample_ready"]
        else f"样本偏薄（typed scene {typed} < {ARC_MIN_TYPED_SCENES}，仅作方向提示）"
    )
    rendered = _render_shares(sorted(deviations), declared, observed)
    note = f"；另有 {untyped} 个 scene 未标注 scene_type（未计入配比）" if untyped else ""
    issue = GenreIssue(
        rule_id=RULE_RATIO_DEVIATION,
        category=_CATEGORY_RATIO,
        message=(
            f"弧级配比总偏离 {total:.1%} > 阈值 {RATIO_DEVIATION_THRESHOLD:.0%}"
            f"（{scope_label}，{len(rows)} 章 / {typed} 个 typed scene，{sample_label}）："
            f"{rendered}{note}"
        ),
        location=location,
        suggestion=(
            "按题材包 ratio_declarations 重排卷内 scene 的 scene_type 与 target_words 分摊"
            "（规划期硬约束）；单章偏离属采样噪声，以弧级口径为准，或人工确认声明需更新。"
        ),
        evidence_refs=[
            f"arc_scope:{scope_key}",
            f"arc_typed_scenes:{typed}",
            *(f"scene_type:{k}" for k in sorted(deviations)),
        ],
    )
    return detail, [issue]


# ---------------------------------------------------------------------------
# 检查 b：红线（字数带一致性 + 红线文本命中）
# ---------------------------------------------------------------------------


def _normalize_text(value: Any) -> str:
    """文本归一（折叠空白 + 去首尾）；非字符串 → 空串。"""
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _collect_redline_corpus(sources: dict[str, Any]) -> dict[str, Any]:
    """红线匹配语料（v2 扩面）→ ``{"text", "sources", "chars"}``。

    语料构成（按序拼接、空白归一）：
    1. ``drafts`` 最新草稿**整章正文**（v2 新增——v1 只有下面第 3/4 项，实测 ~140 字，
       21 章零命中时无法区分「真无命中」与「语料太小」）；
    2. ``plan_json`` 关键段：``chapter_goal`` / ``core_conflict`` / ``turning_point`` /
       ``deviations`` / ``key_beats[].purpose``；
    3. ``length_report``（JSON 串）。

    ``chars`` 是归一后语料长度，随明细回报（``corpus_chars``），供判断「0 命中」的
    可信度。语料规模由草稿长度天然约束（单章数千字），不再另设上限。
    """
    parts: list[str] = []
    names: list[str] = []

    draft = sources.get("draft_content")
    if isinstance(draft, str) and draft.strip():
        parts.append(_normalize_text(draft))
        names.append("draft")

    plan = sources.get("plan_json")
    plan_parts: list[str] = []
    if isinstance(plan, dict):
        for key in _PLAN_CORPUS_FIELDS:
            value = plan.get(key)
            if isinstance(value, str) and value.strip():
                plan_parts.append(_normalize_text(value))
            elif isinstance(value, list):
                for entry in value:
                    text = _normalize_text(entry)
                    if text:
                        plan_parts.append(text)
        beats = plan.get("key_beats")
        if isinstance(beats, list):
            for beat in beats:
                if isinstance(beat, dict):
                    text = _normalize_text(beat.get("purpose"))
                    if text:
                        plan_parts.append(text)
    if plan_parts:
        parts.append(" | ".join(plan_parts))
        names.append("plan_json")

    length_report = sources.get("length_report")
    if isinstance(length_report, dict):
        parts.append(json.dumps(length_report, ensure_ascii=False, sort_keys=True))
        names.append("length_report")

    text = _normalize_text(" | ".join(p for p in parts if p))
    return {"text": text, "sources": names, "chars": len(text)}


def _match_redlines(redlines: Any, corpus: str) -> list[str]:
    """红线文本命中口径（v1 起不变，v2 只扩语料）：整条红线归一后为语料子串即命中。

    口径说明（写在代码里，避免被当成语义判定）：roadmap 举的例（「压抑段≤2章」）是
    **规则陈述句**，无法由正文判定章数；本层不做语义判定，只做「红线条目原文出现在
    语料里」的确定性子串匹配（大小写不敏感）。故命中率取决于红线是否被写进正文 /
    偏差注记——v1 的「21 章零命中」不足信是因为语料只有 ~140 字（见
    :func:`_collect_redline_corpus`），v2 扩到整章正文后零命中才是可信结论。
    """
    if not isinstance(redlines, list) or not corpus:
        return []
    corpus_norm = corpus.lower()
    hits: list[str] = []
    for raw in redlines:
        text = _normalize_text(raw)
        if text and text.lower() in corpus_norm:
            hits.append(text)
    return hits


def _check_redlines(
    payload: dict[str, Any],
    sources: dict[str, Any],
    *,
    location: str,
) -> tuple[dict[str, Any] | None, list[GenreIssue]]:
    """红线检查；返回 ``(明细 dict | None, issues)``。

    ``pacing`` 段缺席 → ``(None, [])``（该题材包未声明节奏公约，整项不展示）。
    """
    pacing = payload.get("pacing")
    if not isinstance(pacing, dict) or not pacing:
        return None, []

    issues: list[GenreIssue] = []
    detail: dict[str, Any] = {}

    # b1) 字数带一致性（pacing.chapter_word_band）
    band = pacing.get("chapter_word_band")
    band_detail: dict[str, Any] = {"checked": False, "reason": None}
    declared_band: dict[str, int] = {}
    if isinstance(band, dict):
        for key in ("low", "high"):
            value = band.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                declared_band[key] = value
    length_report = sources.get("length_report")
    if length_report is not None and isinstance(length_report.get("visible_chars"), int):
        word_count = int(length_report["visible_chars"])
        source = "length_report"
    elif isinstance(sources.get("draft_content"), str):
        word_count = visible_chars(sources["draft_content"])
        source = "draft"
    else:
        word_count = None
        source = None
    band_detail.update({"declared": declared_band, "word_count": word_count, "source": source})
    if not {"low", "high"} <= set(declared_band):
        band_detail["reason"] = "chapter_word_band_not_declared"
    elif word_count is None:
        band_detail["reason"] = "no_draft"
    else:
        within = declared_band["low"] <= word_count <= declared_band["high"]
        band_detail.update({"checked": True, "within_band": within})
        if not within:
            side = "低于下限" if word_count < declared_band["low"] else "超出上限"
            issues.append(
                GenreIssue(
                    rule_id=RULE_WORD_BAND_DEVIATION,
                    category=_CATEGORY_PACING,
                    message=(
                        f"实际字数 {word_count}（{source}）{side}题材包字数带 "
                        f"{declared_band['low']}~{declared_band['high']}"
                    ),
                    location=location,
                    suggestion="对齐题材包 pacing.chapter_word_band，或人工确认该章例外。",
                    evidence_refs=[f"word_band:{declared_band['low']}-{declared_band['high']}"],
                )
            )
    detail["word_band"] = band_detail

    # b2) 红线文本命中（pacing.redlines × 整章正文 + 计划关键段 + 偏差注记 + length_report）
    redlines = pacing.get("redlines")
    redline_detail: dict[str, Any] = {
        "checked": False,
        "reason": None,
        "declared": [],
        "hits": [],
        "corpus_chars": 0,
        "corpus_sources": [],
    }
    if isinstance(redlines, list) and redlines:
        declared_redlines = [t for t in (_normalize_text(r) for r in redlines) if t]
        corpus = _collect_redline_corpus(sources)
        hits = _match_redlines(declared_redlines, corpus["text"])
        redline_detail.update(
            {
                "checked": True,
                "declared": declared_redlines,
                "hits": hits,
                "corpus_chars": corpus["chars"],
                "corpus_sources": corpus["sources"],
            }
        )
        for hit in hits:
            issues.append(
                GenreIssue(
                    rule_id=RULE_REDLINE_HIT,
                    category=_CATEGORY_PACING,
                    message=f"红线文本命中该章正文 / 计划语料：{hit}",
                    location=location,
                    suggestion="人工确认该章是否触碰题材节奏红线（本层只做确定性文本匹配）。",
                    evidence_refs=[f"redline_corpus:{'+'.join(corpus['sources']) or 'empty'}"],
                )
            )
    else:
        redline_detail["reason"] = "redlines_not_declared"
    detail["redlines"] = redline_detail

    return detail, issues


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------


def verify_chapter(
    db_path: str | Path,
    chapter_id: str,
    pack: Any = None,
) -> GenreCheckResult:
    """核销单章（report-only）。

    参数：
        db_path / chapter_id：目标章。
        pack：题材包行 dict（含 ``payload``）/ 裸 payload dict；省略（``None``）时
            读 ``projects.genre_pack_id`` 的绑定题材包。

    返回 :class:`GenreCheckResult`；**任何读路径异常都不抛错**（``error`` 字段
    记录原因，``checked=False``），调用方据此整段跳过或只做展示。

    v2 起本入口除章级两项外，还按 ``chapters.volume_id`` 读弧内各章并出**弧级**
    配比结论（就绪判据见 :func:`_check_arc_ratio`）——仍逐章调用，弧级统计在卷内
    累计样本上算。

    零行为变化口径：未绑定题材包 → ``bound=False`` / 零 issue（不产生任何条目）。
    """
    result = GenreCheckResult()
    location = str(chapter_id)
    try:
        sources = _load_chapter_sources(db_path, chapter_id)
    except Exception as exc:  # noqa: BLE001 —— 读路径整体失败（不阻断评审）
        result.error = f"read_failed: {exc}"
        result.skipped.append("chapter_sources_unavailable")
        return result
    if not sources["found"]:
        result.skipped.append("chapter_not_found")
        return result

    if pack is None:
        try:
            bound = _load_bound_pack(db_path, sources.get("project_id"))
        except Exception as exc:  # noqa: BLE001
            result.error = f"binding_read_failed: {exc}"
            result.skipped.append("genre_pack_unavailable")
            return result
        if bound is None:
            result.skipped.append("no_binding")
            return result
        payload, pack_id, version = (
            bound["payload"], bound["pack_id"], bound["version"],
        )
    else:
        payload, pack_id, version = _split_pack(pack)

    result.bound = True
    result.pack_id = pack_id
    result.pack_version = version

    ratio_declarations = payload.get("ratio_declarations")

    ratio_detail, ratio_issues = _check_ratio(
        sources.get("scene_plan"),
        ratio_declarations,
        location=location,
    )
    result.ratio_check = ratio_detail
    if not ratio_detail["checked"]:
        result.skipped.append(str(ratio_detail["reason"]))

    # 弧级配比（v2 主项）：声明了配比才读弧内各章（未声明 → 零额外查询，与 v1 同）。
    volume_id = sources.get("volume_id")
    project_id = sources.get("project_id")
    if isinstance(ratio_declarations, dict) and ratio_declarations:
        arc_rows = _load_arc_rows(db_path, volume_id=volume_id, project_id=project_id)
    else:
        arc_rows = []
    arc_detail, arc_issues = _check_arc_ratio(
        arc_rows,
        ratio_declarations,
        scope_key=volume_id or f"{_VOLUMELESS_SCOPE_PREFIX}{project_id or '<unknown>'}",
        volume_id=volume_id,
        current_number=sources.get("number"),
        location=location,
    )
    result.arc_check = arc_detail
    if not arc_detail["checked"]:
        result.skipped.append(f"arc_ratio:{arc_detail['reason']}")

    redline_detail, redline_issues = _check_redlines(payload, sources, location=location)
    result.redline_check = redline_detail
    if redline_detail is None:
        result.skipped.append("pacing_not_declared")
    else:
        if not redline_detail["word_band"]["checked"]:
            result.skipped.append(f"word_band:{redline_detail['word_band']['reason']}")
        if not redline_detail["redlines"]["checked"]:
            result.skipped.append(f"redlines:{redline_detail['redlines']['reason']}")

    result.issues = [*ratio_issues, *arc_issues, *redline_issues]
    # checked = 至少有一项真的核过（章级配比 / 弧级配比 / 字数带 / 红线文本任一出结论，
    # 或章级核出 untyped 事实）；只声明了段但无输入（无 scene_type / 无草稿）不算核过。
    word_band_checked = bool(
        redline_detail is not None and redline_detail["word_band"]["checked"]
    )
    redlines_checked = bool(
        redline_detail is not None and redline_detail["redlines"]["checked"]
    )
    result.checked = bool(
        ratio_detail["checked"]
        or ratio_detail["untyped_scene_count"]
        or arc_detail["checked"]
        or word_band_checked
        or redlines_checked
    )
    return result
