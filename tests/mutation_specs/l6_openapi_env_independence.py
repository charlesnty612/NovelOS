"""活 OpenAPI 不得随「构建产物在不在」而变（2026-10-19，CI 红测根因）。

事故
----
``packages/core/api/main.py`` 的 SPA 托管分支随 ``apps/web/dist`` 是否存在而
**改变路由表**（``dist/`` 不在版本库 ⇒ 干净 clone / CI 必然缺）：

* dist 存在 → SPA 分支，catch-all 带 ``include_in_schema=False`` ⇒ 活 OpenAPI
  98 paths、**无** ``/``；
* dist 缺失 → ``else`` 分支的 ``@app.get("/")`` **默认进 schema** ⇒ 99 paths、
  **多出** ``/``。

入库的 ``apps/web/openapi.json`` 是在本地（带 dist）生成的 ⇒ 与 CI 侧活 schema
差集恰为 ``['/']`` ⇒ ``tests/api/test_openapi_contract.py`` 自 **2026-09-09** 起
**每次 push 恒红**，而本地全量闸门全绿（本地有 dist）⇒ **红了 30+ 次无人看红**。
e2e smoke job 另有一条同根因症状（``GET /`` 期望 ``text/html``，实得
``application/json``）。

修法
----
两条 ``include_in_schema=False`` 都钉住：root 端点（2026-10-19 新增）与 SPA
catch-all（原有）。⇒ 两种环境下活 OpenAPI 路径集**逐字相同**。

两条突变各钉一个失效方向
------------------------
* **ENV-1** 撤掉 root 端点的 ``include_in_schema=False`` ⇒ 无 dist 侧多出 ``/``
  ⇒ 两集合不等 ⇒ ``test_openapi_paths_identical_with_and_without_dist`` 必红。
* **ENV-2** 撤掉 SPA catch-all 的 ``include_in_schema=False`` ⇒ **有** dist 侧
  多出 ``/{full_path}`` ⇒ 同样两集合不等。

⚠️ **ENV-2 是必要的**：只钉 ENV-1 的话，「把 catch-all 也改成进 schema」这种
看起来同样合理的改法能全绿——本仓坑区登记过「修复动作只有一个 → 越修越坏」
与「过修」两族（批次十 `E1-ARC3` 即因过修被回退）。

通用纪律
--------
**契约产物的正确性不得依赖构建产物是否存在**——凡「同一份代码在两种环境下
产出不同路由表」的分支，其环境差异必须被 ``include_in_schema`` 排除在契约之外。

判别
----
``tests/api/test_spa_hosting.py::test_openapi_paths_identical_with_and_without_dist``
断言的是**不变式**（两环境路径集相等），不是「比对某个文件」——故它对**两个
方向**都敏感，且与 ``test_openapi_contract.py``（比对入库文件）**互不覆盖**：
后者只在「无 dist」环境（CI）红，前者在任一环境红。
"""

MUTATIONS = [
    (
        "ENV-1",
        "packages/core/api/main.py",
        '        @app.get("/", include_in_schema=False)\n',
        '        @app.get("/")  # MUTATION-ENV-1: 撤掉 include_in_schema\n',
        "tests/api/test_spa_hosting.py",
    ),
    (
        "ENV-2",
        "packages/core/api/main.py",
        '        @app.get("/{full_path:path}", include_in_schema=False)\n',
        '        @app.get("/{full_path:path}")  # MUTATION-ENV-2: 撤掉 include_in_schema\n',
        "tests/api/test_spa_hosting.py",
    ),
]
