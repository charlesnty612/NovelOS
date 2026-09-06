"""导出 OpenAPI schema JSON（前端类型 codegen 输入）。

用法::

    python scripts/export_openapi.py --out apps/web/openapi.json

从 ``create_app()`` 导出 ``app.openapi()``。零新增依赖；生成的 JSON 供
``scripts/gen_frontend_types.py``（内置极简 codegen）或社区工具
（openapi-typescript / orval）消费。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.api.main import create_app  # noqa: E402
from packages.core.config import Settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 OpenAPI schema JSON")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("apps/web/openapi.json"),
        help="输出路径（默认 apps/web/openapi.json）",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="临时 data 目录（默认系统临时目录；避免触碰真实 data/novelos.db）",
    )
    args = parser.parse_args()

    import tempfile

    data_dir = args.data_dir or Path(tempfile.mkdtemp(prefix="novelos_openapi_"))
    settings = Settings(data_dir=data_dir, log_level="ERROR")
    # 不触发 lifespan（迁移/自愈无需跑）；仅需要路由表与 schema。
    app = create_app(settings)
    schema = app.openapi()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_paths = len(schema.get("paths", {}))
    n_schemas = len(schema.get("components", {}).get("schemas", {}))
    print(f"openapi.json written: {args.out} ({n_paths} paths, {n_schemas} schemas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
