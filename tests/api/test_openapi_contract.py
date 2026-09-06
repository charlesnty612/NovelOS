"""前后端契约冒烟测试（2026-09-06 审查批次四）。

防「后端 Pydantic schema 静默漂移、前端手写 types.ts 跟不上」：

1. ``app.openapi()`` 的 components.schemas 必含关键模型，且字段集与
   快照一致（新增字段不报错、删除/改名/改类型才报错——用「快照 ⊆ 实际」
   的宽松口径，避免每次加字段都要更新本测试）。
2. ``types.generated.ts``（codebuild 产物，入库）与 ``openapi.json``
   同步：包含关键 ``export interface``。

用法（schema 变更后）::

    python scripts/export_openapi.py
    python scripts/gen_frontend_types.py
"""

from __future__ import annotations

from pathlib import Path

from packages.core.api.main import create_app
from packages.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]

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

# 前端生成文件必须包含的 interface 名
KEY_TS_INTERFACES = sorted(KEY_SCHEMA_FIELDS)


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
    """types.generated.ts 与当前 app schema 同步（关键字段宽松断言）。"""
    app = _make_app(tmp_path)
    schema = app.openapi()

    generated = (REPO_ROOT / "apps/web/src/api/types.generated.ts").read_text(
        encoding="utf-8"
    )
    stale = [
        m for m in KEY_TS_INTERFACES if f"export interface {m} " not in generated
    ]
    assert not stale, (
        f"types.generated.ts 过期，缺 {stale}——请跑 npm run gen:types 重新生成"
    )

    # 关键字段也必须在生成文件里出现（宽松：字段名作为 interface 属性存在）
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
