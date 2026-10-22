# -*- coding: utf-8 -*-
"""genre 域少量「契约面」承接用例（2026-10-23 批次二十六 B 路 R-1 配套）。

为什么需要这个文件
------------------
审计 B（N-7）实测：genre 域 8 个 .py 中只有 ``models.py`` / ``verifier.py`` 有突变规格，
其余 6 个零覆盖。补 ``l18_genre_modules.py`` 时按「撤修复/改值必红」逐一找承接用例，
其中三处**没有**现成承接面，本文件补齐（其余突变直接复用既有用例）：

1. ``model.GenrePackPayload`` 的 ``extra="allow"`` —— 「未知键原样保留」是
   curation 侧兼容契约（``model.py`` docstring），但此前无任何用例钉住；
2. ``model.parse_payload`` 的读侧容错 —— 非法 payload 必须退空视图而**不抛**；
3. ``service.validate_payload`` 的错误串前缀 ``"[schema] "`` —— 对外 API 契约
   （与 skill / canon 校验器同款），此前只有「含 schema_version 字样」的弱断言。

零真实 LLM、零内容仓接触、零 dev 库（纯内存 payload）。
"""

from __future__ import annotations

from packages.core.genre.model import (
    GENRE_PACK_SCHEMA_VERSION,
    parse_payload,
)
from packages.core.genre.service import validate_payload


def test_parse_payload_preserves_unknown_keys() -> None:
    """``extra="allow"``：未知顶层键必须**原样保留**（curation 的 stale/source 标注等）。

    撤掉 ``GenrePackPayload.model_config = ConfigDict(extra="allow")``（改 ``ignore``）
    ⇒ 本断言必红——l18 的 ``GM-MODEL-EXTRA-1`` 即此突变。
    """
    view = parse_payload(
        {
            "schema_version": GENRE_PACK_SCHEMA_VERSION,
            "payoff_types": [],
            "curation_note": {"stale": False, "owner": "editor"},
        }
    )
    dumped = view.model_dump()
    assert dumped["curation_note"] == {"stale": False, "owner": "editor"}, (
        "未知键被静默裁掉了 ⇒ extra='allow' 丢失"
    )


def test_parse_payload_tolerates_malformed_payload() -> None:
    """读侧容错：字段类型非法 / 非 JSON 串 / None ⇒ 退空视图，**不抛**。

    ``parse_payload`` 不是校验入口（严格校验在 ``service.validate_payload``）；
    装配路径依赖它「坏数据不炸」。撤掉 ``except Exception`` 兜底 ⇒ 本用例必红。
    """
    assert parse_payload({"payoff_types": "打脸"}).payoff_types == []
    assert parse_payload("not-json-at-all").schema_version == GENRE_PACK_SCHEMA_VERSION
    assert parse_payload(None).payoff_types == []


def test_validate_payload_error_string_contract() -> None:
    """错误串形态契约：``"[schema] <path>: <message>"``（对外 API 暴露的排障格式）。

    与 ``tests/unit/test_skill_schema.py`` 同款口径（skill / canon / genre 三处一致）。
    前缀被改（如 ``[genre]``）⇒ 本用例必红——l18 的 ``GM-SERVICE-ERR-1`` 即此突变。
    """
    errs = validate_payload({"schema_version": "genre-pack.v2.0.0"})
    assert errs, "错误版本线必须报错"
    assert all(e.startswith("[schema] ") for e in errs), f"错误串前缀漂移：{errs}"
    root_errs = validate_payload(["not", "a", "dict"])
    assert root_errs and root_errs[0].startswith("[schema] <root>:"), root_errs
