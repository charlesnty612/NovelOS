"""极简 OpenAPI → TypeScript 类型生成器（试点，2026-09-06 审查批次四）。

用法::

    python scripts/export_openapi.py
    python scripts/gen_frontend_types.py
    # 或一键：npm run gen:types （apps/web 下）

职责：
- 读 ``apps/web/openapi.json``（由 ``scripts/export_openapi.py`` 导出），
- 把 ``components.schemas`` 里的 Pydantic 模型逐个生成 TS ``interface``，
  写入 ``apps/web/src/api/types.generated.ts``。

设计取舍（试点边界）：
- 只做「模型名 → interface」映射：string / integer / number / boolean / null /
  array / $ref / anyOf（含 Optional[X] 呈现为 anyOf [T, null]）/ enum。
- **不生成**请求/响应包装类型与 API client——手写的 ``types.ts`` /
  ``endpoints.ts`` 仍是运行时权威；本文件仅作为「后端 schema 漂移」的
  机器可读对照面（契约测试断言关键字段集）。
- 再生成完全确定性（schema 顺序稳定），可安全入库、可 diff 审查。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _ts_type(schema: dict, schemas: dict) -> str:
    if "$ref" in schema:
        ref = schema["$ref"].rsplit("/", 1)[-1]
        return ref
    if "anyOf" in schema:
        parts = [_ts_type(s, schemas) for s in schema["anyOf"]]
        return " | ".join(parts)
    if "enum" in schema:
        return " | ".join(json.dumps(v, ensure_ascii=False) for v in schema["enum"])
    t = schema.get("type")
    if t == "string":
        return "string"
    if t == "integer":
        return "number"
    if t == "number":
        return "number"
    if t == "boolean":
        return "boolean"
    if t == "null":
        return "null"
    if t == "array":
        item = schema.get("items") or {}
        return f"Array<{_ts_type(item, schemas)}>"
    if t == "object":
        return "Record<string, unknown>"
    return "unknown"


def _interface(name: str, schema: dict, schemas: dict) -> str:
    lines = [f"export interface {name} {{"]
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    for prop, pschema in props.items():
        ts = _ts_type(pschema, schemas)
        # JSON 键可能是非法 TS 标识符（中文/连字符）→ 加引号
        key = prop if prop.isidentifier() else json.dumps(prop)
        opt = "" if prop in required else "?"
        doc = pschema.get("description") or schema.get("description")
        if doc:
            first = str(doc).strip().splitlines()[0]
            lines.append(f"  /** {first} */")
        lines.append(f"  {key}{opt}: {ts};")
    if not props:
        lines.append("  [k: string]: unknown;")
    lines.append("}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 types.generated.ts")
    parser.add_argument(
        "--openapi",
        type=Path,
        default=Path("apps/web/openapi.json"),
        help="openapi.json 路径",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("apps/web/src/api/types.generated.ts"),
        help="生成文件路径",
    )
    args = parser.parse_args()

    schema = json.loads(args.openapi.read_text(encoding="utf-8"))
    schemas = schema.get("components", {}).get("schemas", {})

    out = [
        "/* eslint-disable */",
        "/**",
        " * 由 scripts/gen_frontend_types.py 自动生成——请勿手工编辑。",
        " * 数据源：apps/web/openapi.json（scripts/export_openapi.py 导出）。",
        " * 用途：后端 Pydantic schema 的机器可读对照面（契约检查 / diff 审查）；",
        " * 运行时类型仍以手写 api/types.ts 为权威。",
        " */",
        "",
    ]
    for name in sorted(schemas):
        out.append(_interface(name, schemas[name], schemas))
        out.append("")
    args.out.write_text("\n".join(out), encoding="utf-8")
    print(f"types.generated.ts written: {args.out} ({len(schemas)} interfaces)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
