# -*- coding: utf-8 -*-
"""open_volume —— 「开新卷」策展入口（CLI，默认 dry-run）。

## 为什么需要它

能开卷的只有 ``project-init`` 工作流，而它的卷节点把**卷号写死为 1**
（``packages/workflows/project_init/pipeline.py``：``_run_volume_outliner`` 的
``{"number": 1, ...}`` 兜底、``_upsert_volume`` 按 ``(project, number)`` upsert、
``volume_raw.get("number") or 1``）——重跑 init 只会覆盖卷 1。本脚本补上这个缺口：
把一份**卷 brief**（卷头 + 该卷各章的策展大纲）校验后落库，用于开卷 2 / 卷 3 …。

## 用法

::

    python scripts/open_volume.py --project prj_xxxx --brief vol2.json            # 体检（dry-run，不写库）
    python scripts/open_volume.py --project prj_xxxx --brief vol2.json --apply    # 落库
    python scripts/open_volume.py --project prj_xxxx --brief vol2.json --apply --json
    python scripts/open_volume.py --db data/novelos.db --project prj_xxxx --brief vol2.json

选项：``--db``（默认 ``data/novelos.db``）、``--apply``、``--json``、
``--seal-active``（见下「active 卷单例」）。

退出码：``0`` = 校验通过（dry-run）/ 写入完成；``1`` = 校验不通过（**拒写**）；
``2`` = 参数错误 / brief 读不出或不是合法 JSON / 数据库打不开 / 写入中断。

## brief JSON 契约

```json
{
  "volume": {"number": 2, "title": "…", "arc_summary": "…"},
  "chapters": [
    {"number": 25, "title": "…",
     "outline": {"chapter_goal": "…", "core_conflict": "…", "turning_point": "…",
                 "expected_role": "setup|escalation|transition|climax|resolution",
                 "key_beats": ["…", "…"],
                 "character_changes_planned": ["…"],
                 "information_releases": ["…"],
                 "expected_word_count": 2500}}
  ]
}
```

- ``volume.number`` 必填（int ≥ 1）；``title`` / ``arc_summary`` 可选（空串等于不写）。
- ``chapters`` 非空，按**升序连续**给号（25, 26, 27 …，不允许跳号或乱序）。
- ``outline`` 的 8 个字段**全部必填**（与上面的契约同形）；
  ``chapter_goal / core_conflict / turning_point`` 非空字符串，``expected_role`` 取
  枚举值（合法集合见 ``VALID_ROLES``：project_init 的白名单
  ``setup / escalation / transition / climax / resolution / other``，另收 ``turn``
  ——``outline_check`` 的「高潮位」口径含 turn，写它才能被收束段豁免识别），
  ``key_beats`` 非空字符串数组，``character_changes_planned / information_releases``
  为字符串数组（可为空数组），``expected_word_count`` 为正整数。
- 额外字段（``hook_handling`` / ``notes_for_planner`` …）原样保留写入，不校验不丢。
- **不在契约内的字段名会被忽略**：``outline_check`` 的判据读的是
  ``chapter_goal / key_beats / expected_role / hook_handling``，本脚本写的就是这些
  同名键，无改名映射。

## 校验清单（dry-run 与 --apply 都跑；有 error 则 --apply 拒写）

| 码 | 判据 |
|---|---|
| `BRIEF-*` | brief 顶层结构：`volume` 为对象、`chapters` 非空数组、字段类型合法 |
| `VOLUME-NUMBER` | `volume.number` 为 int ≥ 1 |
| `VOLUME-SEALED` | 同号卷已存在且 `status='sealed'`（终态归档，不可承接新开卷）→ 拒写 |
| `VOLUME-ACTIVE` | 项目已存在**别的** active 卷 → 拒写（提示 seal；`--seal-active` 可让本工具封存卷号更小的那个） |
| `VOLUME-NUMBER-SKIP`（warning） | 卷号跳跃（库中最大卷号 +1 之外）/ 补洞，提示但不阻断 |
| `CHAPTER-NUMBER-*` | 章号 int ≥ 1、brief 内不重复、升序连续 |
| `CHAPTER-CONFLICT` | 该章号在库中已存在且**属于别的卷** → 拒写 |
| `CHAPTER-NO-OUTLINE` | 该章号已存在但无 `outline_json`（策展面缺失，可被本工具补写） |
| `CHAPTER-TITLE-DIFF`（warning） | 既有章标题与 brief 不一致——**只提示不改写**（见下「更新口径」） |
| `OUTLINE-*` | 8 个必填字段齐全 / `expected_role` 合法 / `key_beats` 非空 |

## 质量体检（报告器，不是闸门）

落库前用 ``scripts/outline_check.py`` 的**同源判据**（直接 import
``packages.core.quality.outline_check.check_outline``，不 shell 调用）对 brief 做一遍
体检并把命中项 + 证据原文打印出来。**alert 只提示不阻断**（同 ``outline_check`` 的
定位：算子匹配的是字面形态，须人工核对证据）。
命中项按**位置**编号（ch1..chN），报告里会打印位置 → 全书章号的映射行。

## 写入路径（全部走既有 service）

| 动作 | 路径 |
|---|---|
| 封存旧 active 卷（仅 `--seal-active`） | `VolumeService.seal()` |
| 建卷 / 改卷标题 | `VolumeService.create()` / `VolumeService.update()` |
| 建章 | `ChapterService.create()`（`ChapterCreate`） |
| 写策展大纲 | `ChapterService.update(ChapterUpdate(outline_json=…))` |
| 挂章到卷 | `VolumeService.assign_chapter()`（语义=`chapters.volume_id`） |

**为什么有直写 SQL（唯一一处）**：``Volumes.arc_summary`` 这一列
**没有任何 service / 路由入口**（`VolumeCreate` / `VolumeUpdate` 都不含该字段，
0020 迁移加列后只有 init 链路的 plot_event 描述用过它）——brief 里的
``volume.arc_summary`` 若不走直写就会「收下即忘」（本仓已定性过的缺陷形状）。
故仅对**该列**做一条定点 `UPDATE volumes SET arc_summary=…`，且只在值有变化时写。
``chapters.outline_json`` 则**不需要**直写：``ChapterUpdate`` 已带该字段
（策展面口径见 0028 迁移与 AGENTS.md 硬规则「一列不得两用」）。

## 幂等与更新口径

- 同 number 的既有卷 → **复用**（不重复建卷）；标题不一致时走 `VolumeService.update`
  改标题并打印 diff。
- 同 number 的既有章 → **复用**（不重复建章）：`volume_id` 已是目标卷，或为 NULL
  （上次运行半途中断的残留，本次补挂 + 补写大纲）。
- 既有章**只在 `outline_json` 与 brief 不一致时**才 UPDATE（打印逐字段 diff 摘要）；
  `plan_json`（生成面）本脚本**不写**——它由 chapter-plan 工作流整体覆盖，
  写半成品只会复发「一列两用」。
- 既有章的**标题**不一致 → 仅告警 `CHAPTER-TITLE-DIFF`，不改写（本命令只承接
  「策展大纲」这一类改动；改标题请走 `PATCH /api/chapters/{id}`）。

## 与 project-init 的关系

``scripts/open_volume.py`` 是 init **之外**的独立入口，不调用任何工作流、不写
``workflow_runs``；它只写 ``volumes`` / ``chapters`` 两张表 —— 因此可安全用于
「已写完第一卷、要开第二卷」的场景（重跑 init 会覆盖卷 1，本脚本不会）。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.db import get_connection  # noqa: E402
from packages.core.ids import now_iso  # noqa: E402
from packages.core.quality.outline_check import Finding, check_outline  # noqa: E402
from packages.domain.chapter import (  # noqa: E402
    ChapterCreate,
    ChapterNumberConflict,
    ChapterService,
    ChapterUpdate,
)
from packages.domain.volume import (  # noqa: E402
    VolumeConflictError,
    VolumeCreate,
    VolumeNotFoundError,
    VolumeSealedError,
    VolumeService,
    VolumeUpdate,
)
from scripts.outline_check import _names as _actor_names  # noqa: E402

DEFAULT_DB = "data/novelos.db"

# --- brief 契约 ---------------------------------------------------------------

# outline 必填字段（与模块 docstring 的契约同形）。
OUTLINE_REQUIRED_FIELDS = (
    "chapter_goal", "core_conflict", "turning_point", "expected_role",
    "key_beats", "character_changes_planned", "information_releases",
    "expected_word_count",
)
# 三个必须非空的叙述字段。
OUTLINE_TEXT_FIELDS = ("chapter_goal", "core_conflict", "turning_point")
# 必须为字符串数组的字段（key_beats 另加「非空」约束）。
OUTLINE_LIST_FIELDS = ("key_beats", "character_changes_planned", "information_releases")
# expected_role 合法取值：project_init._normalize_chapter_seeds 的白名单
# （setup/escalation/climax/resolution/transition/other，本仓唯一在码的枚举）
# ∪ {"turn"}——outline_check 的高潮/转折位口径含 turn，写它才能被收束段豁免识别。
VALID_ROLES = ("setup", "escalation", "transition", "climax", "resolution", "other", "turn")
# 写入 outline_json 的 schema 标记（与 project_init persist 同款，便于下游识别形态）。
OUTLINE_SCHEMA_VERSION = "chapter-outline.v1"

# 待建卷的归属哨兵：用于「既有章是否已属于目标卷」的比对（真 id 由 INSERT 后产生）。
_PENDING_VOLUME = "__pending_volume__"

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_USAGE = 2


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    """一条校验结论。``level='error'`` 阻断 --apply；``'warning'`` 只提示。"""

    code: str
    level: str
    message: str

    def to_dict(self) -> dict:
        return {"code": self.code, "level": self.level, "message": self.message}


@dataclass
class DbState:
    """落库前读到的现状快照。"""

    project_exists: bool = False
    volumes: list[dict] = field(default_factory=list)
    chapters_by_number: dict[int, dict] = field(default_factory=dict)

    @property
    def max_volume_number(self) -> int:
        return max((int(v["number"]) for v in self.volumes), default=0)


@dataclass
class Plan:
    """一分 brief 的落库计划（dry-run 与 --apply 共用同一份计算）。"""

    project_id: str
    volume: dict
    chapters: list[dict]
    seals: list[dict]
    issues: list[Issue]
    findings: list[Finding]
    actors: dict

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# brief 解析与结构校验
# ---------------------------------------------------------------------------


def load_brief(path: Path) -> Any:
    """读 brief JSON；文件缺失 / 非 UTF-8 / JSON 语法错误 → ValueError。"""
    if not path.exists():
        raise ValueError(f"brief 文件不存在: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - 平台相关
        raise ValueError(f"brief 读不出: {path} ({exc})") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"brief 不是合法 JSON: {exc}") from exc


def _coerce_int(value: Any) -> int | None:
    """宽松取整数（int / 纯数字字符串）；bool 与非数字 → None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def validate_outline(outline: Any, label: str, issues: list[Issue]) -> dict:
    """校验单章 outline 的 8 个必填字段；返回原样 dict（额外字段保留）。"""
    if not isinstance(outline, dict):
        issues.append(Issue("OUTLINE-SHAPE", "error", f"{label}: outline 必须是对象"))
        return {}
    out = dict(outline)
    for name in OUTLINE_TEXT_FIELDS:
        value = out.get(name)
        if not isinstance(value, str) or not value.strip():
            issues.append(Issue(
                "OUTLINE-FIELD", "error", f"{label}: outline.{name} 必填且为非空字符串"))
    role = out.get("expected_role")
    if role not in VALID_ROLES:
        issues.append(Issue(
            "OUTLINE-ROLE", "error",
            f"{label}: outline.expected_role={role!r} 非法，合法取值 {'/'.join(VALID_ROLES)}"))
    for name in OUTLINE_LIST_FIELDS:
        value = out.get(name)
        if name not in out:
            issues.append(Issue(
                "OUTLINE-FIELD", "error", f"{label}: outline.{name} 缺失（契约必填，可给空数组）"))
            continue
        if not isinstance(value, list) or any(not isinstance(b, str) or not b.strip() for b in value):
            issues.append(Issue(
                "OUTLINE-LIST", "error",
                f"{label}: outline.{name} 必须是字符串数组"
                + ("（且非空）" if name == "key_beats" else "")))
        elif name == "key_beats" and not value:
            issues.append(Issue("OUTLINE-BEATS", "error", f"{label}: outline.key_beats 不得为空"))
    if "expected_word_count" not in out:
        issues.append(Issue(
            "OUTLINE-FIELD", "error", f"{label}: outline.expected_word_count 缺失（契约必填）"))
    else:
        wc = _coerce_int(out["expected_word_count"])
        if wc is None or wc <= 0:
            issues.append(Issue(
                "OUTLINE-WORD-COUNT", "error",
                f"{label}: outline.expected_word_count={out['expected_word_count']!r} 必须是正整数"))
    return out


def validate_brief(brief: Any) -> tuple[dict, list[dict], list[Issue]]:
    """brief 结构校验；返回 (volume_spec, chapter_specs, issues)。

    结构性错误（顶层形状）会直接短路——没有卷号 / 章列表就无法做任何现状比对，
    继续拼报告只会给出误导性的「计划」。
    """
    issues: list[Issue] = []
    if not isinstance(brief, dict):
        return {}, [], [Issue("BRIEF-SHAPE", "error", "brief 顶层必须是 JSON 对象")]

    raw_volume = brief.get("volume")
    if not isinstance(raw_volume, dict):
        return {}, [], [Issue("BRIEF-SHAPE", "error", "brief.volume 必须是对象")]

    volume: dict = {
        "number": None,
        "title": raw_volume.get("title"),
        "arc_summary": raw_volume.get("arc_summary") or "",
    }
    vnum = _coerce_int(raw_volume.get("number"))
    if vnum is None or vnum < 1:
        issues.append(Issue(
            "VOLUME-NUMBER", "error",
            f"volume.number={raw_volume.get('number')!r} 非法（必须是 ≥1 的整数）"))
    else:
        volume["number"] = vnum
    if volume["title"] is not None and not isinstance(volume["title"], str):
        issues.append(Issue("VOLUME-TITLE", "error", "volume.title 必须是字符串"))
        volume["title"] = None
    if not isinstance(volume["arc_summary"], str):
        issues.append(Issue("VOLUME-ARC-SUMMARY", "error", "volume.arc_summary 必须是字符串"))
        volume["arc_summary"] = ""
    if not (volume["title"] or "").strip():
        issues.append(Issue("VOLUME-TITLE-EMPTY", "warning", "volume.title 为空（将写 NULL）"))

    raw_chapters = brief.get("chapters")
    if not isinstance(raw_chapters, list) or not raw_chapters:
        issues.append(Issue("BRIEF-SHAPE", "error", "brief.chapters 必须是非空数组"))
        return volume, [], issues

    chapters: list[dict] = []
    for idx, item in enumerate(raw_chapters):
        if not isinstance(item, dict):
            issues.append(Issue("CHAPTER-SHAPE", "error", f"chapters[{idx}] 必须是对象"))
            continue
        number = _coerce_int(item.get("number"))
        label = f"chapters[{idx}]" if number is None else f"chapters[{idx}](#{number})"
        if number is None or number < 1:
            issues.append(Issue(
                "CHAPTER-NUMBER", "error",
                f"{label}: number={item.get('number')!r} 非法（必须是 ≥1 的整数）"))
            continue
        title = item.get("title")
        if title is not None and not isinstance(title, str):
            issues.append(Issue("CHAPTER-TITLE-TYPE", "error", f"{label}: title 必须是字符串"))
            title = None
        outline = validate_outline(item.get("outline"), label, issues)
        chapters.append({"number": number, "title": title, "outline": outline})

    _validate_chapter_numbers(chapters, issues)
    return volume, chapters, issues


def _validate_chapter_numbers(chapters: list[dict], issues: list[Issue]) -> None:
    """章号：brief 内不重复、升序、连续（25,26,27…）。"""
    nums = [c["number"] for c in chapters]
    dup = sorted({n for n in nums if nums.count(n) > 1})
    if dup:
        issues.append(Issue(
            "CHAPTER-DUP", "error", f"brief 内章号重复: {dup}（每章只能出现一次）"))
        return
    if nums != sorted(nums):
        issues.append(Issue(
            "CHAPTER-ORDER", "error", f"brief 内章号必须升序: {nums}"))
        return
    if nums and nums != list(range(nums[0], nums[0] + len(nums))):
        gaps = [n for n in range(nums[0], nums[-1] + 1) if n not in set(nums)]
        issues.append(Issue(
            "CHAPTER-GAP", "error",
            f"brief 内章号不连续（{nums[0]}..{nums[-1]}，缺 {gaps}）"))


# ---------------------------------------------------------------------------
# 现状读取
# ---------------------------------------------------------------------------


def load_state(db_path: Path | str, project_id: str) -> tuple[DbState, dict]:
    """读现状（project / volumes / chapters）+ actors 名字（供卷纲体检用）。"""
    state = DbState()
    conn = get_connection(db_path)
    try:
        state.project_exists = conn.execute(
            "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)).fetchone() is not None
        for row in conn.execute(
            "SELECT volume_id, number, title, status, arc_summary FROM volumes "
            "WHERE project_id = ? ORDER BY number ASC", (project_id,),
        ):
            state.volumes.append(dict(row))
        for row in conn.execute(
            "SELECT chapter_id, number, title, volume_id, status, outline_json FROM chapters "
            "WHERE project_id = ? ORDER BY number ASC", (project_id,),
        ):
            item = dict(row)
            item["outline"] = _parse_json_object(item.pop("outline_json"))
            state.chapters_by_number[int(item["number"])] = item
        try:
            actors = {
                "antagonist": _actor_names(conn, project_id, "antagonist"),
                "protagonist": _actor_names(conn, project_id, "protagonist"),
            }
        except sqlite3.Error:  # pragma: no cover - 老库缺 characters 表时降级
            actors = {"antagonist": [], "protagonist": []}
    finally:
        conn.close()
    return state, actors


def _parse_json_object(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def outline_diff(old: dict, new: dict) -> dict:
    """逐字段 diff：{字段: {"from": 旧值, "to": 新值}}（只含有变化的字段）。"""
    diff: dict[str, dict] = {}
    for key in sorted(set(old) | set(new)):
        before, after = old.get(key), new.get(key)
        if before != after:
            diff[key] = {"from": before, "to": after}
    return diff


# ---------------------------------------------------------------------------
# 计划（现状 × brief → 待执行动作）
# ---------------------------------------------------------------------------


def build_plan(
    project_id: str,
    volume: dict,
    chapters: list[dict],
    state: DbState,
    *,
    seal_active: bool,
    issues: list[Issue],
    actors: dict | None = None,
) -> Plan:
    """把 brief 与现状比对成一份可审、可执行的计划（不写库）。"""
    volumes_by_number = {int(v["number"]): v for v in state.volumes}
    vnum = volume["number"]
    existing = volumes_by_number.get(vnum) if vnum else None
    plan_volume: dict = {
        "number": vnum,
        "title": volume["title"],
        "arc_summary": volume["arc_summary"],
        "volume_id": existing["volume_id"] if existing else None,
        "action": None,
        "status": existing["status"] if existing else None,
        "title_from": existing["title"] if existing else None,
        "arc_summary_from": existing["arc_summary"] if existing else None,
        "arc_summary_write": False,
    }
    seals: list[dict] = []

    if vnum is None:
        # 卷号非法（已有 error）：不再做任何现状比对，避免产出误导性的「冲突」结论。
        return Plan(project_id=project_id, volume=plan_volume, chapters=[],
                    seals=[], issues=issues, findings=[], actors=actors or {})

    if existing is not None:
        if existing["status"] == "sealed":
            issues.append(Issue(
                "VOLUME-SEALED", "error",
                f"卷 #{vnum} 已存在且为 sealed（{existing['volume_id']}）——"
                f"已封存卷是终态归档，不可承接新开卷；请换卷号或先人工处置"))
        else:
            plan_volume["action"] = "adopt"
            if (volume["title"] or None) != (existing["title"] or None):
                plan_volume["action"] = "update-title"
    else:
        plan_volume["action"] = "create"
        if vnum <= state.max_volume_number:
            issues.append(Issue(
                "VOLUME-NUMBER-SKIP", "warning",
                f"卷号 #{vnum} 小于库中最大卷号 #{state.max_volume_number}（补洞写入）"))
        elif vnum > state.max_volume_number + 1:
            issues.append(Issue(
                "VOLUME-NUMBER-SKIP", "warning",
                f"卷号 #{vnum} 跳跃（库中最大卷号 #{state.max_volume_number}）"))

    # arc_summary 无 service 入口（见模块 docstring）：仅在值有变化时定点直写。
    # 新建卷时 existing 为 None → 值非空即写。
    plan_volume["arc_summary_write"] = (
        bool(volume["arc_summary"])
        and (existing.get("arc_summary") if existing else None) != volume["arc_summary"]
    )

    # active 卷单例：VolumeService.create 会拒绝「已有 active 卷」，本脚本先判并给处置路径。
    other_active = [v for v in state.volumes
                    if v["status"] == "active" and int(v["number"]) != vnum]
    if other_active and plan_volume["action"] == "create":
        lower = [v for v in other_active if int(v["number"]) < vnum]
        if seal_active and len(lower) == len(other_active):
            seals = [{"volume_id": v["volume_id"], "number": int(v["number"]),
                      "title": v["title"]} for v in other_active]
        else:
            numbers = sorted(int(v["number"]) for v in other_active)
            hint = ("加 --seal-active 让本工具封存它（不可逆）"
                    if lower else "卷号更大的 active 卷不接受自动封存，请人工处置")
            issues.append(Issue(
                "VOLUME-ACTIVE", "error",
                f"项目已有 active 卷 {numbers}（active 卷单例）——开卷 #{vnum} 前须先封存；{hint}"))

    # 目标卷归属：既有卷用真 id；待建卷用哨兵（既有章不可能等于它 → 一律判冲突）。
    target_volume_id = existing["volume_id"] if existing else _PENDING_VOLUME
    plan_chapters: list[dict] = []
    for spec in chapters:
        cnum = spec["number"]
        outline = dict(spec["outline"])
        outline.setdefault("schema_version", OUTLINE_SCHEMA_VERSION)
        row = state.chapters_by_number.get(cnum)
        item: dict = {
            "number": cnum,
            "chapter_id": None,
            "title": spec["title"],
            "outline": outline,
            "action": "create",
            "needs_assign": True,
            "outline_changed": True,
            "outline_diff": {},
            "status": None,
            "title_from": None,
            "notes": ["新建章 + 写 outline_json + 挂卷"],
        }
        if row is not None:
            owner = row["volume_id"]
            item["chapter_id"] = row["chapter_id"]
            item["status"] = row["status"]
            item["title_from"] = row["title"]
            item["outline_diff"] = outline_diff(row["outline"] or {}, outline)
            item["outline_changed"] = bool(item["outline_diff"])
            item["action"] = "update-outline" if item["outline_changed"] else "adopt"
            item["needs_assign"] = owner != target_volume_id
            item["notes"] = []
            if owner is None:
                item["notes"].append("库中该章无卷归属（上次运行残留）→ 本次补挂")
            elif owner != target_volume_id:
                owner_no = next((int(v["number"]) for v in state.volumes
                                 if v["volume_id"] == owner), None)
                issues.append(Issue(
                    "CHAPTER-CONFLICT", "error",
                    f"章 #{cnum} 已存在（{row['chapter_id']}）且属于"
                    f"{'卷 #' + str(owner_no) if owner_no is not None else '其它卷'}——"
                    f"章号被别的卷占用，拒写"))
            if not row["outline"]:
                item["notes"].append("库中该章 outline_json 为空（策展面缺失）")
            if item["title_from"] != spec["title"]:
                item["notes"].append("标题与库中不一致（本命令不改标题）")
                issues.append(Issue(
                    "CHAPTER-TITLE-DIFF", "warning",
                    f"章 #{cnum} 标题不一致：库中 {item['title_from']!r} ≠ brief "
                    f"{spec['title']!r}（本命令只改 outline，标题请走 PATCH /chapters）"))
            if item["outline_changed"] and row["status"] in ("COMMITTED", "RELEASED"):
                issues.append(Issue(
                    "CHAPTER-WRITTEN", "warning",
                    f"章 #{cnum} 已是 {row['status']}，本次仍会改写其 outline_json"
                    f"（正文不会自动重产）"))
        plan_chapters.append(item)

    return Plan(
        project_id=project_id,
        volume=plan_volume,
        chapters=plan_chapters,
        seals=seals,
        issues=issues,
        findings=[],
        actors=actors or {},
    )


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------


def _write_arc_summary(db_path: Path | str, volume_id: str, arc_summary: str) -> None:
    """直写 volumes.arc_summary（唯一一处非 service 写入，理由见模块 docstring）。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE volumes SET arc_summary = ?, updated_at = ? WHERE volume_id = ?",
            (arc_summary, now_iso(), volume_id),
        )
        conn.commit()
    finally:
        conn.close()


def apply_plan(plan: Plan, db_path: Path | str) -> list[dict]:
    """执行计划，返回留痕清单（每条 = 一个动作）。"""
    actions: list[dict] = []
    volumes = VolumeService(db_path)
    chapters = ChapterService(db_path)

    for seal in plan.seals:
        volumes.seal(seal["volume_id"])
        actions.append({"action": "seal_volume", "volume_no": seal["number"],
                        "volume_id": seal["volume_id"],
                        "detail": f"封存旧 active 卷 #{seal['number']}（不可逆）"})

    volume_id = plan.volume["volume_id"]
    if plan.volume["action"] == "create":
        row = volumes.create(plan.project_id, VolumeCreate(
            number=plan.volume["number"], title=plan.volume["title"]))
        volume_id = row["volume_id"]
        plan.volume["volume_id"] = volume_id  # 摘要里回填真 id（规划期为 None）
        plan.volume["status"] = row["status"]
        actions.append({"action": "create_volume", "volume_no": row["number"],
                        "volume_id": volume_id,
                        "detail": f"建卷 #{row['number']} {row['title']!r}"})
    elif plan.volume["action"] == "update-title":
        volumes.update(volume_id, VolumeUpdate(title=plan.volume["title"]))
        actions.append({"action": "update_volume_title", "volume_no": plan.volume["number"],
                        "volume_id": volume_id,
                        "detail": f"卷 #{plan.volume['number']} 标题 "
                                  f"{plan.volume['title_from']!r} → {plan.volume['title']!r}"})

    if plan.volume["arc_summary_write"]:
        _write_arc_summary(db_path, volume_id, plan.volume["arc_summary"])
        actions.append({"action": "write_volume_arc_summary", "volume_no": plan.volume["number"],
                        "volume_id": volume_id,
                        "detail": "写 volumes.arc_summary（无 service 入口，定点直写）"})

    for item in plan.chapters:
        chapter_id = item["chapter_id"]
        if item["action"] == "create":
            row = chapters.create(plan.project_id, ChapterCreate(
                number=item["number"], title=item["title"]))
            chapter_id = row["chapter_id"]
            # ChapterCreate 不含 outline_json（策展列）：建行后走 ChapterUpdate 写策展面。
            chapters.update(chapter_id, ChapterUpdate(outline_json=item["outline"]))
            actions.append({"action": "create_chapter", "chapter_no": item["number"],
                            "chapter_id": chapter_id,
                            "detail": f"建章 #{item['number']} {item['title']!r} + 写 outline_json"})
        elif item["action"] == "update-outline":
            chapters.update(chapter_id, ChapterUpdate(outline_json=item["outline"]))
            changed = "、".join(item["outline_diff"])
            actions.append({"action": "update_chapter_outline", "chapter_no": item["number"],
                            "chapter_id": chapter_id,
                            "detail": f"更新 outline_json（{len(item['outline_diff'])} 字段：{changed}）",
                            "diff": item["outline_diff"]})
        if item["needs_assign"]:
            volumes.assign_chapter(volume_id, chapter_id)
            actions.append({"action": "assign_chapter", "chapter_no": item["number"],
                            "chapter_id": chapter_id, "volume_id": volume_id,
                            "detail": f"挂章 #{item['number']} → 卷 #{plan.volume['number']}"})
    return actions


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------


def _short(value: Any, limit: int = 44) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def run_outline_check(plan: Plan) -> list[Finding]:
    """对 brief 的策展大纲跑 outline_check 的同源判据。"""
    chapters = [c["outline"] for c in plan.chapters]
    return check_outline(
        chapters,
        antagonist_names=plan.actors.get("antagonist") or [],
        protagonist_names=plan.actors.get("protagonist") or [],
    )


def _outline_check_lines(plan: Plan) -> list[str]:
    findings = plan.findings
    lines = ["[卷纲体检] outline_check 同源判据（report-only，alert 不阻断落库）"]
    lines.append(
        f"  主角名 {plan.actors.get('protagonist') or '（未指定）'}"
        f"｜反派名 {plan.actors.get('antagonist') or '（未指定）'}")
    nums = [c["number"] for c in plan.chapters]
    if nums and nums != list(range(1, len(nums) + 1)):
        lines.append(f"  命中项按位置编号：ch1=#{nums[0]} … ch{len(nums)}=#{nums[-1]}")
    alerts = [f for f in findings if f.level == "alert"]
    for f in findings:
        lines.append(("⚠ " if f.level == "alert" else "  ") + f"[{f.rule_id}] {f.message}")
        for e in f.evidence[:8]:
            lines.append(f"        · {e}")
    lines.append(f"  alert {len(alerts)} 条 / 合计 {len(findings)} 条")
    return lines


def _plan_lines(plan: Plan) -> list[str]:
    lines = ["[计划]"]
    v = plan.volume
    for seal in plan.seals:
        lines.append(f"  - 封存旧 active 卷 #{seal['number']}（{seal['volume_id']}）——不可逆")
    if v["action"] == "create":
        lines.append(f"  - 建卷 #{v['number']} {v['title']!r}")
    elif v["action"] == "update-title":
        lines.append(f"  - 复用卷 #{v['number']}（{v['volume_id']}）并改标题 "
                     f"{v['title_from']!r} → {v['title']!r}")
    else:
        lines.append(f"  - 复用卷 #{v['number']}（{v['volume_id']}）")
    if v["arc_summary_write"]:
        lines.append("  - 写 volumes.arc_summary（定点直写）")
    created = [c for c in plan.chapters if c["action"] == "create"]
    updated = [c for c in plan.chapters if c["action"] == "update-outline"]
    adopted = [c for c in plan.chapters if c["action"] == "adopt"]
    if created:
        lines.append(f"  - 建章 {len(created)} 章："
                     f"#{created[0]['number']}..#{created[-1]['number']}"
                     f"（含写 outline_json + 挂卷 #{v['number']}）")
    if updated:
        lines.append(f"  - 更新既有章 outline_json {len(updated)} 章："
                     f"{['#' + str(c['number']) for c in updated]}")
        for c in updated:
            fields = "、".join(c["outline_diff"])
            lines.append(f"      #{c['number']}（{len(c['outline_diff'])} 字段：{fields}）")
    if adopted:
        lines.append(f"  - 复用既有章（无变化）{len(adopted)} 章："
                     f"{['#' + str(c['number']) for c in adopted]}")
    reassign = [c for c in plan.chapters if c["action"] != "create" and c["needs_assign"]]
    if reassign:
        lines.append(f"  - 补挂卷 {len(reassign)} 章："
                     f"{['#' + str(c['number']) for c in reassign]}")
    return lines


def _issue_lines(plan: Plan) -> list[str]:
    lines: list[str] = []
    for issue in plan.issues:
        mark = "✗" if issue.level == "error" else "⚠"
        lines.append(f"  {mark} [{issue.code}] {issue.message}")
    return lines


def render_text(plan: Plan, *, applied: list[dict], mode: str, brief_path: Path,
                db_path: Path, counts: dict) -> str:
    lines = [f"open_volume: db={db_path} project={plan.project_id} brief={brief_path} mode={mode}"]
    lines.append(f"[校验] error {len(plan.errors)} 条 / warning {len(plan.warnings)} 条")
    lines += _issue_lines(plan)
    if plan.chapters:
        v = plan.volume
        lines.append(f"[卷] #{v['number']} {_short(v['title'] or '（无标题）')}")
        lines.append(f"[章] {len(plan.chapters)} 章："
                     f"#{plan.chapters[0]['number']}..#{plan.chapters[-1]['number']}")
    lines += _outline_check_lines(plan)
    if not plan.ok:
        lines.append("[结论] 校验不通过 → 拒写（未对数据库做任何修改）")
        return "\n".join(lines)
    if mode == "apply":
        if applied:
            lines.append(f"[写入] {len(applied)} 个动作")
            for item in applied:
                lines.append(f"  · {item['action']}: {item['detail']}")
        else:
            lines.append("[写入] 无动作——brief 与库中现状一致（全部复用）")
        lines.append(
            f"[摘要] 建卷 {counts['volumes_created']} / 改卷标题 {counts['volumes_title_updated']} / "
            f"封存 {counts['volumes_sealed']} / 建章 {counts['chapters_created']} / "
            f"改 outline {counts['outlines_updated']} / 挂卷 {counts['chapters_assigned']}")
    else:
        lines += _plan_lines(plan)
        lines.append("[结论] dry-run：校验通过，未写库（加 --apply 落库）")
    return "\n".join(lines)


def build_summary(plan: Plan, *, applied: list[dict], mode: str, brief_path: Path,
                  db_path: Path, counts: dict) -> dict:
    nums = [c["number"] for c in plan.chapters]
    return {
        "mode": mode,
        "db": str(db_path),
        "brief": str(brief_path),
        "project_id": plan.project_id,
        "ok": plan.ok,
        "volume": plan.volume,
        "seals": plan.seals,
        "chapters": plan.chapters,
        "counts": counts,
        "issues": [i.to_dict() for i in plan.issues],
        "warnings": [i.to_dict() for i in plan.warnings],
        "errors": [i.to_dict() for i in plan.errors],
        "outline_check": {
            "alert_count": len([f for f in plan.findings if f.level == "alert"]),
            "total": len(plan.findings),
            "position_to_chapter_number": {f"ch{i}": n for i, n in enumerate(nums, 1)},
            "findings": [
                {"rule_id": f.rule_id, "level": f.level, "message": f.message,
                 "evidence": list(f.evidence)}
                for f in plan.findings
            ],
        },
        "actions": applied,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="open_volume",
        description=(
            "开新卷（卷 N）：校验一份卷 brief 后把卷行 + 各章策展大纲落库。"
            "默认 dry-run 只体检不写库；--apply 才写入。"
            "退出码 0=通过/已写入, 1=校验不通过（拒写）, 2=参数或 IO 错误。"
        ),
        epilog="brief 契约见模块 docstring：volume{number,title,arc_summary} + "
               "chapters[{number,title,outline{8 个必填字段}}]。",
    )
    parser.add_argument("--project", required=True, help="目标 project_id（prj_…）")
    parser.add_argument("--brief", required=True, type=Path, help="卷 brief JSON 路径")
    parser.add_argument("--db", type=Path, default=Path(DEFAULT_DB),
                        help=f"SQLite 路径（默认 {DEFAULT_DB}）")
    parser.add_argument("--apply", action="store_true", help="落库（缺省=dry-run 只体检）")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="输出机器可读摘要（stdout 仅 JSON）")
    parser.add_argument("--seal-active", action="store_true",
                        help="开新卷时封存卷号更小的既有 active 卷（不可逆：sealed 不能回 active）")
    return parser.parse_args(argv)


def _counts(actions: list[dict], plan: Plan) -> dict:
    def n(kind: str) -> int:
        return sum(1 for a in actions if a["action"] == kind)

    return {
        "volumes_created": n("create_volume"),
        "volumes_title_updated": n("update_volume_title"),
        "volumes_sealed": n("seal_volume"),
        "volume_arc_summary_written": n("write_volume_arc_summary"),
        "chapters_created": n("create_chapter"),
        "outlines_updated": n("update_chapter_outline"),
        "chapters_assigned": n("assign_chapter"),
        "planned_chapters": len(plan.chapters),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Windows 控制台默认 GBK：报告里的 ⚠ / ✗ / · 会 UnicodeEncodeError，按 outline_check.py
    # 同款处置把 stdout 切到 UTF-8（errors=replace 兜底不可编码字符）。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover - 非 TTY / 已重定向
        pass

    try:
        brief = load_brief(args.brief)
    except ValueError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return EXIT_USAGE
    volume, chapters, issues = validate_brief(brief)

    db_path: Path = args.db
    if not db_path.exists():
        sys.stderr.write(f"ERROR: 数据库不存在: {db_path}\n")
        return EXIT_USAGE

    try:
        state, actors = load_state(db_path, args.project)
    except sqlite3.Error as exc:
        sys.stderr.write(f"ERROR: 数据库打不开 / 读取失败: {db_path} ({exc})\n")
        return EXIT_USAGE

    if not state.project_exists:
        issues.append(Issue("PROJECT-NOT-FOUND", "error",
                            f"project#{args.project} 不存在于 {db_path}"))
    plan = build_plan(args.project, volume, chapters, state,
                      seal_active=args.seal_active, issues=issues, actors=actors)
    if plan.chapters:
        plan.findings = run_outline_check(plan)

    applied: list[dict] = []
    mode = "apply" if args.apply else "dry-run"
    if args.apply and plan.ok:
        try:
            applied = apply_plan(plan, db_path)
        except (VolumeConflictError, VolumeNotFoundError, VolumeSealedError,
                ChapterNumberConflict, sqlite3.Error) as exc:
            # 无全局事务：中断点之前的动作已落库。计划是幂等的，修好后重跑 --apply
            # 会复用已建对象、只补未完成的部分。
            sys.stderr.write(
                f"ERROR: 写入中断（已完成 {len(applied)} 个动作，均已落库）: {exc}\n"
                f"       修好后重跑 --apply 即可续做（幂等）\n")
            return EXIT_USAGE
    counts = _counts(applied, plan)

    if args.as_json:
        print(json.dumps(
            build_summary(plan, applied=applied, mode=mode, brief_path=args.brief,
                          db_path=db_path, counts=counts),
            ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(plan, applied=applied, mode=mode, brief_path=args.brief,
                          db_path=db_path, counts=counts))
    return EXIT_OK if plan.ok else EXIT_VALIDATION


if __name__ == "__main__":
    sys.exit(main())
