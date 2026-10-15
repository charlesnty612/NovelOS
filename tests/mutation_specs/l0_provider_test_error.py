"""`/test` 端点错误映射修复的突变规格（2026-10-15 批次（六）· 主会话自办）。

缺陷：``ModelRouter.get_provider`` 抛的 ``ValueError`` 落在 ``try`` 之外且只捕
``ProviderError`` ⇒ 未捕获 ⇒ **500 + text/plain**（非 JSON）。

三条：撤掉 ``except ValueError``（两处各一条）+ 把构造挪回 try 之外（还原原缺陷）。
"""

MUTATIONS = [
    (
        "L0-P1",
        "packages/core/api/routers/model_profiles.py",
        """    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"model_profile {profile_id!r} 的 provider 配置无效：{exc}",
        ) from exc""",
        "",
        "tests/api/test_provider_test_endpoint_error_mapping.py",
    ),
    (
        "L0-P2",
        "packages/core/api/routers/model_configs.py",
        """    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"model_config {config_id!r} 的 provider 配置无效：{exc}",
        ) from exc""",
        "",
        "tests/api/test_provider_test_endpoint_error_mapping.py",
    ),
]
