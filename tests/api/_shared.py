"""tests/api 包共用测试 helper（2026-10-23 task-6 阶段一 · 复制粘贴收敛）。

本模块里的每个函数都来自**本包内多个测试文件里逐字相同**的副本 ⇒ 搬过来
**行为逐位不变**（函数名也不变 ⇒ 调用点一行未改）。

⚠️ 只收「自由变量可解析」的簇：函数体用到的名字要么是内建，要么由本模块
顶部同样的 import 绑定 ⇒ 否则宁可不搬（不能凭空造依赖）。

⚠️ 2026-10-24（跨套逐字副本上移）：`_create_app` / `_make_client` / `_make_meta` /
`_evidence` / `_run` 这 5 个定义是**跨套逐字副本**（与 integration / unit / workflow
各自 `_shared.py` 的同名定义逐字节一致 ⇒ 逐块 SHA256 校验落在同一哈希组）⇒ 已上移到
单点 `tests/_shared_common.py`，此处改为**再导出**（函数名不变 ⇒ 消费方
`from ._shared import ...` 一行未改）。
本套**域特有**的 `_request` 留在原地。
"""

from __future__ import annotations

import httpx

from tests._shared_common import _create_app as _create_app
from tests._shared_common import _evidence as _evidence
from tests._shared_common import _make_client as _make_client
from tests._shared_common import _make_meta as _make_meta
from tests._shared_common import _run as _run


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)
