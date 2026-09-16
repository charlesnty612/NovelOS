# -*- coding: utf-8 -*-
"""world_switch —— 「世界切换」（快穿位面）作用域归档入口（CLI，默认 dry-run）。

## 为什么需要它

快穿题材换位面时，**上一世的实体与未结线在装配层仍是"现世事实"**：canon 实体
（characters / locations / factions）默认 ``inject_mode='auto'``，未命中触发时降级为
一行摘要——摘要里**仍有名字**；命中触发键时则是完整 excerpt（含 core_json /
current_state）。hooks 的 OPEN 线、narrative_debts 的 open/acknowledged 线同样无条件
进 director payload（``hook_ledger_excerpt`` / ``open_foreshadow_list`` /
``narrative_debt_excerpt``）。于是第二世界开篇就会看到临江、永丰号、苏婉清——这是
快穿最典型的穿帮。

本脚本把「上一世作用域」按既有机制（``inject_mode`` 三态 + hooks/debts 状态机）
归档，并把结算单落到 ``volumes``：**不改任何消费逻辑、不动物理数据（无 DELETE）**。

## 用法

::

    python scripts/world_switch.py --project prj_xxxx --from-volume 1 --to-volume 2 \\
        --settlement vol1_settlement.json                   # dry-run：只打印将改什么
    python scripts/world_switch.py --project prj_xxxx --from-volume 1 --to-volume 2 \\
        --settlement vol1_settlement.json --keep-name 林昭 --apply    # 落库
    python scripts/world_switch.py ... --json               # 机器可读报告（stdout 仅 JSON）

选项：``--db``（默认 ``data/novelos.db``）、``--apply``、``--json``、
``--keep <entity_id>``（可重复）、``--keep-name <名字>``（可重复）、``--no-keep``、
``--keep-mode {unchanged,always}``、``--skip-debts``。

退出码：``0`` = 通过（dry-run）/ 写入完成；``1`` = 校验不通过（**拒写**）；
``2`` = 参数错误 / 文件读不出 / 数据库打不开 / 写入中断。

## 豁免（跨位面主账本角色）

主角按 canon「记忆账本」规则是**跨位面主账本**——每世都在场，不能被归档。豁免名单
**只从命令行给**（``--keep`` / ``--keep-name``），脚本里**不写死任何名字**（写死就把
别的项目锁死了）：

- ``--apply`` 时豁免名单为空且未显式给 ``--no-keep`` → **拒写**（``KEEP-EMPTY``）：
  空名单意味着「把主角也归档」，那是静默穿帮的反向事故。dry-run 不拦，只提示。
- ``--keep`` / ``--keep-name`` 里出现库中不存在的 id / 名字 → **error 拒写**
  （``KEEP-NOT-FOUND``）——名字打错必须当场报，不能静默少豁免一个人。
- ``--keep-mode always``：把豁免角色一并置 ``inject_mode='always'``（主角跨位面常驻
  全量注入）。默认 ``unchanged``（只豁免，不改既有模式）。

## 结算单 JSON 契约

```json
{
  "volume_number": 1,
  "settlement": {
    "恶名清洗度": "…", "账本结余": "…", "据点规模": "…",
    "人心归附度": "…", "记忆损耗": "…"
  },
  "chips": ["情报卷·灾变预判", "人脉卷·账房旧识"],
  "note": "自由文本（可选）"
}
```

- ``volume_number`` 必填，且必须 **等于** ``--from-volume``（错配直接拒写，
  ``SETTLEMENT-VOLUME``）；
- ``settlement`` 必填对象，五个键各是字符串。缺键 / 非字符串 → warning 照落
  （结算单是**人写自由文本**，形态不全不该阻断归档）；
- ``chips``（字符串数组）与 ``note``（字符串）可选，缺省 ``[] / ""``；
- 契约外的顶层键**原样保留**写入快照（不校验不丢）。

## 归档口径（逐表理由）

- ``characters`` → ``inject_mode='never'``：装配层三态里唯一"完全不注入"的档。
  ``auto`` 未命中仍会漏一行名字（``summary_line``），``always`` 更是全量 excerpt。
  豁免名单除外。
- ``locations`` → 同上。``world_state_excerpts.locations`` 随之干净，``sensory_anchors``
  （从**已注入** location 的 ``data_json`` 派生）也随之不再贡献上一世感官锚点。
- ``factions`` → 同上（``world_state_excerpts.active_factions``）。
- ``hooks``：**仍在注入面**的状态（``OPEN/ACTIVE/ESCALATED``；该集合直接 import
  ``context_engine.builders_common._HOOK_OPEN_STATUSES``，与消费方同源，不另写一份）
  → ``ABANDONED`` + ``name`` 追加结算标记。``ABANDONED`` 是 hooks 五态机**唯一从任意
  非终态可达的终态**（由 ``HOOK_ALLOWED_NEXT`` 推导，不写死猜值）；转终态后
  ``hook_ledger_excerpt`` / ``open_foreshadow_list`` 自然不再注入。**已结线
  （``RESOLVED``）不动**——它是"已兑现"，再标 ABANDONED 是抹掉事实。hooks 表无 note
  列 → 标记写进 ``name``（幂等：已含标记不重复追加）。
- ``narrative_debts``：仍进台账的状态（``open/acknowledged``，同源
  ``_DEBT_OPEN_STATUSES``）→ ``forgiven``。同形泄漏面（``narrative_debt_excerpt`` 的
  描述里点名上一世实体）。取 ``forgiven``（结算核销——本世结束、账随世结）而非
  ``paid``（会谎称"已偿还"）；已结（``paid/forgiven``）不动。``--skip-debts`` 可关。
- ``volumes``（上一卷那行）：``terminal_snapshot_json`` 合并写入 + ``arc_summary``
  追加结算块——结算单的唯一权威落点（本脚本不建卷、不封卷，那是 ``open_volume.py`` 的活）。
- ``world_rules``：**不动**。世界规则是**跨位面的框架级设定**（价签视价 / 接锅继承 /
  位面锚点…），按设计常驻 L0；换位面恰恰要它继续成立。
- ``character_states`` / ``chapters`` / ``drafts`` / ``chapter_summaries`` /
  ``plot_events`` / ``story_states`` 等：**不动**。归档≠删除——历史正文与台账是审计迹；
  这些表的"跨世倒灌"问题在**消费侧**（见模块末「已知缺口」），本脚本不越权改。

## 上一世归属怎么判（``--from-volume`` 的作用）

canon 实体表**没有 volume 列**，所以归属靠**证据**推断 + 默认隔离：

1. 证据：``plot_events``（``introduced_chapter_id`` → 章 → 卷）里的
   ``participants_json`` / ``location_id`` 引用；hooks 用 ``introduced_chapter_id``、
   debts 用 ``created_chapter_id``。
2. 证据落在**更晚的卷**（``vol > from``）→ **跳过不归档**（``later_volume``）：
   已属新位面的实体不能被上一世的结算误杀。
3. 无证据 → **按隔离处理归档**（报告里标 ``证据: 无``），理由：切换时库里存在的实体
   都是本次切换之前创建的。dry-run 报告会单独标出这类行，供人工抽查。

## 幂等与可逆

- 计划里**只包含真正需要变更的行**（旧值 == 新值 → 不进 ``changes``）→ 重跑
  ``--apply`` 第二次为零变更、零写入；
- 落库内容用**作用域口径**（同一 ``--from-volume`` 下可重算、与"本次改了几行"无关），
  不写"本次 delta"——否则第二次重跑 delta 为空、内容比对必然不等，会把快照反复重写；
- ``terminal_snapshot_json`` 里的结算记录**忽略 ``applied_at``** 参与比对（保留首次
  应用的时间戳）；``arc_summary`` 的结算块按 ``<!-- world-switch:begin/end volume=N -->``
  成对标记**整块替换**，不重复追加；
- 全程 **UPDATE，无 DELETE**；``terminal_snapshot_json`` 若已有内容（例如已 ``seal``
  写入的故事快照）**原样保留**，结算记录只是多一个 ``world_settlement`` 键。

## 与 open_volume.py 的关系

两条互补的入口，互不写对方的表：``open_volume.py`` 建新卷 / 建章 / 写策展大纲 /
封存旧卷；``world_switch.py`` 归档上一世作用域 + 落结算单。

**注意顺序**：``VolumeService.seal()`` 会用 story_states 最新快照**整体覆盖**
``terminal_snapshot_json``。若先跑本脚本再 ``open_volume --seal-active``，结算记录会被
封存写入覆盖（本脚本不阻止，只在 ``VOLUME-SEALED`` 提示里点明）。建议顺序：
先 ``open_volume --seal-active``，再 ``world_switch --apply``。

## 已知缺口（本脚本**不改**消费逻辑，登记在此）

``inject_mode`` / hook 终态能止住 canon 台账三条注入面，但装配层还有三处**按章号而非
按卷**取数，换位面后仍会把上一世正文倒灌进第二世界开篇（均属 ``packages/core/
context_engine`` 消费逻辑，本脚本不越权改）：

1. ``recent_prose_tail``（writer 的 ``recent_prose.last_chapter_excerpt``）与
   ``previous_chapter_tail``（director）：取 ``number = 当前章 - 1`` 的正文尾段——
   第二世界第 1 章的"上一章"就是上一世末章。
2. ``recent_chapter_summaries``（director）：最近 ≤40 章摘要，不分卷。
3. ``recalled_passages``（FTS 召回，writer + director）：只过滤 ``chapter_no < 当前章``，
   不分卷。

对策方向（供主会话裁决）：三处取数加「章的 ``volume_id`` 与当前章一致」过滤，或对
跨卷的摘要 / 尾段打「上一世（已结算）」标记。本脚本已把「哪一章属哪一卷」判据留在
``load_state()`` 的 ``chapter_volume_no`` 里可复用。

另两处残留（实测记录，非本脚本能处理）：

4. ``never`` 的实体在 payload 里仍留一份**元标记**（``_suppressed_characters`` /
   ``_suppressed_locations`` / ``_suppressed_factions``），其中带 ``name``——供预览面板
   区分"已剔除 / 未命中降级"用，但同时会随 user message 进模型。要彻底干净需消费侧
   决定"生产 payload 不发 suppressed 标记（或只发 id 不发 name）"。
5. 被豁免的主角，其 ``character_states`` 最新一行仍是上一世的物理状态
   （``current_state.location`` / ``inventory`` / ``resources`` / ``current_goal``），
   随其 excerpt 一并注入——本脚本不伪造新的状态行（那要动 story state 写入口径）。
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

from packages.core.context_engine.builders_common import (  # noqa: E402
    _DEBT_OPEN_STATUSES,
    _HOOK_OPEN_STATUSES,
)
from packages.core.db import get_connection  # noqa: E402
from packages.core.ids import now_iso  # noqa: E402
from packages.domain.character import CharacterService, CharacterUpdate  # noqa: E402
from packages.domain.ledger import (  # noqa: E402
    DEBT_ALLOWED_NEXT,
    HOOK_ALLOWED_NEXT,
    DebtUpdate,
    HookUpdate,
    LedgerService,
)
from packages.domain.world import WorldService  # noqa: E402

DEFAULT_DB = "data/novelos.db"

# --- 常量 ---------------------------------------------------------------------

# 「还进注入面」的状态集合：直接取消费方（context_engine）的同一份常量——本脚本要
# 归档的正是**仍会进 payload** 的那些行，口径必须与消费方同源，不能各写一份。
#   hooks：`_hook_ledger_excerpt` / `_open_foreshadow_list` = OPEN/ACTIVE/ESCALATED；
#   debts：`_narrative_debt_excerpt` = open/acknowledged。
HOOK_OPEN_STATUSES: frozenset[str] = _HOOK_OPEN_STATUSES
DEBT_OPEN_STATUSES: frozenset[str] = _DEBT_OPEN_STATUSES

# 归档目标状态：从状态机常量**推导**可达性，不写死猜值。
#   hooks 五态机（packages/domain/ledger/models.py）：OPEN→{OPEN,ACTIVE,ABANDONED}、
#   ACTIVE→{…,ABANDONED}、ESCALATED→{…,ABANDONED}、RESOLVED→{RESOLVED,ABANDONED}
#   → ABANDONED 是唯一从任意非终态可达的终态。
#   debts 四态机：open→{open,acknowledged,paid,forgiven}、acknowledged→{acknowledged,paid,forgiven}
#   → forgiven 与 paid 都可达；本脚本取 forgiven（结算核销，不是"已偿还"）。
HOOK_ARCHIVE_STATUS = "ABANDONED"
DEBT_ARCHIVE_STATUS = "forgiven"

# canon 实体的归档态值（inject_mode 三态中的 never）。
ARCHIVED_INJECT_MODE = "never"

# 结算标记（hooks.name 追加；结算记录与 arc_summary 块同源引用）。
SETTLEMENT_MARKER = "本世结算，未结的线按账结清"
SETTLEMENT_SCHEMA_VERSION = "volume-settlement.v1"
SETTLEMENT_SNAPSHOT_KEY = "world_settlement"
SETTLEMENT_REQUIRED_KEYS = ("恶名清洗度", "账本结余", "据点规模", "人心归附度", "记忆损耗")

# arc_summary 结算块的成对标记（幂等：整块替换，不重复追加）。
ARC_BEGIN = "<!-- world-switch:begin volume={n} -->"
ARC_END = "<!-- world-switch:end volume={n} -->"

KEEP_MODES = ("unchanged", "always")

# canon 实体的三类（报告分组 / 计数口径）。
ARCHIVE_KINDS = ("character", "location", "faction")

# 卷外框架级表（报告里显式列出"没动"，避免读者以为漏了）。
FRAMEWORK_TABLES_NOT_TOUCHED = (
    "world_rules（跨位面框架级设定，按设计常驻 L0）",
)

# 「不归档」的理由码（报告 + --json 都带）。
REASON_KEEP = "keep"  # 豁免名单命中
REASON_ALREADY_ARCHIVED = "already_archived"  # canon 实体已是 inject_mode='never'
REASON_ALREADY_CLOSED = "already_closed"  # hook/debt 已是终态（不进注入面）
REASON_LATER_VOLUME = "later_volume"  # 证据指向更晚的卷 → 不属上一世
REASON_UNREACHABLE = "status_unreachable"  # 状态机不允许迁到归档态

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_USAGE = 2

# canon 实体 id 前缀（用于从 plot_events 的 JSON 里捞引用）。
_ID_PREFIXES = ("char_", "loc_", "fac_", "hook_", "debt_", "event_")


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
class RowChange:
    """一行实体的归档 / 豁免决定（计划期即为终态，apply 只照做）。"""

    kind: str  # character | location | faction | hook | debt
    entity_id: str
    name: str
    field: str  # 被改的列
    old: Any
    new: Any
    action: str  # archive | keep | promote | skip
    reason: str
    evidence: list[str] = field(default_factory=list)
    in_scope: bool = True  # 是否属本次归档作用域（skip 行 = False）
    extra: dict = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return self.action in ("archive", "promote")

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.entity_id,
            "name": self.name,
            "field": self.field,
            "from": self.old,
            "to": self.new,
            "action": self.action,
            "reason": self.reason,
            "in_scope": self.in_scope,
            "evidence": list(self.evidence),
            **({"extra": self.extra} if self.extra else {}),
        }


@dataclass
class VolumeWrite:
    """volumes 两列的定点写入计划（内容比对，无变化则不入计划）。"""

    volume_id: str
    volume_no: int
    terminal_snapshot_json: str | None = None  # 目标原文（None = 不写）
    arc_summary: str | None = None  # 目标原文（None = 不写）
    terminal_from: Any = None
    arc_from: Any = None


@dataclass
class Plan:
    """一次世界切换的完整计划（dry-run 与 --apply 共用同一份计算）。"""

    project_id: str
    from_volume: int
    to_volume: int
    db_path: Path
    settlement_payload: dict
    settlement_body: dict
    keep_ids: list[str]
    keep_names: list[str]
    keep_mode: str
    include_debts: bool
    rows: list[RowChange] = field(default_factory=list)
    volume: dict | None = None
    volume_write: VolumeWrite | None = None
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def changes(self) -> list[RowChange]:
        """真正会改库的行（归档 + 豁免提升）；keep / skip 不算。"""
        return [r for r in self.rows if r.changed]

    def by_kind(self, kind: str) -> list[RowChange]:
        return [r for r in self.rows if r.kind == kind]

    def in_scope(self, kind: str) -> list[RowChange]:
        return [r for r in self.by_kind(kind) if r.in_scope]


# ---------------------------------------------------------------------------
# 结算单
# ---------------------------------------------------------------------------


def load_settlement(path: Path) -> Any:
    """读结算单 JSON；文件缺失 / 读不出 / 非合法 JSON → ValueError。"""
    if not path.exists():
        raise ValueError(f"结算单文件不存在: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - 平台相关
        raise ValueError(f"结算单读不出: {path} ({exc})") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"结算单不是合法 JSON: {exc}") from exc


def validate_settlement(raw: Any, from_volume: int, issues: list[Issue]) -> dict:
    """校验结算单契约；返回 ``settlement`` 段（缺失 / 非法 → {}）。

    结构性错误（非对象 / volume_number 错配）记 error 拒写；free-text 缺键只 warning。
    """
    if not isinstance(raw, dict):
        issues.append(Issue("SETTLEMENT-SHAPE", "error", "结算单顶层必须是 JSON 对象"))
        return {}

    vnum = raw.get("volume_number")
    if vnum is None:
        issues.append(Issue(
            "SETTLEMENT-VOLUME", "error",
            "结算单缺 volume_number（必须等于 --from-volume，防错配）"))
    elif vnum != from_volume:
        issues.append(Issue(
            "SETTLEMENT-VOLUME", "error",
            f"结算单 volume_number={vnum!r} ≠ --from-volume {from_volume}"
            f"——结算单与要归档的卷对不上，拒写"))

    body = raw.get("settlement")
    if not isinstance(body, dict):
        issues.append(Issue(
            "SETTLEMENT-SHAPE", "error", "结算单 settlement 必须是对象（五个口径键）"))
        body = {}
    elif not body:
        issues.append(Issue("SETTLEMENT-EMPTY", "error", "结算单 settlement 为空对象"))
    for key in SETTLEMENT_REQUIRED_KEYS:
        if key not in body:
            issues.append(Issue(
                "SETTLEMENT-KEY", "warning", f"结算单缺口径键 {key!r}（照落，不阻断）"))
        elif not isinstance(body[key], str):
            issues.append(Issue(
                "SETTLEMENT-KEY", "warning",
                f"结算单 {key!r} 不是字符串（照落，不阻断）"))

    chips = raw.get("chips")
    if chips is None:
        issues.append(Issue("SETTLEMENT-CHIPS", "warning", "结算单缺 chips（按空数组落）"))
    elif not isinstance(chips, list) or any(not isinstance(c, str) for c in chips):
        issues.append(Issue(
            "SETTLEMENT-CHIPS", "error", "结算单 chips 必须是字符串数组"))

    note = raw.get("note")
    if note is not None and not isinstance(note, str):
        issues.append(Issue("SETTLEMENT-NOTE", "error", "结算单 note 必须是字符串"))
    return body


# ---------------------------------------------------------------------------
# 现状读取（含上一世归属证据）
# ---------------------------------------------------------------------------


def _parse_json(raw: Any) -> Any:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _collect_refs(value: Any, out: set[str]) -> None:
    """递归收集 JSON 里的实体 id（plot_events 的 participants_json 形态不固定）。"""
    if isinstance(value, str):
        if value.startswith(_ID_PREFIXES):
            out.add(value)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_refs(item, out)
    elif isinstance(value, list):
        for item in value:
            _collect_refs(item, out)


def load_state(conn: sqlite3.Connection, project_id: str) -> dict:
    """读现状：项目 / 卷 / 章归属 / canon 实体 / hooks / debts + 归属证据。

    返回的 ``chapter_volume_no``（chapter_id → 卷号）就是「哪一章属哪一卷」的判据，
    消费侧若要给摘要 / 尾段 / 召回加按卷过滤可直接复用同口径。
    """
    state: dict = {
        "project_exists": conn.execute(
            "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone() is not None,
        "volumes": {},
        "volume_by_id": {},
        "chapter_volume_no": {},
        "chapter_no": {},
        "entities": [],
        "hooks": [],
        "debts": [],
        "evidence": {},
        "eras": {},
    }
    if not state["project_exists"]:
        return state

    for row in conn.execute(
        "SELECT volume_id, number, title, status, terminal_snapshot_json, arc_summary "
        "FROM volumes WHERE project_id = ? ORDER BY number ASC",
        (project_id,),
    ):
        item = dict(row)
        state["volumes"][int(item["number"])] = item
        state["volume_by_id"][item["volume_id"]] = item

    for row in conn.execute(
        "SELECT chapter_id, number, volume_id FROM chapters WHERE project_id = ?",
        (project_id,),
    ):
        state["chapter_no"][row["chapter_id"]] = int(row["number"])
        vol = state["volume_by_id"].get(row["volume_id"])
        state["chapter_volume_no"][row["chapter_id"]] = (
            int(vol["number"]) if vol is not None else None
        )

    for kind, table, id_col, label_col, note_col in (
        ("character", "characters", "character_id", "name", "role"),
        ("location", "locations", "location_id", "name", "statement"),
        ("faction", "factions", "faction_id", "name", "statement"),
    ):
        for row in conn.execute(
            f"SELECT {id_col} AS eid, {label_col} AS label, {note_col} AS note, "
            f"inject_mode FROM {table} WHERE project_id = ? ORDER BY {id_col} ASC",
            (project_id,),
        ):
            state["entities"].append({
                "kind": kind,
                "table": table,
                "id_field": id_col,
                "id": row["eid"],
                "name": row["label"],
                "note": row["note"],
                "inject_mode": row["inject_mode"] or "auto",
            })

    for row in conn.execute(
        "SELECT hook_id, name, status, introduced_chapter_id FROM hooks "
        "WHERE project_id = ? ORDER BY hook_id ASC",
        (project_id,),
    ):
        state["hooks"].append(dict(row))

    for row in conn.execute(
        "SELECT debt_id, description, status, created_chapter_id FROM narrative_debts "
        "WHERE project_id = ? ORDER BY debt_id ASC",
        (project_id,),
    ):
        state["debts"].append(dict(row))

    _fill_evidence(conn, project_id, state)
    return state


def _fill_evidence(conn: sqlite3.Connection, project_id: str, state: dict) -> None:
    """从 plot_events 收集 canon 实体的归属证据（entity_id → [证据串] / {卷号}）。"""
    evidence: dict[str, list[str]] = {}
    eras: dict[str, set[int | None]] = {}
    try:
        rows = conn.execute(
            "SELECT event_id, introduced_chapter_id, participants_json, location_id "
            "FROM plot_events WHERE project_id = ? ORDER BY event_id ASC",
            (project_id,),
        ).fetchall()
    except sqlite3.OperationalError:  # pragma: no cover - 极老库无 plot_events
        rows = []
    for row in rows:
        refs: set[str] = set()
        _collect_refs(_parse_json(row["participants_json"]), refs)
        if isinstance(row["location_id"], str) and row["location_id"]:
            refs.add(row["location_id"])
        if not refs:
            continue
        ch_id = row["introduced_chapter_id"]
        ch_no = state["chapter_no"].get(ch_id) if ch_id else None
        vol_no = state["chapter_volume_no"].get(ch_id) if ch_id else None
        label = f"plot_event:{row['event_id']}"
        if ch_no is not None:
            label += f"@ch{ch_no}"
        label += f"(卷{vol_no})" if vol_no is not None else "(无卷归属)"
        for ref in refs:
            evidence.setdefault(ref, []).append(label)
            eras.setdefault(ref, set()).add(vol_no)
    state["evidence"] = {k: sorted(set(v)) for k, v in evidence.items()}
    state["eras"] = eras


# ---------------------------------------------------------------------------
# 计划（现状 × 结算单 → 待执行动作）
# ---------------------------------------------------------------------------


def _era_decision(eras: set[int | None], from_volume: int) -> tuple[str, str]:
    """归属判定：返回 ``(action, reason)``。"""
    known = {e for e in eras if e is not None}
    if any(e > from_volume for e in known):
        # 证据指向更晚的卷 → 已属新位面，不能按上一世归档（跨世实体也是这一支）。
        return "skip", REASON_LATER_VOLUME
    # 只有本世及更早的卷归属，或完全没有证据（切换时库里的实体都是本世之前建的）。
    return "archive", "closed_era"


def _can_transition(status: str, allowed_next: dict[str, set[str]], target: str) -> bool:
    """状态机守卫：当前状态能否合法迁到目标状态（防状态机变动后静默越迁）。"""
    return target in allowed_next.get(status, set())


def _mark_name(name: str | None, *, limit: int = 200) -> tuple[str, bool]:
    """给 hooks.name 追加结算标记（幂等）；超长则截断原名的尾巴。

    ``limit`` 对齐 ``HookUpdate.name`` 的 max_length=200（服务层校验，超长会 422）。
    """
    original = name or ""
    if SETTLEMENT_MARKER in original:
        return original, False
    suffix = f"（{SETTLEMENT_MARKER}）"
    if len(original) + len(suffix) <= limit:
        return original + suffix, False
    return original[: max(0, limit - len(suffix))] + suffix, True


def scope_counts(plan: Plan) -> dict:
    """作用域口径计数（**可重算**：同一 ``--from-volume`` 下重跑恒同值）。

    - ``total``：属本次作用域的行数（不含 ``later_volume`` 跳过行）；
    - ``archived``：作用域内**终态为归档态**的行数（首跑 / 重跑同值——重跑时这些行
      已是归档态、"本次 delta" 为空，所以持久记录绝不能写 delta）；
    - ``kept``：作用域内被豁免的行数（仅 canon 实体有意义）。
    """
    out: dict[str, dict] = {}
    for kind, archive_value in (
        ("character", ARCHIVED_INJECT_MODE),
        ("location", ARCHIVED_INJECT_MODE),
        ("faction", ARCHIVED_INJECT_MODE),
        ("hook", HOOK_ARCHIVE_STATUS),
        ("debt", DEBT_ARCHIVE_STATUS),
    ):
        rows = plan.in_scope(kind)
        out[_count_key(kind)] = {
            "total": len(rows),
            "archived": sum(1 for r in rows if r.new == archive_value),
            "kept": sum(1 for r in rows if r.reason == REASON_KEEP),
        }
    return out


def _count_key(kind: str) -> str:
    return {
        "character": "characters",
        "location": "locations",
        "faction": "factions",
        "hook": "hooks",
        "debt": "debts",
    }.get(kind, kind)


def delta_counts(plan: Plan) -> dict:
    """本次真的要改的行数（**不可重算**，只用于本次报告）。"""
    return {key: sum(1 for r in plan.changes if r.kind == kind)
            for kind, key in (("character", "characters"), ("location", "locations"),
                              ("faction", "factions"), ("hook", "hooks"),
                              ("debt", "debts"))}


def _settlement_record(
    *,
    settlement_payload: dict,
    settlement_body: dict,
    from_volume: int,
    to_volume: int,
    applied_at: str,
    counts: dict,
    kept: dict,
) -> dict:
    """``volumes.terminal_snapshot_json[world_settlement]`` 的内容。

    ``settlement`` = 校验后的五口径段（消费方可直接按键取用）；
    ``settlement_payload`` = 主会话给的结算单**原文**（契约外键一并保留，不裁剪）。
    """
    return {
        "schema_version": SETTLEMENT_SCHEMA_VERSION,
        "tool": "scripts/world_switch.py",
        "volume_number": from_volume,
        "to_volume": to_volume,
        "applied_at": applied_at,
        "marker": SETTLEMENT_MARKER,
        "principles": {
            "entities": f"上一世 canon 实体 inject_mode='{ARCHIVED_INJECT_MODE}'（主角等豁免）",
            "hooks": f"仍进注入面的状态 → {HOOK_ARCHIVE_STATUS}",
            "debts": f"仍进注入面的状态 → {DEBT_ARCHIVE_STATUS}",
            "frame": "world_rules 跨位面框架级设定，不动",
            "no_delete": "全程 UPDATE，无物理删除",
        },
        "settlement": settlement_body,
        "settlement_payload": settlement_payload,
        "scope": counts,
        "kept": kept,
    }


def _strip_volatile(record: dict) -> dict:
    """比对用归一：去掉只记首次应用的时间戳。"""
    return {k: v for k, v in record.items() if k != "applied_at"}


def build_arc_block(
    *,
    from_volume: int,
    to_volume: int,
    settlement_payload: dict,
    settlement_body: dict,
    counts: dict,
    kept_names: list[str],
) -> str:
    """arc_summary 结算块（人可读的持久痕迹；成对标记包裹，幂等整块替换）。

    五口径取**校验后的** ``settlement_body``；chips / note 由调用方从结算单原文取
    （它们不在五口径段内）。
    """
    pairs = "；".join(f"{k}={settlement_body.get(k, '—')}" for k in SETTLEMENT_REQUIRED_KEYS)
    chips = settlement_payload.get("chips") or []
    note = settlement_payload.get("note") or ""
    lines = [
        ARC_BEGIN.format(n=from_volume),
        f"【第 {from_volume} 世 → 第 {to_volume} 世 结算】{SETTLEMENT_MARKER}",
        f"- 结算单：{pairs}",
    ]
    if chips:
        lines.append(f"- 跨界筹码：{'；'.join(str(c) for c in chips)}")
    lines.append("- 作用域（第 %d 世）：%s" % (
        from_volume,
        " / ".join(f"{k} {v['total']}" for k, v in counts.items()),
    ))
    lines.append("- 归档态：%s（world_rules 框架级不动；无物理删除）" % (
        " / ".join(f"{k} {v['archived']}" for k, v in counts.items()),
    ))
    if kept_names:
        lines.append(f"- 豁免（跨位面主账本）：{'、'.join(kept_names)}")
    if note:
        lines.append(f"- 备注：{note}")
    lines.append(ARC_END.format(n=from_volume))
    return "\n".join(lines)


def upsert_arc_block(existing: str | None, from_volume: int, block: str) -> str:
    """把结算块并入 arc_summary：同卷标记存在 → 整块替换；否则追加（保留既有内容）。"""
    current = existing or ""
    begin = ARC_BEGIN.format(n=from_volume)
    end = ARC_END.format(n=from_volume)
    i, j = current.find(begin), current.find(end)
    if i != -1 and j != -1 and j > i:
        return current[:i] + block + current[j + len(end):]
    if not current.strip():
        return block
    sep = "\n" if current.endswith("\n") else "\n\n"
    return current + sep + block


def build_plan(
    *,
    db_path: Path | str,
    project_id: str,
    from_volume: int,
    to_volume: int,
    settlement_payload: dict,
    settlement_body: dict,
    keep_ids: list[str],
    keep_names: list[str],
    keep_mode: str,
    include_debts: bool,
    issues: list[Issue],
    applied_at: str | None = None,
) -> Plan:
    """把「现状 × 结算单」比对成一份可审、可执行的计划（只读，不写库）。"""
    plan = Plan(
        project_id=project_id,
        from_volume=from_volume,
        to_volume=to_volume,
        db_path=Path(db_path),
        settlement_payload=settlement_payload,
        settlement_body=settlement_body,
        keep_ids=list(keep_ids),
        keep_names=list(keep_names),
        keep_mode=keep_mode,
        include_debts=include_debts,
        issues=issues,
    )
    if from_volume >= to_volume:
        issues.append(Issue(
            "VOLUME-ORDER", "error",
            f"--from-volume {from_volume} 必须小于 --to-volume {to_volume}"))

    conn = get_connection(db_path)
    try:
        state = load_state(conn, project_id)
    finally:
        conn.close()

    if not state["project_exists"]:
        issues.append(Issue(
            "PROJECT-NOT-FOUND", "error", f"project#{project_id} 不存在于 {db_path}"))
        return plan

    volume = state["volumes"].get(from_volume)
    plan.volume = volume
    if volume is None:
        issues.append(Issue(
            "VOLUME-NOT-FOUND", "error",
            f"卷 #{from_volume} 在 project#{project_id} 不存在（无处落结算单）"))
    elif volume["status"] == "sealed":
        issues.append(Issue(
            "VOLUME-SEALED", "warning",
            f"卷 #{from_volume} 已是 sealed：本次写入**保留**其故事快照（只加 "
            f"{SETTLEMENT_SNAPSHOT_KEY} 键）；但若之后再 seal，terminal_snapshot_json "
            f"会被整体覆盖，结算记录随之丢失——建议 seal 在前、本命令在后"))
    if to_volume in state["volumes"]:
        issues.append(Issue(
            "VOLUME-TO-EXISTS", "warning",
            f"卷 #{to_volume} 已存在（{state['volumes'][to_volume]['volume_id']}）"
            f"——本脚本不改卷状态，仅按 --from-volume 归档"))

    # --- 豁免名单解析（id 精确匹配；三类都允许按名豁免） --------------------------
    keep_resolved: list[dict] = []
    for eid in keep_ids:
        hit = next((e for e in state["entities"] if e["id"] == eid), None)
        if hit is None:
            issues.append(Issue(
                "KEEP-NOT-FOUND", "error",
                f"--keep {eid} 在 project#{project_id} 的 canon 实体里不存在"
                f"（id 打错会让该实体被归档，拒写）"))
        else:
            keep_resolved.append(hit)
    for name in keep_names:
        hits = [e for e in state["entities"] if e["name"] == name]
        if not hits:
            issues.append(Issue(
                "KEEP-NOT-FOUND", "error",
                f"--keep-name {name!r} 在 project#{project_id} 的 canon 实体里不存在"
                f"（名字打错会让该实体被归档，拒写）"))
        else:
            keep_resolved.extend(hits)
    keep_entity_ids = [h["id"] for h in keep_resolved]
    keep_entity_id_set = set(keep_entity_ids)

    # --- canon 实体：先判归属，再判是否已归档 -----------------------------------
    for item in state["entities"]:
        eid = item["id"]
        evidence = state["evidence"].get(eid, [])
        action, reason = _era_decision(state["eras"].get(eid, set()), from_volume)
        base_extra = {"role": item.get("note")}
        if eid in keep_entity_id_set:
            target = "always" if keep_mode == "always" else item["inject_mode"]
            plan.rows.append(RowChange(
                kind=item["kind"], entity_id=eid, name=item["name"],
                field="inject_mode", old=item["inject_mode"], new=target,
                action="promote" if target != item["inject_mode"] else "keep",
                reason=REASON_KEEP, evidence=evidence, extra=base_extra,
            ))
            continue
        if action == "skip":
            plan.rows.append(RowChange(
                kind=item["kind"], entity_id=eid, name=item["name"],
                field="inject_mode", old=item["inject_mode"], new=item["inject_mode"],
                action="skip", reason=reason, evidence=evidence, in_scope=False,
                extra=base_extra,
            ))
            issues.append(Issue(
                "ROW-LATER-VOLUME", "warning",
                f"{item['kind']} {item['name']}（{eid}）有更晚卷的归属证据"
                f"（{evidence[:3]}）→ 跳过不归档，需人工确认"))
            continue
        if item["inject_mode"] == ARCHIVED_INJECT_MODE:
            plan.rows.append(RowChange(
                kind=item["kind"], entity_id=eid, name=item["name"],
                field="inject_mode", old=ARCHIVED_INJECT_MODE,
                new=ARCHIVED_INJECT_MODE, action="keep",
                reason=REASON_ALREADY_ARCHIVED, evidence=evidence, extra=base_extra,
            ))
            continue
        plan.rows.append(RowChange(
            kind=item["kind"], entity_id=eid, name=item["name"],
            field="inject_mode", old=item["inject_mode"], new=ARCHIVED_INJECT_MODE,
            action="archive", reason=reason, evidence=evidence,
            extra={**base_extra, "evidence_none": not evidence},
        ))
        if not evidence:
            issues.append(Issue(
                "EVIDENCE-NONE", "warning",
                f"{item['kind']} {item['name']}（{eid}）无归属证据 → 按隔离处理归档"
                f"（dry-run 请抽查这类行）"))

    # --- hooks ---------------------------------------------------------------
    for hook in state["hooks"]:
        hid = hook["hook_id"]
        status = hook["status"]
        evidence = [_origin_label(state, "hook", hook["introduced_chapter_id"])]
        vol_no = _chapter_volume_no(state, hook["introduced_chapter_id"])
        if vol_no is not None and vol_no > from_volume:
            plan.rows.append(RowChange(
                kind="hook", entity_id=hid, name=hook["name"], field="status",
                old=status, new=status, action="skip", reason=REASON_LATER_VOLUME,
                evidence=evidence, in_scope=False,
            ))
            continue
        if status not in HOOK_OPEN_STATUSES:
            # 已结（RESOLVED/ABANDONED）：不进 hook_ledger_excerpt / open_foreshadow_list，
            # 无需处理——尤其**不能**把已 RESOLVED 的线又标成 ABANDONED（那是抹掉事实）。
            plan.rows.append(RowChange(
                kind="hook", entity_id=hid, name=hook["name"], field="status",
                old=status, new=status, action="keep",
                reason=REASON_ALREADY_CLOSED, evidence=evidence,
            ))
            continue
        if not _can_transition(status, HOOK_ALLOWED_NEXT, HOOK_ARCHIVE_STATUS):
            plan.rows.append(RowChange(
                kind="hook", entity_id=hid, name=hook["name"], field="status",
                old=status, new=status, action="skip", reason=REASON_UNREACHABLE,
                evidence=evidence, in_scope=False,
            ))
            issues.append(Issue(
                "HOOK-UNREACHABLE", "error",
                f"hook {hid}（{hook['name']}）当前 status={status} 无法迁到 "
                f"{HOOK_ARCHIVE_STATUS}（状态机已变？）——跳过该行"))
            continue
        marked, truncated = _mark_name(hook["name"])
        plan.rows.append(RowChange(
            kind="hook", entity_id=hid, name=hook["name"], field="status",
            old=status, new=HOOK_ARCHIVE_STATUS, action="archive",
            reason="closed_era", evidence=evidence,
            extra={"name_new": marked, "name_truncated": truncated},
        ))

    # --- narrative_debts -----------------------------------------------------
    if include_debts:
        for debt in state["debts"]:
            did = debt["debt_id"]
            status = debt["status"]
            evidence = [_origin_label(state, "debt", debt["created_chapter_id"])]
            vol_no = _chapter_volume_no(state, debt["created_chapter_id"])
            label = (debt["description"] or "")[:40]
            if vol_no is not None and vol_no > from_volume:
                plan.rows.append(RowChange(
                    kind="debt", entity_id=did, name=label, field="status",
                    old=status, new=status, action="skip",
                    reason=REASON_LATER_VOLUME, evidence=evidence, in_scope=False,
                ))
                continue
            if status not in DEBT_OPEN_STATUSES:
                # 已结（paid/forgiven）：不进 narrative_debt_excerpt，无需处理。
                plan.rows.append(RowChange(
                    kind="debt", entity_id=did, name=label, field="status",
                    old=status, new=status, action="keep",
                    reason=REASON_ALREADY_CLOSED, evidence=evidence,
                ))
                continue
            if not _can_transition(status, DEBT_ALLOWED_NEXT, DEBT_ARCHIVE_STATUS):
                plan.rows.append(RowChange(
                    kind="debt", entity_id=did, name=label, field="status",
                    old=status, new=status, action="skip",
                    reason=REASON_UNREACHABLE, evidence=evidence, in_scope=False,
                ))
                issues.append(Issue(
                    "DEBT-UNREACHABLE", "error",
                    f"debt {did} 当前 status={status} 无法迁到 {DEBT_ARCHIVE_STATUS}"))
                continue
            plan.rows.append(RowChange(
                kind="debt", entity_id=did, name=label, field="status",
                old=status, new=DEBT_ARCHIVE_STATUS, action="archive",
                reason="closed_era", evidence=evidence,
            ))

    # --- volumes：结算单落点 --------------------------------------------------
    if volume is not None:
        counts = scope_counts(plan)
        kept = {"characters": sorted(keep_entity_id_set)}
        record = _settlement_record(
            settlement_payload=settlement_payload,
            settlement_body=settlement_body,
            from_volume=from_volume,
            to_volume=to_volume,
            applied_at=applied_at or now_iso(),
            counts=counts, kept=kept,
        )
        existing_raw = volume.get("terminal_snapshot_json")
        existing = _parse_json(existing_raw)
        if not isinstance(existing, dict):
            existing = {}
        merged = dict(existing)
        merged[SETTLEMENT_SNAPSHOT_KEY] = record
        old_record = existing.get(SETTLEMENT_SNAPSHOT_KEY)
        terminal_target: str | None = None
        if (not isinstance(old_record, dict)
                or _strip_volatile(old_record) != _strip_volatile(record)):
            terminal_target = json.dumps(merged, ensure_ascii=False)

        kept_names = [r.name for r in plan.rows
                      if r.kind == "character" and r.reason == REASON_KEEP]
        block = build_arc_block(
            from_volume=from_volume, to_volume=to_volume,
            settlement_payload=settlement_payload, settlement_body=settlement_body,
            counts=counts, kept_names=kept_names,
        )
        arc_target = upsert_arc_block(volume.get("arc_summary"), from_volume, block)
        arc_write = arc_target != (volume.get("arc_summary") or "")

        if terminal_target is not None or arc_write:
            plan.volume_write = VolumeWrite(
                volume_id=volume["volume_id"], volume_no=from_volume,
                terminal_snapshot_json=terminal_target,
                arc_summary=arc_target if arc_write else None,
                terminal_from=existing_raw,
                arc_from=volume.get("arc_summary"),
            )

    # 豁免名单为空：dry-run 只提示，--apply 由 main 升级为 error 拒写。
    if not keep_entity_id_set:
        issues.append(Issue(
            "KEEP-EMPTY", "warning",
            "豁免名单为空——本次会把**全部** canon 实体（含跨位面主角）归档；"
            "主账本角色请用 --keep / --keep-name 指定，或用 --no-keep 显式确认"))
    return plan


def _chapter_volume_no(state: dict, chapter_id: str | None) -> int | None:
    if not chapter_id:
        return None
    return state["chapter_volume_no"].get(chapter_id)


def _origin_label(state: dict, kind: str, chapter_id: str | None) -> str:
    """证据串：``hook@ch24(卷1)`` / ``debt@ch3(卷1)`` / 无章号 / 无卷归属。"""
    if not chapter_id:
        return f"{kind}@（无章号）"
    ch_no = state["chapter_no"].get(chapter_id)
    vol_no = state["chapter_volume_no"].get(chapter_id)
    label = f"{kind}@ch{ch_no}" if ch_no is not None else f"{kind}@{chapter_id}"
    return label + (f"(卷{vol_no})" if vol_no is not None else "(无卷归属)")


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------


def _write_volume_columns(db_path: Path | str, write: VolumeWrite) -> None:
    """直写 volumes 两列（无 service 入口；理由见 docstring「归档口径」表）。

    - ``terminal_snapshot_json``：合并写入（既有键原样保留，只加 / 更新
      ``world_settlement``），只在内容有变化时写；
    - ``arc_summary``：成对标记整块替换 / 追加，不覆盖既有内容。
    """
    sets: list[str] = []
    params: list[Any] = []
    if write.terminal_snapshot_json is not None:
        sets.append("terminal_snapshot_json = ?")
        params.append(write.terminal_snapshot_json)
    if write.arc_summary is not None:
        sets.append("arc_summary = ?")
        params.append(write.arc_summary)
    if not sets:
        return
    sets.append("updated_at = ?")
    params.append(now_iso())
    params.append(write.volume_id)
    conn = get_connection(db_path)
    try:
        conn.execute(f"UPDATE volumes SET {', '.join(sets)} WHERE volume_id = ?", params)
        conn.commit()
    finally:
        conn.close()


def apply_plan(plan: Plan, db_path: Path | str) -> list[dict]:
    """执行计划，返回「改了哪些表 / 哪些行 / 旧值→新值」的完整明细。"""
    actions: list[dict] = []
    characters = CharacterService(db_path)
    world = WorldService(db_path)
    ledger = LedgerService(db_path)

    for row in plan.changes:
        if row.kind == "character":
            characters.update(row.entity_id, CharacterUpdate(inject_mode=row.new))
        elif row.kind == "location":
            world.update_location(row.entity_id, inject_mode=row.new)
        elif row.kind == "faction":
            world.update_faction(row.entity_id, inject_mode=row.new)
        elif row.kind == "hook":
            ledger.update_hook(row.entity_id, HookUpdate(
                status=row.new,
                name=row.extra.get("name_new") or None,
            ))
        elif row.kind == "debt":
            ledger.update_debt(row.entity_id, DebtUpdate(status=row.new))
        actions.append({
            "action": row.action,
            "table": _table_of(row.kind),
            "id": row.entity_id,
            "name": row.name,
            "field": row.field,
            "from": row.old,
            "to": row.new,
            "reason": row.reason,
            "detail": f"{_table_of(row.kind)} {row.entity_id} {row.field}: "
                      f"{row.old!r} → {row.new!r}",
        })

    if plan.volume_write is not None:
        write = plan.volume_write
        _write_volume_columns(db_path, write)
        if write.terminal_snapshot_json is not None:
            actions.append({
                "action": "write_volume_terminal_snapshot",
                "table": "volumes",
                "id": write.volume_id,
                "field": "terminal_snapshot_json",
                "from": write.terminal_from,
                "to": write.terminal_snapshot_json,
                "reason": "settlement_persist",
                "detail": f"volumes#{write.volume_no} terminal_snapshot_json 合并写入 "
                          f"key={SETTLEMENT_SNAPSHOT_KEY}",
            })
        if write.arc_summary is not None:
            actions.append({
                "action": "append_volume_arc_summary",
                "table": "volumes",
                "id": write.volume_id,
                "field": "arc_summary",
                "from": write.arc_from,
                "to": write.arc_summary,
                "reason": "settlement_persist",
                "detail": f"volumes#{write.volume_no} arc_summary 并入结算块"
                          f"（保留既有内容）",
            })
    return actions


def _table_of(kind: str) -> str:
    return {
        "character": "characters",
        "location": "locations",
        "faction": "factions",
        "hook": "hooks",
        "debt": "narrative_debts",
    }.get(kind, kind)


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------


def _short(value: Any, limit: int = 42) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_text(
    plan: Plan, *, actions: list[dict], mode: str, settlement_path: Path,
    keep_reason: str | None,
) -> str:
    lines = [
        f"world_switch: db={plan.db_path} project={plan.project_id} "
        f"volume #{plan.from_volume} → #{plan.to_volume} "
        f"settlement={settlement_path} mode={mode}"
    ]
    lines.append(f"[校验] error {len(plan.errors)} 条 / warning {len(plan.warnings)} 条")
    for issue in plan.issues:
        mark = "✗" if issue.level == "error" else "⚠"
        lines.append(f"  {mark} [{issue.code}] {issue.message}")

    lines.append("[归档口径]")
    lines.append("  · characters / locations / factions：上一世 → inject_mode="
                 f"'{ARCHIVED_INJECT_MODE}'（world_rules 框架级不动）")
    lines.append(f"  · hooks：仍进注入面的状态（{'/'.join(sorted(HOOK_OPEN_STATUSES))}，"
                 f"口径同 context_engine 消费方）→ {HOOK_ARCHIVE_STATUS}"
                 f" + name 追加标记「{SETTLEMENT_MARKER}」；已结线（RESOLVED/ABANDONED）不动")
    if plan.include_debts:
        lines.append(f"  · narrative_debts：{'/'.join(sorted(DEBT_OPEN_STATUSES))} → "
                     f"{DEBT_ARCHIVE_STATUS}（结算核销；--skip-debts 可关）")
    else:
        lines.append("  · narrative_debts：--skip-debts 已关闭，本表不动")
    lines.append("  · volumes：terminal_snapshot_json 合并写入 + arc_summary 追加结算块")
    lines.append("  · 无 DELETE；历史正文 / 台账 / 快照一律保留")

    counts = delta_counts(plan)
    lines.append(
        f"[变更] 本次改 {len(plan.changes)} 行：characters {counts['characters']} / "
        f"locations {counts['locations']} / factions {counts['factions']} / "
        f"hooks {counts['hooks']} / debts {counts['debts']}"
    )
    for row in plan.changes:
        detail = f"{row.field}: {row.old!r} → {row.new!r}"
        if row.kind == "hook" and row.extra.get("name_new"):
            detail += f"；name → {_short(row.extra['name_new'], 60)}"
            if row.extra.get("name_truncated"):
                detail += "（原名超 200 字符已截断）"
        lines.append(f"  · [{_table_of(row.kind)}] {row.name}（{row.entity_id}）{detail}")
        if row.evidence:
            lines.append(f"      证据: {', '.join(row.evidence[:3])}"
                         + (" …" if len(row.evidence) > 3 else ""))
        elif row.kind in ARCHIVE_KINDS:
            lines.append("      证据: 无（按隔离处理归档——请抽查）")

    kept = [r for r in plan.rows if r.reason == REASON_KEEP]
    if kept:
        lines.append("[豁免] " + "、".join(f"{r.name}（{r.entity_id}）" for r in kept))
    else:
        lines.append("[豁免] 无")
    if keep_reason:
        lines.append(f"  · {keep_reason}")

    already = [r for r in plan.rows
               if r.reason in (REASON_ALREADY_ARCHIVED, REASON_ALREADY_CLOSED)]
    if already:
        lines.append(f"[已归档/已结] {len(already)} 行已是目标态（幂等跳过）："
                     + "、".join(_short(r.name, 18) for r in already[:6])
                     + (" …" if len(already) > 6 else ""))
    skipped = [r for r in plan.rows if r.action == "skip"]
    if skipped:
        lines.append(f"[跳过] {len(skipped)} 行（更晚卷归属 / 状态不可达 / 未启用）")
        for r in skipped:
            lines.append(f"  · {_table_of(r.kind)} {_short(r.name, 24)}（{r.entity_id}）"
                         f"理由={r.reason}")

    scope = scope_counts(plan)
    lines.append("[作用域口径] " + " / ".join(
        f"{k} {v['total']}（归档态 {v['archived']}，豁免 {v['kept']}）"
        for k, v in scope.items()))

    if actions:
        lines.append(f"[写入] {len(actions)} 个动作")
        for item in actions:
            lines.append(f"  · {item['action']}: {item['detail']}")
    if not plan.ok:
        lines.append("[结论] 校验不通过 → 拒写（未对数据库做任何修改）")
    elif actions:
        lines.append("[结论] apply 完成（无 DELETE；重跑为零变更）")
    elif mode == "apply":
        lines.append("[结论] apply 完成：本次零变更（幂等，未写库）")
    else:
        lines.append("[结论] dry-run：未写库（加 --apply 落库）")
    return "\n".join(lines)


def build_summary(
    plan: Plan, *, actions: list[dict], mode: str, keep_reason: str | None
) -> dict:
    return {
        "mode": mode,
        "db": str(plan.db_path),
        "project_id": plan.project_id,
        "from_volume": plan.from_volume,
        "to_volume": plan.to_volume,
        "volume_id": (plan.volume or {}).get("volume_id"),
        "settlement": plan.settlement_payload,
        "keep": {
            "ids": plan.keep_ids,
            "names": plan.keep_names,
            "mode": plan.keep_mode,
            "note": keep_reason,
        },
        "delta_counts": delta_counts(plan),
        "scope_counts": scope_counts(plan),
        "rows_changed": len(plan.changes),
        "archived": {
            "characters": [r.to_dict() for r in plan.by_kind("character")
                           if r.action == "archive"],
            "locations": [r.to_dict() for r in plan.by_kind("location")
                          if r.action == "archive"],
            "factions": [r.to_dict() for r in plan.by_kind("faction")
                         if r.action == "archive"],
            "hooks": [r.to_dict() for r in plan.by_kind("hook") if r.action == "archive"],
            "debts": [r.to_dict() for r in plan.by_kind("debt") if r.action == "archive"],
        },
        "kept": [r.to_dict() for r in plan.rows if r.reason == REASON_KEEP],
        "skipped": [r.to_dict() for r in plan.rows if r.action == "skip"],
        "already_archived": [r.to_dict() for r in plan.rows
                             if r.reason == REASON_ALREADY_ARCHIVED],
        "already_closed": [r.to_dict() for r in plan.rows
                           if r.reason == REASON_ALREADY_CLOSED],
        "frame_tables_untouched": list(FRAMEWORK_TABLES_NOT_TOUCHED),
        "issues": [i.to_dict() for i in plan.issues],
        "warnings": [i.to_dict() for i in plan.warnings],
        "errors": [i.to_dict() for i in plan.errors],
        "actions": actions,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="world_switch",
        description=(
            "世界切换（快穿位面）：把上一世作用域按 inject_mode 三态 + hooks/debts 终态"
            "归档，并落结算单到 volumes。默认 dry-run 只打印不改；--apply 才写入。"
            "退出码 0=通过/已写入, 1=校验不通过（拒写）, 2=参数或 IO 错误。"
        ),
        epilog="结算单契约见模块 docstring："
               "{volume_number, settlement{5 口径键}, chips[], note?}。",
    )
    parser.add_argument("--project", required=True, help="目标 project_id（prj_…）")
    parser.add_argument("--from-volume", type=int, required=True,
                        help="要归档的上一世卷号（严格小于 --to-volume）")
    parser.add_argument("--to-volume", type=int, required=True, help="新位面卷号")
    parser.add_argument("--settlement", required=True, type=Path,
                        help="结算单 JSON 路径（volume_number 必须等于 --from-volume）")
    parser.add_argument("--db", type=Path, default=Path(DEFAULT_DB),
                        help=f"SQLite 路径（默认 {DEFAULT_DB}）")
    parser.add_argument("--keep", action="append", default=[], metavar="ENTITY_ID",
                        help="豁免的实体 id（可重复）——主角等跨位面角色")
    parser.add_argument("--keep-name", action="append", default=[], metavar="NAME",
                        help="豁免的实体名（可重复；按 name 精确匹配）")
    parser.add_argument("--no-keep", action="store_true",
                        help="显式声明「不豁免任何人」（无豁免名单时 --apply 需它放行）")
    parser.add_argument("--keep-mode", choices=KEEP_MODES, default="unchanged",
                        help="豁免实体的 inject_mode：unchanged（默认，只豁免）/ "
                             "always（常驻全量）")
    parser.add_argument("--skip-debts", action="store_true",
                        help="不处理 narrative_debts（默认按结算核销为 forgiven）")
    parser.add_argument("--apply", action="store_true", help="落库（缺省=dry-run 只打印）")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="输出机器可读摘要（stdout 仅 JSON）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Windows 控制台默认 GBK：报告里的 ⚠/✗/· 会 UnicodeEncodeError（同 open_volume.py）。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover - 非 TTY / 已重定向
        pass

    try:
        raw_settlement = load_settlement(args.settlement)
    except ValueError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return EXIT_USAGE

    db_path: Path = args.db
    if not db_path.exists():
        sys.stderr.write(f"ERROR: 数据库不存在: {db_path}\n")
        return EXIT_USAGE

    issues: list[Issue] = []
    body = validate_settlement(raw_settlement, args.from_volume, issues)
    try:
        plan = build_plan(
            db_path=db_path, project_id=args.project,
            from_volume=args.from_volume, to_volume=args.to_volume,
            settlement_payload=raw_settlement if isinstance(raw_settlement, dict) else {},
            settlement_body=body,
            keep_ids=list(args.keep), keep_names=list(args.keep_name),
            keep_mode=args.keep_mode, include_debts=not args.skip_debts,
            issues=issues,
        )
    except sqlite3.Error as exc:
        sys.stderr.write(f"ERROR: 数据库打不开 / 读取失败: {db_path} ({exc})\n")
        return EXIT_USAGE

    keep_reason: str | None = None
    if not (args.keep or args.keep_name):
        if args.no_keep:
            keep_reason = "--no-keep：显式确认不豁免任何人（含主角）"
        elif args.apply:
            plan.issues.append(Issue(
                "KEEP-EMPTY", "error",
                "--apply 时豁免名单为空且未给 --no-keep：本命令会把全部 canon 实体"
                "（含跨位面主角）归档。请用 --keep / --keep-name 指定豁免，"
                "或加 --no-keep 显式确认"))
        else:
            keep_reason = ("dry-run 提示：未指定豁免名单"
                           "（--apply 前须补 --keep/--keep-name 或 --no-keep）")

    applied: list[dict] = []
    mode = "apply" if args.apply else "dry-run"
    if args.apply and plan.ok:
        try:
            applied = apply_plan(plan, db_path)
        except (sqlite3.Error, ValueError) as exc:
            # 无全局事务：中断点之前的动作已落库。计划幂等，修好后重跑会跳过已完成行。
            sys.stderr.write(
                f"ERROR: 写入中断（已完成 {len(applied)} 行，均已落库）: {exc}\n"
                f"       修好后重跑 --apply 即可续做（幂等）\n")
            return EXIT_USAGE

    if args.as_json:
        print(json.dumps(
            build_summary(plan, actions=applied, mode=mode, keep_reason=keep_reason),
            ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(plan, actions=applied, mode=mode,
                          settlement_path=args.settlement, keep_reason=keep_reason))
    return EXIT_OK if plan.ok else EXIT_VALIDATION


if __name__ == "__main__":
    sys.exit(main())
