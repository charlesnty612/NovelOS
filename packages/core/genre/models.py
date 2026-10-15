"""题材包 **API 出参** 契约模型（2026-10-15 批次（六）· G7）。

与同目录 :mod:`packages.core.genre.model`（**单数**）的分工——两个文件刻意分开：

- ``model.py``：**资源 / 入参**模型（``GenrePack`` / ``GenrePackSummary`` /
  ``GenrePackCreate`` / ``GenrePackUpdate`` / ``GenrePackPayload``）。它们同时被
  service、装配缓存、jsonschema 校验层消费，是**领域模型**。
- 本文件：**API 出参**模型。只描述 ``GET|POST /projects/{pid}/genre-pack``、
  ``.../genre-pack/bind``、``.../genre-pack/unbind`` 三条端点**实际吐出**的 JSON 键集，
  供 OpenAPI 声明与前端 codegen 使用。

⚠️ 为什么文件名只差一个字母还要分：批次任务书把本组新增路径钉死在这里。两者互不
import（本文件的 ``pack`` 直接复用 ``model.GenrePack``），无循环依赖，可随时合并。

字段形状经**实测**（临时库 + 合成夹具，见
``tests/api/test_genre_quality_reference_response_contract.py``），来源如下：

``GET|POST .../genre-pack`` 三条端点同构，都是
:func:`GenrePackService.get_project_binding` 的返回值：

- ``project_id`` —— 回显，恒在；
- ``pack_id`` —— 单 slot；未绑定时为 ``None``（**键仍在**，不是省略）；
- ``bound`` —— ``bool(pack_id)``；
- ``pack`` —— 题材包全文（``queries.pack_row_to_dict`` 的 9 键投影），未绑定时 ``None``。

⚠️ ``pack`` 复用 ``model.GenrePack`` 而**不是**复制一份：那份模型的字段集恰好等于
``genre_packs`` 表 8 列（``payload_json`` 改名为 ``payload``）+ 派生列
``bound_project_count``，是投影的单一事实源。复制一份就会得到「两份声明、只有一份被测」
的老形状。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

from .model import GenrePack

__all__ = ["GenrePackBinding"]


class GenrePackBinding(BaseModel):
    """项目 ↔ 题材包绑定（单 slot）的出参形状。

    ``extra="allow"``：绑定行将来若加派生字段（如 ``bound_at``），宁可透出也不被
    ``response_model`` 静默裁掉——「数据在库里、API 读不到」是本仓反复吃过的亏
    （见 ``AGENTS.md`` 坑区「算出来的问题没人能说『不』」同族的契约丢失事故）。

    ⚠️ 正因为开了 ``extra="allow"``，**只断言响应键集抓不到「字段声明被删」**
    （删了声明，响应照样有该键）。契约测试必须**另**断言 OpenAPI 里本模型的
    ``properties`` / ``required``（批次（五）突变 M20 实测存活的那一类）。
    """

    model_config = ConfigDict(extra="allow")

    project_id: str
    pack_id: Optional[str] = None
    bound: bool
    pack: Optional[GenrePack] = None
