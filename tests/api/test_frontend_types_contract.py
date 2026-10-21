"""前端契约双源治理的机器看守（2026-10-21）。

背景
----
AGENTS.md §二 分层约定原文：``apps/web/src/api/types.ts`` = **手写面 + 从
``types.generated.ts`` 重导出**；手写只保留两类（① 后端**刻意不建模**的请求体；
② 尚未进 OpenAPI 的）。

复算（2026-10-21）发现它没做到：``types.ts`` 里有 **35 个名字**既在
``components.schemas`` 里、又是**独立手写副本**（不是重导出）⇒ 后端加字段前端看不见。
字段级实测漂移：``Chapter`` 缺 ``outline_json``、``Character`` 缺 ``aliases`` /
``inject_mode``、``ContinueVariant`` 缺 ``error``（后端早就在返回）。TS **少字段不
报错** ⇒ 漂移静默累积，而 78 处 import 吃的就是这份旧副本。

而 ``tests/api/test_openapi_contract.py`` 只校 ``types.generated.ts ↔ 活 schema``，
**从不看 ``types.ts``** ⇒ 这条约定一直是散文纪律，没有任何闸门。

⚠️ **本文件不声称「35 个已清零」**（2026-10-21 订正：曾有版本把该清单写成空集合并
注明「本批已全部改成重导出」，而 ``types.ts`` 实际仍有 147 条手写声明、35 个双源名字
一个没动 ⇒ 那条用例自己就是红的。**声称与实测分离**是本仓反复登记的形状，故此处如实
写成「债：35 笔，逐条登记理由」）。

本看守做五件事
--------------
1. **阻断新增双源**：手写声明名 ∩ OpenAPI schema 必须 ⊆ :data:`HANDWRITTEN_DEBT`
   （不在清单里的新重名 ⇒ 红并点名）；
2. **不许留僵尸条目**：清单里每一项都必须仍是真实重名（退债=改重导出后**删条目**，
   删条目是唯一退债方式，逼后来者递减）；
3. **理由不得为空**：每条登记写清「为什么这处暂时只能手写」——空理由等于没登记
   （同坑区「skip 理由不是元数据，它是对覆盖率的断言」）；
4. **重导出不得指向空气**：``export type { Src as Local }`` 的**源名**必须真实存在于
   ``types.generated.ts``（别名本地名不算）；
5. **钉住本批修掉的 4 个字段在「有效面」上可见**：手写副本在场时查手写体，已改重导出时
   查生成体——两种形态都必须可见（撤掉任一 ⇒ 红）。

⚠️ 残余边界（如实登记）
---------------------
只比对**名字集合**与上述 4 个字段，**不做全字段双向差集**：``extends`` / ``Omit<>`` /
内联对象三种形态需要真正的 TS 解析器，纯正则会产假阳性（2026-10-21 实测：简单正则把
``Volume extends Omit<VolumeListItem,'chapter_count'>`` 误判成缺 9 个字段——量具失真长得
像被测物有病）。全字段级别的漂移由后端 ``response_model`` + codegen 负责。

判别动作（撤修复必红，均已手工验证）
----------------------------------
- 往 ``types.ts`` 新增一个与 schema 同名的未登记手写声明 ⇒ 用例 1 红；
- 把某条登记改成重导出后**不删条目** ⇒ 用例 2 红；
- 清空某条理由 ⇒ 用例 3 红；
- 改某个重导出源名成不存在名字 ⇒ 用例 4 红；
- 从 ``Chapter`` 手写体删掉 ``outline_json`` ⇒ 用例 5 红。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TYPES_TS = REPO_ROOT / "apps/web/src/api/types.ts"
GENERATED_TS = REPO_ROOT / "apps/web/src/api/types.generated.ts"
OPENAPI_JSON = REPO_ROOT / "apps/web/openapi.json"

#: 存量双源债（35 笔，2026-10-21 实测）→ 登记理由。
#:
#: **只许递减**：把某个名字改成 ``export type X = GeneratedX`` 之后，删掉这里对应项
#: （删条目是唯一退债方式，用例 2 会替你把「僵尸条目」抓出来）。
_DEBT_REASON = (
    "响应模型：后端已有 response_model 与 codegen 产物，但本批只修了字段级缺失"
    "（4 个字段），未做批量替换——纯重导出需处理 extends / Omit<> / 内联对象三种形态，"
    "换法本身要单独验证（见本文件「残余边界」）"
)
HANDWRITTEN_DEBT: dict[str, str] = {
    name: _DEBT_REASON
    for name in (
        "AiCallLogDetail", "AiCallLogSummary", "BackupMetadata", "BackupPackage",
        "Chapter", "Character", "CharacterState", "ContextPreviewItem",
        "ContextPreviewLayer", "ContinueResponse", "ContinueVariant", "Debt",
        "DeconstructStartResponse", "DeliveryChapter", "DeliveryEvidenceSource",
        "DeliveryIssue", "DeliveryLength", "DeliveryQuality", "DeliveryReason",
        "DeliveryRollUp", "DeliverySigning", "DeliverySigningItem",
        "DeliveryVerdictResponse", "Draft", "GenrePack", "GenrePackSummary",
        "HealthResponse", "Hook", "Project", "SigningCheckItem",
        "SigningCheckResult", "SnapshotResponse", "StyleSample", "Volume",
        "VolumeListItem",
    )
}
#: 清单长度上限：防止「遇到红就往清单加一项」这种退化解法。
DEBT_CAP = 35

#: 本批（2026-10-21）修掉的真实漂移字段：必须在**有效面**上可见（撤掉必红）。
REQUIRED_VISIBLE_FIELDS: tuple[tuple[str, str], ...] = (
    ("Chapter", "outline_json"),
    ("Character", "aliases"),
    ("Character", "inject_mode"),
    ("ContinueVariant", "error"),
)


def _openapi_schema_names() -> set[str]:
    data = json.loads(OPENAPI_JSON.read_text(encoding="utf-8"))
    schemas = data.get("components", {}).get("schemas", {})
    assert isinstance(schemas, dict) and schemas, (
        f"{OPENAPI_JSON} 的 components.schemas 为空或缺失 —— "
        "看守读不到权威集合会假绿，先查 openapi.json 本身"
    )
    return set(schemas)


def _handwritten_names() -> set[str]:
    """``types.ts`` 里**手写声明**的名字（``export type {`` 重导出块不算：``{`` 非 ``\\w``）。"""
    text = TYPES_TS.read_text(encoding="utf-8")
    return set(re.findall(r"^export (?:interface|type) (\w+)", text, re.M))


def _reexport_pairs() -> dict[str, str]:
    """``export type { Src as Local, Other }`` → ``{local: source}``。

    ⚠️ 必须区分**源名**与**本地名**（2026-10-21 实测踩到）：本文件有
    ``RevealPolicyOut as RevealPolicy`` / ``ListDiff as SnapshotListDiff`` 这类别名
    重导出，只取本地名去比对 ``types.generated.ts`` 会误判成「重导出指向空气」。
    """
    text = TYPES_TS.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for block in re.finditer(r"export type \{([^}]+)\}", text, re.M | re.S):
        for part in block.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            if " as " in part:
                src, local = (s.strip() for s in part.split(" as ", 1))
            else:
                src = local = part
            out[local] = src
    return out


def _generated_names() -> set[str]:
    text = GENERATED_TS.read_text(encoding="utf-8")
    return set(re.findall(r"^export (?:interface|type) (\w+)", text, re.M))


def _handwritten_body(name: str) -> str:
    text = TYPES_TS.read_text(encoding="utf-8")
    m = re.search(rf"^export interface {re.escape(name)}\b[^{{]*\{{(.*?)^\}}", text, re.M | re.S)
    return m.group(1) if m else ""


def _shadowing() -> set[str]:
    """双源集合：手写声明名 ∩ OpenAPI schema 名。"""
    return _handwritten_names() & _openapi_schema_names()


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


def test_openapi_json_is_readable_and_non_trivial() -> None:
    """前置：权威集合本身必须可读且非空（量具坏掉时本文件必须红，而不是假绿）。"""
    names = _openapi_schema_names()
    assert len(names) > 100, f"OpenAPI schema 数量异常：{len(names)}"
    assert {"Project", "Chapter"} <= names, "OpenAPI schema 集合缺关键模型"


def test_no_new_handwritten_duplicate_of_openapi_schema() -> None:
    """新增「既在 OpenAPI 里、又手写一份」的名字 ⇒ 红并点名。"""
    new_drift = sorted(_shadowing() - set(HANDWRITTEN_DEBT))
    assert not new_drift, (
        f"发现新增的前端手写副本（后端 OpenAPI 里已有同名 schema）：{new_drift}。"
        "`types.ts` 应只从 `types.generated.ts` 重导出；若确实不能纯重导出，"
        "必须在 HANDWRITTEN_DEBT 登记名字与理由（显式欠债，不许静默新增）"
    )


def test_debt_list_has_no_stale_entries() -> None:
    """清单里每一项都必须仍是真实重名（改成重导出后必须删条目）。"""
    stale = sorted(set(HANDWRITTEN_DEBT) - _shadowing())
    assert not stale, (
        f"清单里有 {len(stale)} 条已不再重名：{stale} —— "
        "说明它们已被改成重导出/已删除，请从 HANDWRITTEN_DEBT 删除对应项（退债必须显式）"
    )


def test_debt_entries_have_reasons() -> None:
    """每条登记都必须写非空理由（空理由让复核者停止追问，等于没登记）。"""
    empty = sorted(name for name, reason in HANDWRITTEN_DEBT.items() if not reason.strip())
    assert not empty, f"HANDWRITTEN_DEBT 里有条目没写理由：{empty}"


def test_debt_never_grows() -> None:
    """清单长度不得增长（防止「遇到红就往清单加一项」）。"""
    assert len(HANDWRITTEN_DEBT) <= DEBT_CAP, (
        f"双源债清单 {len(HANDWRITTEN_DEBT)} 项 > 上限 {DEBT_CAP}：只许递减"
    )


def test_reexports_are_not_empty() -> None:
    """重导出集合必须非空（清空 ⇒ 双源治理退化成全手写）。"""
    assert _reexport_pairs(), (
        "types.ts 里没有任何从 './types.generated' 的重导出 —— 双源治理退化成全手写"
    )


def test_reexports_point_at_real_generated_names() -> None:
    """重导出的**源名**必须真实存在于 ``types.generated.ts``（防指向空气）。"""
    pairs = _reexport_pairs()
    missing = sorted({src for src in pairs.values()} - _generated_names())
    assert not missing, (
        f"重导出的**源名**在 types.generated.ts 里不存在：{missing} —— "
        "通常是 codegen 改名后 types.ts 没跟上（指向空气 ⇒ 该类型实际不可用）"
    )


def test_generated_surface_is_non_trivial() -> None:
    """生成面本身必须非空（它是重导出的来源，空了则重导出全部指向空气）。"""
    generated = _generated_names()
    assert len(generated) > 50, f"types.generated.ts 声明数异常：{len(generated)}"


def test_drifted_fields_are_visible_on_effective_surface() -> None:
    """本批修掉的 4 个字段必须在**有效面**上可见（撤掉任一 ⇒ 红）。

    有效面 = 「若该名字仍是手写副本 → 查手写体；若已改成重导出 → 查生成体」。
    ⚠️ 只查生成体会**测不到**真实缺陷：缺陷恰恰是「手写副本缺字段」，而生成面一直有
    （2026-10-21 订正：本文件曾只断言生成面 ⇒ 从手写 ``Chapter`` 删掉 ``outline_json``
    仍然全绿，那是「断言了结果 ≠ 判别了机制」）。
    """
    reexported = _reexport_pairs()
    generated_text = GENERATED_TS.read_text(encoding="utf-8")
    missing: list[str] = []

    for type_name, field in REQUIRED_VISIBLE_FIELDS:
        if type_name in reexported:
            src = reexported[type_name]
            m = re.search(
                rf"^export interface {re.escape(src)}\b[^{{]*\{{(.*?)^\}}",
                generated_text, re.M | re.S,
            )
            assert m, f"types.generated.ts 里找不到 `export interface {src}`（重导出源）"
            body = m.group(1)
        else:
            body = _handwritten_body(type_name)
            assert body, (
                f"types.ts 里既没有 `export interface {type_name}` 也没有它的重导出"
                "（夹具前提失效）"
            )
        if not re.search(rf"^\s+{re.escape(field)}\??\s*:", body, re.M):
            missing.append(f"{type_name}.{field}")

    assert not missing, (
        f"有效面上缺字段：{missing} —— 后端 OpenAPI 里确有它们（2026-10-21 漂移修复），"
        "删掉等于把「前端看不见后端字段」的缺陷放回来"
    )
