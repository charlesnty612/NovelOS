"""导出 OpenAPI schema JSON（前端类型 codegen 输入）。

用法::

    python scripts/export_openapi.py --out apps/web/openapi.json
    python scripts/export_openapi.py --check      # 只校验漂移，不写盘（漂移 → 退出码 1）

从 ``create_app()`` 导出 ``app.openapi()``。零新增依赖；生成的 JSON 供
``scripts/gen_frontend_types.py``（内置极简 codegen）或社区工具
（openapi-typescript / orval）消费。

⚠️ **默认路径锚定仓库根**（2026-10-23 批次三十一，3-3）：``--out`` 不给时解析为
``<仓库根>/apps/web/openapi.json``（仓库根由**脚本自身位置**推导 ⇒ 与 CWD 无关）。
旧实现是 CWD 相对的 ``Path("apps/web/openapi.json")`` ⇒ 在非仓库 CWD 执行会得到
「产物不存在」的**假漂移**（exit 1），而本 CLI 的定位是「可复用于 CI / pre-commit」
⇒ 假漂移 = CI 随机红。显式传 ``--out`` 时仍是 CWD 相对（**行为不变**）。

⚠️ **比对口径（``--check``）：换行归一后再比。**
本仓无 ``.gitattributes``，且本机 ``core.autocrlf=true``：git blob 里存的是 LF，
Windows 工作树是 CRLF，而 ``Path.write_text`` 在 Windows 上也会把 ``\\n`` 翻成 ``\\r\\n``。
若不归一行尾，这道闸门会在「不同平台 / 不同 ``core.autocrlf`` 设置」下给出**不同答案**
——那是环境依赖的假信号，不是 schema 漂移。**闸门只能对 schema 漂移报警。**

⚠️ 必须用项目 venv 跑：schema 的 ``info.version`` 来自 ``pyproject.toml``（经 importlib 读取），
系统 python 会读成 ``dev``，造成**假漂移**。``--check`` 失败信息里会打出
``info.version`` 与当前解释器路径，便于一眼区分「真漂移」与「解释器不对」。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.api.main import create_app  # noqa: E402
from packages.core.config import Settings  # noqa: E402
from scripts._codegen_check import (  # noqa: E402
    RepoRootNotFound,
    check_artifact,
    default_openapi_path,
)


def _build_schema(data_dir: Path) -> dict:
    """构造 ``app.openapi()``（不触发 lifespan：迁移 / 自愈无需跑）。"""
    settings = Settings(data_dir=data_dir, log_level="ERROR")
    # 不触发 lifespan（迁移/自愈无需跑）；仅需要路由表与 schema。
    return create_app(settings).openapi()


def build_schema(data_dir: Path | None = None) -> dict:
    """构造 ``app.openapi()``（不写盘、不触发 lifespan）。

    ⚠️ 临时 data 目录**必用完即删**（2026-10-23 批次三十一，3-2）：旧实现
    ``Path(tempfile.mkdtemp(prefix="novelos_openapi_"))`` **从不清理**
    ⇒ 实测系统临时目录已积 **208** 个空目录、每次 ``--check`` +1
    ⇒ 「``--check`` 绝不写盘」**字面为假**（准确表述是「绝不写**产物**盘」）。
    清理用 ``ignore_errors=True`` ⇒ **清理失败不得影响退出码**。
    """
    if data_dir is None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="novelos_openapi_"))
        try:
            return _build_schema(tmp_dir)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    return _build_schema(data_dir)


def render(schema: dict) -> str:
    """把 schema 渲染成产物文本（写盘与校验共用，保证两条路径逐字同源）。"""
    return json.dumps(schema, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出 / 校验 OpenAPI schema JSON")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="输出路径（默认 <仓库根>/apps/web/openapi.json；**相对仓库根**，与 CWD 无关）",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="临时 data 目录（默认系统临时目录；避免触碰真实 data/novelos.db）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验漂移、不写盘：一致 → 0，漂移或缺失 → 非 0",
    )
    args = parser.parse_args(argv)

    # 默认路径**先**解析：解析不了要给出**明确文案**，而不是含糊的 MISSING 假漂移。
    if args.out is None:
        try:
            out_path = default_openapi_path()
        except RepoRootNotFound as exc:
            print(
                f"[ABORT] 找不到仓库根（脚本位置推导：{exc.root}）"
                "—— 无法解析默认产物路径。\n"
                "        请显式传 --out <产物路径>。",
                file=sys.stderr,
            )
            return 2
    else:
        out_path = args.out

    schema = build_schema(args.data_dir)
    rendered = render(schema)

    if args.check:
        ok, msg = check_artifact(out_path, rendered)
        version = (schema.get("info") or {}).get("version")
        print(
            f"{msg}\n"
            f"    (openapi info.version={version!r}, interpreter={sys.executable})",
            file=sys.stdout if ok else sys.stderr,
        )
        return 0 if ok else 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    n_paths = len(schema.get("paths", {}))
    n_schemas = len(schema.get("components", {}).get("schemas", {}))
    print(f"openapi.json written: {out_path} ({n_paths} paths, {n_schemas} schemas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
