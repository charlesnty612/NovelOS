"""CLI：执行 SQLite 迁移并打印结果。

用法::

    python scripts/migrate.py            # 迁移 NOVELOS_DATA_DIR / NOVELOS_DB_PATH 指向的库
    python scripts/migrate.py --help     # 只打印用法

V3.9 检修：本脚本原先完全不看 ``argv``——``--help`` / 拼错的参数都会照常迁移
（对运行库执行 DDL 的破坏半径太大）。现在用 argparse 显式拒绝未知参数：
``--help`` 打印用法并 exit 0，未知参数打印 usage + error 并 exit 2，**两者都不迁移**。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本可直接运行（python scripts/migrate.py）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.config import get_settings  # noqa: E402
from packages.core.db import apply_migrations  # noqa: E402
from packages.core.logging_config import configure_logging  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器（本命令无位置参数；仅保留 -h/--help）。"""
    return argparse.ArgumentParser(
        prog="migrate",
        description=(
            "对运行库执行 SQLite 迁移（按 _migrations 记录幂等）。"
            "目标库由 NOVELOS_DATA_DIR / NOVELOS_DB_PATH 环境变量决定。"
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # 未知参数 → argparse 打印 usage + error 到 stderr 并 exit 2（不触碰 DB）
    parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    settings.ensure_data_dir()
    result = apply_migrations(settings.db_path)
    print(
        f"applied={len(result['applied'])} skipped={len(result['skipped'])} "
        f"tables={result['tables']} db={settings.db_path}"
    )
    if result["applied"]:
        print("newly applied:")
        for n in result["applied"]:
            print(f"  - {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
