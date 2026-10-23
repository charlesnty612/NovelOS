"""极简 OpenAPI → TypeScript 类型生成器（试点，2026-09-06 审查批次四）。

用法::

    python scripts/export_openapi.py
    python scripts/gen_frontend_types.py
    python scripts/gen_frontend_types.py --check   # 只校验漂移，不写盘（漂移 → 退出码 1）
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

⚠️ ``--check`` 的比对口径与 ``scripts/export_openapi.py`` **共用同一实现**
（``scripts/_codegen_check.py``）：换行归一后比较。本仓无 ``.gitattributes`` 且
``core.autocrlf=true``，git blob 存 LF、Windows 工作树是 CRLF ⇒ 不归一会让闸门
在不同平台/不同 git 配置下给出不同答案，那是假信号不是漂移。

⚠️ **默认路径锚定仓库根**（2026-10-23 批次三十一，3-3）：``--openapi`` / ``--out``
不给时解析为 ``<仓库根>/apps/web/...``（仓库根由**脚本自身位置**推导 ⇒ 与 CWD 无关）。
旧实现是 CWD 相对 ⇒ 在非仓库 CWD 执行会得到「输入不存在」的**假漂移**（exit 2），
而本 CLI 的定位是「可复用于 CI / pre-commit」⇒ 假漂移 = CI 随机红。
显式传 ``--openapi`` / ``--out`` 时仍是 CWD 相对（**行为不变**）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._codegen_check import (  # noqa: E402
    RepoRootNotFound,
    check_artifact,
    default_openapi_path,
    default_types_path,
)


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


def render(schema: dict) -> str:
    """把 OpenAPI schema 渲染成 ``types.generated.ts`` 文本。

    写盘与 ``--check`` 共用本函数 ⇒ 两条路径逐字同源（不会出现「生成用一套、校验用另一套」）。
    """
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
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 / 校验 types.generated.ts")
    parser.add_argument(
        "--openapi",
        type=Path,
        default=None,
        help="openapi.json 路径（默认 <仓库根>/apps/web/openapi.json；**相对仓库根**，与 CWD 无关）",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="生成文件路径（默认 <仓库根>/apps/web/src/api/types.generated.ts；**相对仓库根**）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验漂移、不写盘：一致 → 0，漂移或缺失 → 非 0",
    )
    args = parser.parse_args(argv)

    # 默认路径**先**解析：解析不了要给出**明确文案**，而不是含糊的 MISSING 假漂移。
    try:
        openapi_path = args.openapi if args.openapi is not None else default_openapi_path()
        out_path = args.out if args.out is not None else default_types_path()
    except RepoRootNotFound as exc:
        print(
            f"[ABORT] 找不到仓库根（脚本位置推导：{exc.root}）"
            "—— 无法解析默认输入 / 输出路径。\n"
            "        请显式传 --openapi / --out。",
            file=sys.stderr,
        )
        return 2

    if not openapi_path.exists():
        print(
            f"[ABORT] 找不到 openapi 输入：{openapi_path}\n"
            "        先跑 scripts/export_openapi.py 生成它。",
            file=sys.stderr,
        )
        return 2

    schema = json.loads(openapi_path.read_text(encoding="utf-8"))
    n_ifaces = len(schema.get("components", {}).get("schemas", {}))
    rendered = render(schema)

    if args.check:
        ok, msg = check_artifact(out_path, rendered)
        print(
            f"{msg}\n    (source={openapi_path}, interfaces={n_ifaces})",
            file=sys.stdout if ok else sys.stderr,
        )
        return 0 if ok else 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    print(f"types.generated.ts written: {out_path} ({n_ifaces} interfaces)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
