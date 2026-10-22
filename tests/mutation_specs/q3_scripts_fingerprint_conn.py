"""装配指纹的连接复用：装饰器**不注入 conn** ⇒ 建连断言必红（dev-b 批次二十三）。

缺陷形状
--------
「传参了但没用」是本仓最难发现的形状之一：签名里多了 ``conn``、调用点也传了，
但**函数内部没真用** ⇒ 装配时的建连次数**纹丝不动** ⇒ 热路径优化等于没做，
而**没有任何断言会响**（功能全对、只是慢）。

本条突变模拟它：让 :func:`packages.core.context_engine.cache.with_fingerprint_conn`
**不再注入** ``_conn`` ⇒ 装配器的五个指纹退回「各自自建连」⇒ 建连次数从 ≤2
弹回 5~6 ⇒ ``tests/unit/test_ctx_b_fingerprint_conn_reuse.py`` 的建连断言必红。

突变
----
* **FP-CONN-1** 把 ``return fn(*args, _conn=conn, **kwargs)`` 改成
  ``return fn(*args, **kwargs)`` ⇒ 连接**不被注入** ⇒ 指纹各自自建连。

⚠️ **replacement 不破坏 import、不改变任何指纹值**（口径完全不变，只是连接数变了）
⇒ 红的**原因**是「建连次数断言失效」，正是本条要守的机制。

判别
----
``tests/unit/test_ctx_b_fingerprint_conn_reuse.py``（3 例：writer ≤2 条 / director ≤2 条 /
**负对照**：不传 conn 时六个指纹各开一条 ⇒ 证明计数器是灵的）。
"""

MUTATIONS = [
    (
        "FP-CONN-1",
        "packages/core/context_engine/cache.py",
        "            return fn(*args, _conn=conn, **kwargs)\n",
        "            return fn(*args, **kwargs)  # MUTATION-FP-CONN: 不注入 conn\n",
        "tests/unit/test_ctx_b_fingerprint_conn_reuse.py",
    ),
]
