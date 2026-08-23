"""CLI：执行 SQLite 迁移并打印结果。"""

from __future__ import annotations

import sys
from pathlib import Path

# 让脚本可直接运行（python scripts/migrate.py）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.config import get_settings  # noqa: E402
from packages.core.db import apply_migrations  # noqa: E402
from packages.core.logging_config import configure_logging  # noqa: E402


def main() -> int:
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
