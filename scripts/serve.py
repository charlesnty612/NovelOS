"""CLI：启动 uvicorn 服务（Sprint 0）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from packages.core.config import get_settings  # noqa: E402


def _ensure_provider_key() -> None:
    """把通用 ``MINIMAX_API_KEY`` 映射到 provider 期望的 ``NOVELOS_API_KEY_OPENAI_COMPATIBLE``。

    provider 只认 ``NOVELOS_API_KEY_<PROVIDER大写>``（providers.resolve_api_key），
    而部署/脚本侧惯用 ``MINIMAX_API_KEY``。缺映射时服务 401 秒失败（实测 ch064
    plan 0.4s FAILED）。仅当目标变量未设置时注入，不覆盖显式配置；密钥只走
    进程环境，不落盘。
    """
    target = "NOVELOS_API_KEY_OPENAI_COMPATIBLE"
    if not os.environ.get(target):
        minimax = os.environ.get("MINIMAX_API_KEY")
        if minimax:
            os.environ[target] = minimax


def main() -> int:
    _ensure_provider_key()
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
