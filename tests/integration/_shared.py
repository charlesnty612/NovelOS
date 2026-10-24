"""tests/integration 包共用测试 helper（2026-10-23 task-6 阶段一 · 复制粘贴收敛）。

⚠️ 2026-10-24（跨套逐字副本上移）：本模块原有 5 个定义全部是**跨套逐字副本**
（`_create_app` / `_make_client` 与 api、workflow 三套逐字节一致；
`_make_meta` / `_run` 与 api 两套一致；`_evidence` 与 api、unit 三套一致
⇒ 逐块 SHA256 校验落在同一哈希组）⇒ 已上移到单点 `tests/_shared_common.py`，
本模块改为**纯再导出**（函数名不变 ⇒ 消费方 `from ._shared import ...` 一行未改）。

⚠️ 本套**无域特有 helper**：收敛后本模块不含任何自有定义。
"""

from __future__ import annotations

from tests._shared_common import _create_app as _create_app
from tests._shared_common import _evidence as _evidence
from tests._shared_common import _make_client as _make_client
from tests._shared_common import _make_meta as _make_meta
from tests._shared_common import _run as _run
