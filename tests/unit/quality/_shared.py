"""tests/unit/quality 包共用测试 helper（2026-10-23 task-6 阶段一 · 复制粘贴收敛）。

本模块里的每个函数都来自**本包内多个测试文件里逐字相同**的副本 ⇒ 搬过来
**行为逐位不变**（函数名也不变 ⇒ 调用点一行未改）。

⚠️ 只收「自由变量可解析」的簇：函数体用到的名字要么是内建，要么由本模块
顶部同样的 import 绑定 ⇒ 否则宁可不搬（不能凭空造依赖）。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.config import Settings
from packages.core.db import apply_migrations


def _make_db(tmp_path: Path) -> str:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return str(settings.db_path)
