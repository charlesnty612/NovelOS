"""CLI：启动 uvicorn 服务（Sprint 0）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from packages.core.config import get_settings  # noqa: E402


def main() -> int:
    settings = get_settings()
    uvicorn.run(
        "packages.core.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        log_level=settings.log_level.lower(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
