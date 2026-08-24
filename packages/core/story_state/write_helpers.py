"""Story State 双写面共享写入助手（V2.0 Wave B）。

职责：
- 把 ``write_through.write_through``（canon 写透）与 ``packages.domain.*.service``（管理面直写）
  共用的「字段三态语义」「JSON 序列化」「updated_at 注入」等横切关注点抽到本模块。
- 现状：两路写面各有自己的 ``_dump_json`` / ``_dump_json_or_null`` / ``_load_who_knows`` 实现，
  散落在 character / world / ledger / plot 四个 domain service 与 write_through.py 中；
  散落实现一致性靠「代码评审」维持，缺统一口径——本模块提供**唯一权威实现**。

设计要点：
- 本模块**纯函数 + SQL 片段生成**，不持任何连接；调用方在自己事务内执行生成的 SQL。
- who_knows 三态语义（对齐 ``knowledge-permission-v0.md §6`` / ``state-delta-v0.md §2.6``）：
    - ``None``（缺失）→ DB 列置 ``NULL``（沿用实体现状，不参与合并）
    - ``[]``        → DB 列置 ``'[]'``（显式置空）
    - 非空 list     → JSON 字符串（``ensure_ascii=False``）
  编码由 :func:`encode_who_knows` 负责；``canon`` 与 ``domain`` 双路统一调用。
- visibility 默认值在调用方决定（不同表 DDL 默认不同，如 locations='PUBLIC'，
  factions='VISIBLE'，world_rules='PUBLIC'，hooks='RESTRICTED'，debts='RESTRICTED'）；
  本模块提供 :func:`resolve_visibility` 让调用方传 default + value 统一处理。
- 单一 ``json.dumps(ensure_ascii=False)`` 实现：``_dump``（对象→JSON）与
  ``_dump_or_null``（None→NULL，list/dict→JSON）双版，对应两路写面的现有口径。
- ``build_set_clause`` / ``_inject_who_knows_clause``：UPDATE 路径三态「缺失=不更新该列」
  vs 「非 None=覆盖」的 SQL 片段生成——write_through 与 domain ledger 的 hooks/debts
  同一实现。
- 模块依赖纪律：本模块只依赖 ``json``；不 import 任何具体 service / model，
  避免循环依赖；可被 ``write_through``、``domain/character``、``domain/world``、
  ``domain/ledger``、``domain/plot`` 同时调用。

API 契约（V2.0 Wave B 起稳定）：
- :func:`encode_who_knows`     —— 三态编码（None/[]/list → None/'[]'/JSON）。
- :func:`decode_who_knows`     —— 反序列化（DB raw → list|None，空字符串/None 视为 None）。
- :func:`resolve_visibility`   —— default + value 兜底。
- :func:`dump_json`            —— 对象/dict/list → JSON（ensure_ascii=False）。
- :func:`dump_json_or_null`    —— None → NULL；list/dict → JSON。
- :func:`now_iso_for_db`       —— 时间戳字符串（调用 ``packages.core.ids.now_iso``）。
- :func:`build_set_clause`     —— UPDATE 路径 SET 子句生成（dict → "k1=:k1, k2=:k2"）。
- :func:`who_knows_update_clause` —— UPDATE 路径「缺失=不更新」vs「非 None=覆盖」三态子句。
"""

from __future__ import annotations

import json
from typing import Any

from packages.core.ids import now_iso

# ---------------------------------------------------------------------------
# who_knows 三态编码（唯一权威实现）
# ---------------------------------------------------------------------------


def encode_who_knows(value: list | None) -> str | None:
    """三态语义编码（对齐 knowledge-permission-v0.md §6 / state-delta-v0.md §2.6）。

    - ``None``（缺失）→ ``NULL``（沿用实体现状，不参与合并）
    - ``[]``         → ``'[]'``（显式置空）
    - 非空 list       → JSON 字符串（``ensure_ascii=False``）

    canon（write_through）与 domain（character / world / ledger / plot）双路
    必须统一调用本函数，避免出现「domain 显式空 '[]'」与「canon 置 NULL」差异。
    """
    if value is None:
        return None
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return None


def decode_who_knows(raw: str | None) -> list[str] | None:
    """DB raw → list|None（反序列化）。

    规则：
    - ``None`` / 空字符串 → ``None``（沿用实体现状）
    - 否则尝试 ``json.loads``；解析失败 → ``None``（兜底，避免污染调用方）
    - 解析成功但非 list → ``None``（类型不匹配，按缺失处理）

    适用于 ``characters.who_knows`` / ``character_states.who_knows`` /
    ``locations.who_knows`` / ``factions.who_knows`` / ``world_rules.who_knows`` /
    ``hooks.who_knows`` / ``narrative_debts.who_knows`` / ``chapters.who_knows`` 等。
    """
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        return list(raw) if all(isinstance(x, str) for x in raw) else None
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
        return parsed
    return None


# ---------------------------------------------------------------------------
# visibility 兜底
# ---------------------------------------------------------------------------


def resolve_visibility(value: str | None, default: str) -> str:
    """``None`` → 表默认 visibility；显式 → 透传。

    各表 DDL DEFAULT 不同：
    - characters / character_states: PUBLIC / VISIBLE
    - locations / world_rules: PUBLIC
    - factions: VISIBLE
    - chapters / scenes: VISIBLE
    - hooks / narrative_debts / plot_events: RESTRICTED

    调用方传 default 即可。
    """
    if value is None or value == "":
        return default
    return value


# ---------------------------------------------------------------------------
# JSON 列序列化（ensure_ascii=False，保持中文可读）
# ---------------------------------------------------------------------------


def dump_json(value: Any) -> str:
    """对象/dict/list → JSON 字符串。

    ``None`` 传入也兜底为 ``"{}"``（与现行 domain service 一致，避免破坏现有调用方
    把 ``None`` 当成空对象写入 JSON 列）；列表单独传入也保持原样序列化。
    """
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def dump_json_or_null(value: list | dict | None) -> str | None:
    """``None`` → ``NULL``；list/dict → JSON 字符串。

    与 :func:`dump_json` 区别：本函数保留 ``None`` 语义（写入 ``NULL``），
    适用于 who_knows 列与所有「可空 JSON 列」。
    """
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 时间戳
# ---------------------------------------------------------------------------


def now_iso_for_db() -> str:
    """``created_at`` / ``updated_at`` 列写入用的 UTC ISO-8601 字符串。

    单一权威实现，避免散落 ``datetime.now(timezone.utc).isoformat()``。
    """
    return now_iso()


# ---------------------------------------------------------------------------
# UPDATE SET 子句生成
# ---------------------------------------------------------------------------


def build_set_clause(fields: dict[str, Any]) -> str:
    """UPDATE SET 子句生成：dict → ``"k1 = :k1, k2 = :k2, ..."``。

    调用方自管 params：``params = dict(fields); params["pk"] = pk_value``。

    注意：本函数只生成子句与占位符；不负责白名单校验（调用方按业务白名单
    过滤后再传入）；who_knows 三态语义走 :func:`who_knows_update_clause` 单独生成。
    """
    return ", ".join(f"{k} = :{k}" for k in fields)


def who_knows_update_clause(
    db_who: str | None,
    *,
    include_who_knows: bool = True,
) -> tuple[str, dict[str, Any]]:
    """UPDATE 路径「缺失=不更新该列」vs「非 None=覆盖」三态子句生成。

    - ``db_who`` 是 :func:`encode_who_knows` 编码后的值：
        - ``None``：本次不更新该列（沿用 DB 现存值）
        - ``'[]'`` 或 JSON 字符串：本次覆盖

    返回：``(sql_clause, params_dict)``：
    - 若 ``include_who_knows=False`` 或 ``db_who is None``：返回 ``("", {})``（调用方跳过 who_knows 列）。
    - 否则：返回 ``("who_knows = :who_knows", {"who_knows": db_who})``。

    用于：write_through 中 characters/locations/factions/world_rules/narrative_debts
    的 UPDATE，以及 domain LedgerService 的 hooks / narrative_debts 的 UPDATE。
    """
    if not include_who_knows or db_who is None:
        return "", {}
    return "who_knows = :who_knows", {"who_knows": db_who}


__all__ = [
    "encode_who_knows",
    "decode_who_knows",
    "resolve_visibility",
    "dump_json",
    "dump_json_or_null",
    "now_iso_for_db",
    "build_set_clause",
    "who_knows_update_clause",
]
