"""``get_connection`` 的 WAL 降级（2026-10-16 批次十 · Lead 自办）的突变规格。

两条，各钉一个失效方向：

* **WAL-1** 撤掉 ``try/except`` ⇒ 恢复裸 ``conn.execute("PRAGMA journal_mode = WAL")``
  ⇒ 锁竞争下 ``get_connection`` 抛 ``database is locked``，被
  ``_peek_chapter_context`` 的 ``except Exception -> 全空态`` 吞成脏缓存键。
* **WAL-2** **过度修正**：干脆把 ``PRAGMA journal_mode = WAL`` 整行删掉。
  这条与 WAL-1 方向相反——只钉 WAL-1 的话，「把出错的语句删了」这种看起来同样
  合理的修法能全绿（本仓登记的「修复动作只有一个」与「过修」两族）。

⚠️ 这组是对本批次**我自己写的修复**的判别，量具侧自查尤其要紧：
初版（2026-10-16）我把 ``busy_timeout`` 提前后，用例红在「读不到数据」上，
而真因是 rollback journal 下 ``BEGIN EXCLUSIVE`` 连 ``SELECT 1`` 都挡——
不是实现坏了，是夹具在错误的时机做断言。

⚠️ **登记一条「未做突变看守」的行为**：``busy_timeout`` 与 ``journal_mode`` 的
**先后顺序**是有意的（提前会让本句先白等 5 秒再抛，与 M1 事故「宁可立刻失败
也不要挂住」的教训相悖），源码里有注释钉住，但**没有突变覆盖**——
要判别它只能写时序断言，那必然脆。按纪律登记为声称，不写成已验。
"""

MUTATIONS = [
    (
        "WAL-1",
        "packages/core/db.py",
        '    try:\n        conn.execute("PRAGMA journal_mode = WAL")\n'
        "    except sqlite3.OperationalError:\n",
        '    conn.execute("PRAGMA journal_mode = WAL")  # WAL-MUTATION: 撤掉降级\n'
        "    if False:\n",
        "tests/unit/test_db_connection_wal_degradation.py",
    ),
    (
        "WAL-2",
        "packages/core/db.py",
        '    try:\n        conn.execute("PRAGMA journal_mode = WAL")\n'
        "    except sqlite3.OperationalError:\n"
        "        log.warning(\n"
        '            "切 WAL 失败，退回 rollback journal（并发降低，功能不受影响）：%s",\n'
        "            db_path,\n"
        "            exc_info=True,\n"
        "        )\n",
        "    # WAL-MUTATION: 过度修正——把整块删掉\n    pass\n",
        "tests/unit/test_db_connection_wal_degradation.py",
    ),
]
