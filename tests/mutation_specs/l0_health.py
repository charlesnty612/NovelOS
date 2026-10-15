"""`GET /api/health` 的突变规格（2026-10-15 批次（六）· 主会话自办）。

主会话自己改的文件（``core/api/main.py``）同样要过突变闸门——
「例外」是纪律漏洞的第一个入口。

三条：撤 response_model / 少声明一个字段 / 给字段加默认值（必填变可省略）。
"""

MUTATIONS = [
    (
        "L0-1",
        "packages/core/api/main.py",
        '@app.get("/api/health", response_model=HealthResponse)',
        '@app.get("/api/health")',
        "tests/api/test_health_response_contract.py",
    ),
    (
        "L0-2",
        "packages/core/api/main.py",
        '    tables: int = Field(description="**业务表数**（已扣除 ``_migrations`` 虚表）")',
        "",
        "tests/api/test_health_response_contract.py",
    ),
    (
        "L0-3",
        "packages/core/api/main.py",
        '    version: str = Field(description="pyproject 单源版本，经 importlib 读取")',
        '    version: str = Field(default="dev", description="pyproject 单源版本，经 importlib 读取")',
        "tests/api/test_health_response_contract.py",
    ),
]
