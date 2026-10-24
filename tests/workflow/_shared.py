"""tests/workflow 包共用测试 helper（2026-10-23 task-6 阶段一 · 复制粘贴收敛）。

⚠️ 2026-10-24（跨套逐字副本上移）：`_create_app` / `_make_client` 是**跨套逐字副本**
（与 api、integration 两套的同名定义逐字节一致 ⇒ 逐块 SHA256 校验落在同一哈希组）
⇒ 已上移到单点 `tests/_shared_common.py`，本模块改为**再导出**（函数名不变 ⇒
消费方 `from ._shared import ...` 一行未改）。

⚠️ 本套**域特有**的 `_get_run_via_http` / `_observer_noop_script` / `_make_engine`
留在原地，本模块一概不碰。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.config import Settings
from packages.core.db import apply_migrations
from packages.core.workflow_runtime.engine import WorkflowEngine
from tests._shared_common import _create_app as _create_app
from tests._shared_common import _make_client as _make_client


async def _get_run_via_http(app, run_id: str) -> dict | None:
    import httpx
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        r = await client.get(f"/api/runs/{run_id}")
    finally:
        await client.aclose()
    if r.status_code == 404:
        return None
    return r.json()

def _observer_noop_script() -> list[str]:
    return [
        json.dumps(
            {
                "schema_version": "observer.v1",
                "prompt_version": "observer:v1",
                "chapter_id": "ch_xxx",
                "narrative_observations": [],
                "continuity_observations": [],
                "world_state_observations": [],
                "relationship_observations": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
                "world_changes": [],
                "relationship_changes": [],
            },
            ensure_ascii=False,
        )
    ]

def _make_engine(tmp_path: Path) -> WorkflowEngine:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return WorkflowEngine(settings.db_path)
