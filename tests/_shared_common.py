"""tests 跨套共用测试 helper 的**单点定义处**（2026-10-24 · 跨套逐字副本上移）。

⚠️ 背景：全仓 5 个同名 `_shared.py`（tests/api、tests/integration、tests/unit、
tests/unit/quality、tests/workflow）里，有 8 个定义是**跨套逐字副本**
（逐块 SHA256 校验：同一哈希组，无任何差异）：

====================  ==========================================
定义                  出现套
====================  ==========================================
``_create_app``       api / integration / workflow
``_make_client``      api / integration / workflow
``_evidence``         api / integration / unit
``_make_meta``        api / integration
``_run``              api / integration
====================  ==========================================

⇒ 本模块是它们的**唯一定义处**；各套 `_shared.py` 改为
``from tests._shared_common import ...`` **原样再导出**（函数名不变 ⇒
**消费方零改动**，96 处 ``from ._shared import ...`` 一行未动）。

⚠️ 只收「**跨套逐字一致**」的簇：任何逐字校验有差异的版本（含注释 / 空行 /
返回值注解 / 默认值）一律**留在原套**，不在此处静默统一。

⚠️ **域特有 helper 不在本模块**（各自留原地，本模块一概不碰）：

- ``tests/unit/_shared.py``：``_insert_project`` / ``_insert_draft`` /
  ``_insert_chapter`` / ``_make_settings`` / ``_db``
- ``tests/api/_shared.py``：``_request``
- ``tests/workflow/_shared.py``：``_get_run_via_http`` /
  ``_observer_noop_script`` / ``_make_engine``
- ``tests/unit/quality/_shared.py``：``_make_db``（转发 ``migrated_db``）

⚠️ 各测试文件里的**私有同名副本**（如 ``tests/api/test_chapter_drafts.py`` 的
``_make_client``、``tests/api/test_chapter_reopen.py`` 的 ``_insert_chapter``）
**不在本次收敛范围**：它们多数与本模块版本**不同**（如缺返回值注解、默认
``status`` 相反、且**不跑** ``apply_migrations``）⇒ 保留原地。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)

def _make_client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")

def _make_meta(delta_id: str, chapter_id: str, prev_version: int) -> dict:
    """构造 observer state-delta 负载。

    ⚠️ ``created_at`` 固定为 **2026-08-23**：与
    ``tests/unit/snapshot_rebuild_helpers.py`` 私有版的 **2026-08-24**
    **日期有意错开**（排序测试用）⇒ 同名收敛时须**显式参数化**日期，
    **禁止静默统一**（两者本就不是逐字副本，是有意差异）。
    """
    return {
        "delta_id": delta_id,
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": chapter_id,
        "workflow_run_id": f"wfr_{delta_id}",
        "previous_state_version": prev_version,
        "created_by": "observer:v1",
        "created_at": "2026-08-23T10:00:00+00:00",
        "supersedes": None,
        "notes": None,
    }

def _evidence(chapter_id: str) -> dict:
    return {"chapter_id": chapter_id, "scene_id": None, "excerpt": "excerpt", "span": None}

def _run(coro):
    return asyncio.run(coro)
