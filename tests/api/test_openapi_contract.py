"""前后端契约冒烟测试（2026-09-06 审查批次四；2026-09-30 闸门补强）。

防「后端 Pydantic schema 静默漂移、前端手写 types.ts 跟不上」：

1. ``app.openapi()`` 的 components.schemas 必含关键模型，且字段集与
   快照一致（新增字段不报错、删除/改名/改类型才报错——用「快照 ⊆ 实际」
   的宽松口径，避免每次加字段都要更新本测试）。
2. ``types.generated.ts``（codebuild 产物，入库）与**当前** app schema 的
   ``components.schemas`` 集合**完全相等**（双向差集都须为空），
   外加关键模型字段级宽松断言。

2026-09-30 补强理由（旧实现两处缺陷，见下）：

- 旧实现把 ``app.openapi()`` 赋给 ``_schema`` 后**从未使用**——下划线前缀
  是为压制 ruff F841（CHANGELOG:1084 载明），属改名掩盖而非修复；所谓
  「与当前 schema 同步」实为对入库文件的纯静态文本检查，与当前后端无关。
- interface 名断言面只有 ``KEY_SCHEMA_FIELDS`` 的 5 个 Key 名，其余 46 个
  interface 即使漂移也不会让本文件变红。

用法（schema 变更后）::

    python scripts/export_openapi.py
    python scripts/gen_frontend_types.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from packages.core.api.main import create_app
from packages.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED_TS = REPO_ROOT / "apps/web/src/api/types.generated.ts"

# 关键模型 → 必须持续存在的字段快照（宽松口径：只查「存在」，不锁全集）
KEY_SCHEMA_FIELDS: dict[str, set[str]] = {
    "Project": {"project_id", "name", "premise", "status", "created_at"},
    "Chapter": {"chapter_id", "project_id", "number", "title", "status"},
    "Draft": {"draft_id", "chapter_id", "version", "content"},
    "Character": {"character_id", "project_id", "name"},
    "Volume": {"volume_id", "project_id", "number", "status"},
}
# 注：WorkflowRun / QualityReport / ModelProfile / CapabilityBinding /
# ReferenceCanon 等端点当前返回裸 dict（无 response_model），不进 OpenAPI
# components——见 docs/reviews/全项目审查与优化建议-2026-09-06.md 批次四注记。
# 给这些端点补 response_model 属后续优化，不在本契约测试断言面内。

# 生成文件里 interface 的行首形态。``gen_frontend_types.py`` 恒产
# ``export interface <Name> {``，据此取名；解析不到的名字只会让两侧差集非空
# （判红），不会被静默放过。
_TS_INTERFACE_RE = re.compile(r"^export interface ([A-Za-z0-9_]+)\s*\{", re.MULTILINE)


def _generated_interface_names(generated: str) -> set[str]:
    """生成文件里全部 ``export interface`` 名——全量，不再只取 Key。"""
    return set(_TS_INTERFACE_RE.findall(generated))


def _schema_names(schema: dict) -> set[str]:
    """``app.openapi()`` 的 components.schemas 全名集合。"""
    return set(schema.get("components", {}).get("schemas", {}))


def _drift_diff(schema_names: set[str], generated_names: set[str]) -> str:
    """两侧集合差异的可读诊断；完全一致时返回空串。"""
    only_schema = sorted(schema_names - generated_names)
    only_generated = sorted(generated_names - schema_names)
    if not only_schema and not only_generated:
        return ""
    return f"仅 schema 有: {only_schema}；仅生成文件有: {only_generated}"


def _make_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="ERROR")
    return create_app(settings)


def test_openapi_contains_key_schemas_with_required_fields(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    schema = app.openapi()
    components = schema.get("components", {}).get("schemas", {})

    missing_models = [m for m in KEY_SCHEMA_FIELDS if m not in components]
    assert not missing_models, f"openapi 缺关键模型: {missing_models}"

    for model, expected_fields in KEY_SCHEMA_FIELDS.items():
        props = set(components[model].get("properties", {}))
        missing = expected_fields - props
        assert not missing, f"{model} 缺字段: {missing}（后端 schema 漂移？）"


def test_generated_ts_matches_openapi(tmp_path: Path) -> None:
    """types.generated.ts 与当前 app schema 同步（**集合级**：interface 名全量相等）。"""
    app = _make_app(tmp_path)
    _schema = app.openapi()

    generated = GENERATED_TS.read_text(encoding="utf-8")
    # interface 名断言面：全量（2026-09-30 起不再是 5 个 Key）。
    KEY_TS_INTERFACES = _generated_interface_names(generated)
    schema_names = _schema_names(_schema)

    # ① 缺失向（沿用旧断言语义，覆盖面由 5 个 Key 扩到全量）
    stale = sorted(schema_names - KEY_TS_INTERFACES)
    assert not stale, (
        f"types.generated.ts 过期，缺 {stale}——请跑 npm run gen:types 重新生成"
    )

    # ② 集合级相等：补上旧断言查不出的「多出」方向。
    #    _schema 直接出现在断言表达式里（不再是无用的中间赋值）。
    assert set(_schema["components"]["schemas"]) == KEY_TS_INTERFACES, (
        f"types.generated.ts 与当前 schema 集合不一致——"
        f"{_drift_diff(schema_names, KEY_TS_INTERFACES)}——"
        f"请跑 npm run gen:types 重新生成"
    )

    # 关键字段也必须在生成文件里出现（宽松：字段名作为 interface 属性存在）。
    # 字段级口径保持原样：只查 KEY_SCHEMA_FIELDS 的 5 个 Key 模型，不扩字段面。
    for model, expected_fields in KEY_SCHEMA_FIELDS.items():
        start = generated.find(f"export interface {model} ")
        assert start >= 0
        end = generated.find("\nexport interface ", start + 1)
        body = generated[start : end if end != -1 else len(generated)]
        missing = [f for f in expected_fields if f"{f}" not in body]
        assert not missing, f"{model} 生成文件缺字段 {missing}"


def test_openapi_health_endpoint_present(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    schema = app.openapi()
    assert "/api/health" in schema["paths"]


def test_drift_check_rejects_stale_generated_file(tmp_path: Path) -> None:
    """突变自检：抽掉任一**非 Key** interface，drift 比对必须报红并点名。

    为什么需要这条：主断言在库内自洽时恒绿，「它对漂移真的敏感」无法由它
    自己证明。本用例用**构造的**残缺生成文件做等价突变（把目标 interface
    改名＝从集合里删掉该名字），不触碰入库产物。

    若前提断言 ``not _drift_diff(schema_names, names)`` 变红，说明库内已存在
    真实漂移——此时应先处理漂移（重跑 regen），本用例的结论才有意义。
    """
    app = _make_app(tmp_path)
    schema_names = _schema_names(app.openapi())
    generated = GENERATED_TS.read_text(encoding="utf-8")
    names = _generated_interface_names(generated)

    # 前提：未突变时两侧一致，否则样本无效
    assert not _drift_diff(schema_names, names), (
        "库内 types.generated.ts 与当前 schema 本就不一致，突变样本无效——"
        "请先处理真实漂移（重跑 regen）"
    )

    victims = sorted(names - set(KEY_SCHEMA_FIELDS))
    assert victims, "生成文件里找不到非 Key interface，无法构造突变样本"
    target = victims[0]

    mutated = generated.replace(
        f"export interface {target} {{", "export interface __MUTATED_OUT__ {", 1
    )
    assert mutated != generated, f"突变未生效：生成文件里没有 interface {target}"
    mutated_names = _generated_interface_names(mutated)
    assert target not in mutated_names, "改名后目标名仍留在集合里，样本无效"

    diff = _drift_diff(schema_names, mutated_names)
    assert diff, "drift 比对对残缺生成文件没有报错——闸门失效"
    assert target in diff, f"drift 诊断未点名被抽掉的 interface {target}: {diff}"


def test_main_assertion_rejects_stale_generated_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """闭合到**真实主断言**：残缺副本必须让 test_generated_ts_matches_openapi 打红。

    存在的理由（task-10 review MINOR-3）：上面的自检只证明辅助函数
    ``_drift_diff`` 会报红；而主断言读的是磁盘上真实入库的 types.generated.ts，
    永远不可能因「副本残缺」而变红——辅助函数与主断言之间此前只是**逻辑推导**，
    没有直接证据。本用例把断点补上：monkeypatch 掉模块常量，让主断言真的
    读到残缺内容。

    可 patch 的依据：主断言函数体内以 ``GENERATED_TS.read_text(...)`` 形式
    运行时查找模块属性（不是 ``from ... import GENERATED_TS`` 那种读时绑定）。
    monkeypatch 是 fixture，函数返回后自动还原，真实文件不受影响。
    """
    # 阶段①：正常文件下主断言通过（不抛即绿）
    test_generated_ts_matches_openapi(tmp_path)

    # 造残缺副本：把任一**非 Key** interface 改名＝该名从集合里消失
    generated = GENERATED_TS.read_text(encoding="utf-8")
    names = _generated_interface_names(generated)
    victims = sorted(names - set(KEY_SCHEMA_FIELDS))
    assert victims, "生成文件里找不到非 Key interface，无法构造残缺副本"
    target = victims[0]

    stale_copy = tmp_path / "types.generated.stale.ts"
    stale_copy.write_text(
        generated.replace(
            f"export interface {target} {{", "export interface __MUTATED_OUT__ {", 1
        ),
        encoding="utf-8",
    )
    stale_names = _generated_interface_names(stale_copy.read_text(encoding="utf-8"))
    assert stale_names != names, "残缺副本未生效：目标 interface 名仍在集合里"
    assert target not in stale_names, "残缺副本里目标名未被抽掉，样本无效"

    # 阶段②：主断言读到残缺副本必须打红，并点名被抽掉的 interface
    monkeypatch.setattr(sys.modules[__name__], "GENERATED_TS", stale_copy)
    with pytest.raises(AssertionError) as excinfo:
        test_generated_ts_matches_openapi(tmp_path)

    message = str(excinfo.value)
    assert target in message, (
        f"主断言已报红但未点名被抽掉的 interface {target}: {message}"
    )


def test_main_assertion_rejects_surplus_generated_interface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """闭合 ``:112`` 集合相等断言的「**多出**」方向（task-12 追加轮）。

    与上一个用例互补且不可互相替代：那个抽掉一个 interface 打**缺失**向
    （红在 :106 的 stale 断言）；本用例往生成文件里**塞进**一个 schema 里不存在的
    interface——stale 方向只查「缺」，对「多」完全无感，只有 :112 的集合相等会红。

    单独覆盖的理由（task-10 review MINOR-3 遗留风险①）：MUT-C 的一次性脚本已证明
    :112 不可替代，但无常驻看守就等于论证没入库。本用例用报错文案把「红在 :112」
    这件事本身也钉住。
    """
    # 阶段①：正常文件下主断言通过（不抛即绿）
    test_generated_ts_matches_openapi(tmp_path)

    generated = GENERATED_TS.read_text(encoding="utf-8")
    names = _generated_interface_names(generated)
    surplus_name = "__SURPLUS_NOT_IN_SCHEMA__"
    assert surplus_name not in names, "占位名与真实 interface 冲突，样本无效"

    # 塞进一个 schema 里不存在的 interface（残缺＝比实际多，而非少）
    surplus_copy = tmp_path / "types.generated.surplus.ts"
    surplus_copy.write_text(
        generated + f"\nexport interface {surplus_name} {{\n  x: string;\n}}\n",
        encoding="utf-8",
    )
    surplus_names = _generated_interface_names(
        surplus_copy.read_text(encoding="utf-8")
    )
    assert surplus_names == names | {surplus_name}, "多余 interface 未写进副本"

    monkeypatch.setattr(sys.modules[__name__], "GENERATED_TS", surplus_copy)
    with pytest.raises(AssertionError) as excinfo:
        test_generated_ts_matches_openapi(tmp_path)

    message = str(excinfo.value)
    # 报错必须点名那个多余的名字
    assert surplus_name in message, (
        f"主断言已报红但未点名多余的 interface {surplus_name}: {message}"
    )
    # 且必须红在 :112 集合相等分支（stale 分支的文案是「过期，缺 …」），
    # 否则「多出」方向其实没被覆盖，本用例即空转。
    assert "集合不一致" in message, (
        f"报红来自 :112 集合相等断言之外的分支，多出方向覆盖无效: {message}"
    )
